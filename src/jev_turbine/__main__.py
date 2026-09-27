"""CLI entry point.

    uv run --env-file .env python -m jev_turbine run [--data DIR] [--out DIR] [--sample]
                                                       [--cache FILE] [--no-context]

Loads every Status CSV in --data (default data/raw), triages each event against Jev's
judgments (step 1), then, unless --no-context, turns every event step 1 left uncertain
into a code-only read of the turbine's own production numbers (step 2, see
escalate.py; no second Jev call). Writes out/judgments.json (step 1's cache of Jev's
answers per distinct (status, message) pair, persisted and reused between runs so a
pair already asked is never asked again), out/triage.jsonl (one line per event),
out/evaluation.json (see evaluate.py) and out/summary.json (the printed summary below,
as JSON), then prints that summary. `--sample` points --data at the committed
data/sample/ folder instead, for readers without the full Zenodo download, and also
points step 2 at an in-memory DuckDB built from data/sample's own 10-minute slices
instead of data/raw/kelmarsh.duckdb. `--cache FILE` seeds the run from an existing
judgments cache (for example results/judgments-2016.json) instead of
out/judgments.json, without ever writing back to FILE itself; the merged result
(FILE's answers plus anything newly asked) is still written to out/judgments.json as
usual.

`--no-context` skips step 2 entirely: no production numbers are read, no DuckDB is
opened at all (not even data/raw/kelmarsh.duckdb needs to exist).

out/judgments.json also stores a hash of questions/event.yaml. If that hash does not
match the questions this run is using, the cache is not trusted and is ignored instead
of silently serving answers to questions that have since changed wording; the run says
so, both on stdout and in out/summary.json.

Needs TYPESAFE_API_KEY in the environment for a real run, and only once a question is
actually asked: the real Jev() client is built lazily, on the first step-1 cache miss,
so a run whose cache already covers every pair needs no key and makes no network call
at all. Step 2 never calls Jev at all, so it never needs a key either way. Nothing here
reads .env itself (`uv run --env-file .env` does that). Tests pass a fake `ask` straight
to `main()`/`run()` instead, so the test suite never needs a key.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from .escalate import escalate
from .evaluate import evaluate
from .jev import AskFn, Jev, JevResult
from .loader import load_events
from .measurements import DB_FILENAME, build_database_from_dir, connect_for_read, connect_in_memory
from .triage import ACT_NOW, QUESTIONS_PATH, Cache, TriageResult, triage

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / "data" / "raw"
SAMPLE_DATA_DIR = REPO_ROOT / "data" / "sample"
DEFAULT_OUT_DIR = REPO_ROOT / "out"
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / DB_FILENAME

TOP_ACT_NOW_MESSAGES = 5


class _UsageTracker:
    """Wraps an AskFn and records every JevResult.meta, so the summary can report Jev
    calls, tokens, cost and the model ids seen, without changing triage.py's
    signature."""

    def __init__(self, ask: AskFn):
        self._ask = ask
        self.calls: list[dict] = []

    async def __call__(self, state: dict, questions: dict[str, dict]) -> JevResult:
        result = await self._ask(state, questions)
        self.calls.append(result.meta)
        return result


class _LazyJev:
    """Stands in for a real Jev() until the first pair actually needs asking. A run
    whose cache already covers every distinct pair never calls this, so it never
    builds a client and never needs TYPESAFE_API_KEY."""

    def __init__(self):
        self._jev: Jev | None = None

    async def __call__(self, state: dict, questions: dict[str, dict]) -> JevResult:
        if self._jev is None:
            self._jev = Jev()
        return await self._jev.ask(state, questions)

    async def aclose(self) -> None:
        if self._jev is not None:
            await self._jev.aclose()


def _resolve_data_dir(data_arg: str | None, sample: bool) -> Path:
    if data_arg is not None:
        return Path(data_arg)
    return SAMPLE_DATA_DIR if sample else DEFAULT_DATA_DIR


def _questions_hash() -> str:
    return hashlib.sha256(QUESTIONS_PATH.read_bytes()).hexdigest()


def _load_cache(path: Path) -> tuple[Cache, bool]:
    """Returns (cache, ignored). `ignored` is True only when `path` existed but its
    stored questions_hash did not match questions/event.yaml's current hash (or the
    file predates that field), so its answers were not trusted or used. A `path` that
    does not exist at all is just an empty starting cache, not an ignored one."""
    if not path.exists():
        return {}, False
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("questions_hash") != _questions_hash():
        return {}, True
    return payload.get("cache", {}), False


def _save_cache(path: Path, cache: Cache) -> None:
    payload = {"questions_hash": _questions_hash(), "cache": cache}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _triage_row(result: TriageResult) -> dict:
    return {
        "turbine": result.turbine,
        "start": result.start.isoformat(),
        "end": result.end.isoformat() if result.end is not None else None,
        "duration_seconds": result.duration_seconds,
        "status": result.status,
        "message": result.message,
        "triage": result.triage,
        "reasons": result.reasons,
        "chattering": result.chattering,
        "flood": result.flood,
        "cause": result.cause,
        # Step 2 fields; None for an event never escalated (including every run with
        # --no-context, since none is ever escalated then).
        "step1_triage": result.step1_triage,
        "step1_reasons": result.step1_reasons,
        "context": result.context,
    }


def _write_triage_jsonl(path: Path, results: Sequence[TriageResult]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for result in results:
            f.write(json.dumps(_triage_row(result), ensure_ascii=False) + "\n")


def _summarise(
    results: Sequence[TriageResult],
    evaluation: dict,
    usage_calls: list[dict],
    wall_seconds: float,
    cache_ignored: bool,
) -> dict:
    counts = Counter(r.triage for r in results)
    act_now_messages = Counter(r.message for r in results if r.triage == ACT_NOW)
    model_ids = sorted({c["model"] for c in usage_calls if c.get("model")})
    return {
        "counts": dict(counts),
        "top_act_now_messages": act_now_messages.most_common(TOP_ACT_NOW_MESSAGES),
        "accuracy_by_event": evaluation["accuracy_by_event"],
        "accuracy_by_message": evaluation["accuracy_by_message"],
        "accuracy_by_event_with_context": evaluation.get("accuracy_by_event_with_context"),
        "jev_calls": len(usage_calls),
        "input_tokens": sum(c["input_tokens"] for c in usage_calls),
        "cost_usd": sum(c["cost_usd"] for c in usage_calls),
        "wall_seconds": wall_seconds,
        "model_ids": model_ids,
        "cache_ignored": cache_ignored,
    }


def _fmt_accuracy(acc: dict) -> str:
    if acc["total"] == 0:
        return "no events evaluated"
    return f"{acc['correct']}/{acc['total']} ({acc['accuracy']:.0%})"


def _format_summary(summary: dict) -> str:
    lines = []
    if summary["cache_ignored"]:
        lines.append(
            "Cache ignored: questions/event.yaml does not match the hash stored with "
            "the cache, so every pair was asked fresh."
        )
    lines.append("Triage counts:")
    for cls in ("act_now", "monitor", "no_action"):
        lines.append(f"  {cls}: {summary['counts'].get(cls, 0)}")

    lines.append("Top act_now messages:")
    if summary["top_act_now_messages"]:
        for message, count in summary["top_act_now_messages"]:
            lines.append(f"  {message}: {count}")
    else:
        lines.append("  none")

    lines.append(
        "Evaluation accuracy: "
        f"{_fmt_accuracy(summary['accuracy_by_event'])} by event, "
        f"{_fmt_accuracy(summary['accuracy_by_message'])} by distinct message"
    )
    if summary.get("accuracy_by_event_with_context"):
        lines.append(
            "Evaluation accuracy with step 2: "
            f"{_fmt_accuracy(summary['accuracy_by_event_with_context'])} by event"
        )
    lines.append(
        f"Jev calls: {summary['jev_calls']}, input tokens: {summary['input_tokens']}, "
        f"cost: ${summary['cost_usd']:.6f}, wall time: {summary['wall_seconds']:.2f}s"
    )
    if summary["model_ids"]:
        lines.append(f"Model(s): {', '.join(summary['model_ids'])}")
    return "\n".join(lines)


async def run(
    data_dir: Path,
    out_dir: Path,
    ask: AskFn | None = None,
    seed_cache: Path | None = None,
    sample: bool = False,
    no_context: bool = False,
) -> dict:
    """Run the full pipeline once: load, triage (step 1, asking Jev only for pairs not
    already in the cache), then, unless `no_context`, escalate (step 2: a code-only
    read of the turbine's own production numbers for every event step 1 left
    uncertain, no Jev call), write the output files, print and return the summary.

    If `ask` is None, a single real Jev() is built lazily, the first time step 1
    actually needs to ask something (needs TYPESAFE_API_KEY), and closed once step 1
    is done; a run whose cache already covers every pair needs no key and builds no
    client at all. Step 2 never calls Jev, with or without a key.

    `seed_cache`, if given, is read instead of out_dir/judgments.json as the starting
    cache; the merged result is still written to out_dir/judgments.json, never back to
    the seed file.

    `sample` points step 2 at an in-memory DuckDB built from data/sample's own
    10-minute slices, matching `--sample`'s effect on `data_dir`, instead of the
    persisted data/raw/kelmarsh.duckdb. `no_context` skips step 2 entirely: no
    production numbers are read and no DuckDB is opened at all, not even to check it
    exists."""
    lazy_jev: _LazyJev | None = None
    if ask is None:
        lazy_jev = _LazyJev()
        ask = lazy_jev
    step1_tracker = _UsageTracker(ask)
    con = None
    try:
        events = load_events(data_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        cache_path = out_dir / "judgments.json"
        load_path = seed_cache if seed_cache is not None else cache_path
        cache, cache_ignored = _load_cache(load_path)

        started = time.perf_counter()
        step1_results = await triage(events, step1_tracker, cache)
        wall_seconds = time.perf_counter() - started

        _save_cache(cache_path, cache)

        results: Sequence[TriageResult] = step1_results

        if not no_context:
            if sample:
                con = connect_in_memory()
                build_database_from_dir(con, SAMPLE_DATA_DIR)
            else:
                con = connect_for_read(DEFAULT_DB_PATH)
            results = escalate(step1_results, events, events, con)

        _write_triage_jsonl(out_dir / "triage.jsonl", results)

        evaluation = evaluate(events, cache, results)
        (out_dir / "evaluation.json").write_text(
            json.dumps(evaluation, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        summary = _summarise(results, evaluation, step1_tracker.calls, wall_seconds, cache_ignored)
        (out_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        print(_format_summary(summary))
        return summary
    finally:
        if con is not None:
            con.close()
        if lazy_jev is not None:
            await lazy_jev.aclose()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m jev_turbine", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Load events, ask Jev, triage and evaluate.")
    run_parser.add_argument("--data", metavar="DIR", default=None, help=f"folder of Status CSVs (default: {DEFAULT_DATA_DIR}); step 2's production numbers still come from data/raw/kelmarsh.duckdb unless --sample")
    run_parser.add_argument("--out", metavar="DIR", default=None, help=f"output folder (default: {DEFAULT_OUT_DIR})")
    run_parser.add_argument("--sample", action="store_true", help="use the committed data/sample/ folder instead of --data, and its 10-minute slices instead of data/raw/kelmarsh.duckdb")
    run_parser.add_argument("--cache", metavar="FILE", default=None, help="seed the step-1 judgments cache from FILE instead of out/judgments.json")
    run_parser.add_argument("--no-context", action="store_true", help="skip step 2 (production numbers): no escalation, no DuckDB opened at all")

    return parser


def main(argv: Sequence[str] | None = None, ask: AskFn | None = None) -> None:
    args = _build_parser().parse_args(argv)
    if args.command == "run":
        data_dir = _resolve_data_dir(args.data, args.sample)
        out_dir = Path(args.out) if args.out is not None else DEFAULT_OUT_DIR
        seed_cache = Path(args.cache) if args.cache is not None else None
        asyncio.run(
            run(
                data_dir,
                out_dir,
                ask=ask,
                seed_cache=seed_cache,
                sample=args.sample,
                no_context=args.no_context,
            )
        )


if __name__ == "__main__":
    main()
