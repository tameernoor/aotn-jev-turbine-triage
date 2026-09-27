"""CLI entry point.

    uv run --env-file .env python -m jev_turbine run [--data DIR] [--out DIR] [--sample]

Loads every Status CSV in --data (default data/raw), triages each event against Jev's
judgments, and writes out/judgments.json (a cache of Jev's answers per distinct
(status, message) pair, persisted and reused between runs so a pair already asked is
never asked again), out/triage.jsonl (one line per event) and out/evaluation.json (see
evaluate.py), then prints a short summary. `--sample` points --data at the committed
data/sample/ folder instead, for readers without the full Zenodo download.

Needs TYPESAFE_API_KEY in the environment for a real run; nothing here reads .env
itself (`uv run --env-file .env` does that). Tests pass a fake `ask` straight to
`main()`/`run()` instead, so the test suite never needs a key or a network call.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from .evaluate import evaluate
from .jev import AskFn, Jev
from .loader import load_events
from .triage import ACT_NOW, Cache, TriageResult, triage

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / "data" / "raw"
SAMPLE_DATA_DIR = REPO_ROOT / "data" / "sample"
DEFAULT_OUT_DIR = REPO_ROOT / "out"

TOP_ACT_NOW_MESSAGES = 5


class _UsageTracker:
    """Wraps an AskFn and records every JevResult.meta, so the summary can report Jev
    calls, tokens and cost without changing triage.py's signature."""

    def __init__(self, ask: AskFn):
        self._ask = ask
        self.calls: list[dict] = []

    async def __call__(self, state: dict, questions: dict[str, dict]):
        result = await self._ask(state, questions)
        self.calls.append(result.meta)
        return result


def _resolve_data_dir(data_arg: str | None, sample: bool) -> Path:
    if data_arg is not None:
        return Path(data_arg)
    return SAMPLE_DATA_DIR if sample else DEFAULT_DATA_DIR


def _load_cache(path: Path) -> Cache:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_cache(path: Path, cache: Cache) -> None:
    path.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


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
    }


def _write_triage_jsonl(path: Path, results: Sequence[TriageResult]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for result in results:
            f.write(json.dumps(_triage_row(result), ensure_ascii=False) + "\n")


def _summarise(results: Sequence[TriageResult], evaluation: dict, usage_calls: list[dict], wall_seconds: float) -> dict:
    counts = Counter(r.triage for r in results)
    act_now_messages = Counter(r.message for r in results if r.triage == ACT_NOW)
    return {
        "counts": dict(counts),
        "top_act_now_messages": act_now_messages.most_common(TOP_ACT_NOW_MESSAGES),
        "accuracy_by_event": evaluation["accuracy_by_event"],
        "accuracy_by_message": evaluation["accuracy_by_message"],
        "jev_calls": len(usage_calls),
        "input_tokens": sum(c["input_tokens"] for c in usage_calls),
        "cost_usd": sum(c["cost_usd"] for c in usage_calls),
        "wall_seconds": wall_seconds,
    }


def _fmt_accuracy(acc: dict) -> str:
    if acc["total"] == 0:
        return "no events evaluated"
    return f"{acc['correct']}/{acc['total']} ({acc['accuracy']:.0%})"


def _format_summary(summary: dict) -> str:
    lines = ["Triage counts:"]
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
    lines.append(
        f"Jev calls: {summary['jev_calls']}, input tokens: {summary['input_tokens']}, "
        f"cost: ${summary['cost_usd']:.6f}, wall time: {summary['wall_seconds']:.2f}s"
    )
    return "\n".join(lines)


async def run(data_dir: Path, out_dir: Path, ask: AskFn | None = None) -> dict:
    """Run the full pipeline once: load, triage (asking Jev only for pairs not already
    in out/judgments.json), write the three output files, print and return the
    summary. If `ask` is None, builds a real Jev() (needs TYPESAFE_API_KEY) and closes
    it afterwards; a test passes a fake ask instead."""
    jev: Jev | None = None
    if ask is None:
        jev = Jev()
        ask = jev.ask
    tracker = _UsageTracker(ask)
    try:
        events = load_events(data_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        cache_path = out_dir / "judgments.json"
        cache = _load_cache(cache_path)

        started = time.perf_counter()
        results = await triage(events, tracker, cache)
        wall_seconds = time.perf_counter() - started

        _save_cache(cache_path, cache)
        _write_triage_jsonl(out_dir / "triage.jsonl", results)

        evaluation = evaluate(events, cache)
        (out_dir / "evaluation.json").write_text(
            json.dumps(evaluation, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        summary = _summarise(results, evaluation, tracker.calls, wall_seconds)
        print(_format_summary(summary))
        return summary
    finally:
        if jev is not None:
            await jev.aclose()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m jev_turbine", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Load events, ask Jev, triage and evaluate.")
    run_parser.add_argument("--data", metavar="DIR", default=None, help=f"folder of Status CSVs (default: {DEFAULT_DATA_DIR})")
    run_parser.add_argument("--out", metavar="DIR", default=None, help=f"output folder (default: {DEFAULT_OUT_DIR})")
    run_parser.add_argument("--sample", action="store_true", help="use the committed data/sample/ folder instead of --data")

    return parser


def main(argv: Sequence[str] | None = None, ask: AskFn | None = None) -> None:
    args = _build_parser().parse_args(argv)
    if args.command == "run":
        data_dir = _resolve_data_dir(args.data, args.sample)
        out_dir = Path(args.out) if args.out is not None else DEFAULT_OUT_DIR
        asyncio.run(run(data_dir, out_dir, ask=ask))


if __name__ == "__main__":
    main()
