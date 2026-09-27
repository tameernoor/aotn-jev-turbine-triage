import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from fakes import FakeJev

from jev_turbine import escalate
from jev_turbine.__main__ import DEFAULT_DATA_DIR, SAMPLE_DATA_DIR, _resolve_data_dir, main
from jev_turbine.loader import load_events
from jev_turbine.triage import QUESTIONS_PATH

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "sample"


class SlowRaisingAsk:
    """Like TwoPhaseFakeJev, but step 2 awaits a real asyncio.sleep before answering
    or raising, so many concurrent asks genuinely interleave (a synchronous fake
    would hide the bug this is meant to catch: escalate() used to gather without
    return_exceptions, so one raise propagated immediately and left every other
    still-sleeping ask's eventual answer unsaved). The first distinct step-2 state
    dispatched (attempts are counted synchronously, before any await, so this is
    deterministic) raises after a short sleep; every other one succeeds after a
    longer sleep, so it is still outstanding at the moment the first one raises."""

    def __init__(self, step1_values: dict, step2_values: dict, raise_delay: float = 0.001, normal_delay: float = 0.05):
        self.step1 = FakeJev(values=step1_values)
        self.step2 = FakeJev(values=step2_values)
        self._raise_delay = raise_delay
        self._normal_delay = normal_delay
        self._raise_attempt: int | None = None
        self.step2_attempts = 0

    async def __call__(self, state, questions):
        if "context" not in state:
            return await self.step1.ask(state, questions)
        self.step2_attempts += 1
        attempt = self.step2_attempts
        if self._raise_attempt is None:
            self._raise_attempt = attempt
        if attempt == self._raise_attempt:
            await asyncio.sleep(self._raise_delay)
            raise RuntimeError("simulated step-2 failure")
        await asyncio.sleep(self._normal_delay)
        return await self.step2.ask(state, questions)


class TwoPhaseFakeJev:
    """A stand-in for Jev whose answers depend on whether the state carries a
    `context` key. Step 1 (no context) is deliberately left uncertain, via a
    borderline names_safety_hazard noul, so every non-informational sample event
    escalates; step 2 (context present, still the old three-question set) answers
    confidently and differently, so escalation visibly changes the outcome. Kept as two
    separate FakeJevs so each phase's own call count and states are inspectable."""

    def __init__(self):
        self.step1 = FakeJev(values=dict(names_safety_hazard=0.5))
        self.step2 = FakeJev(values=dict(cause="planned", safety_related=0.1))

    async def ask(self, state, questions):
        fake = self.step2 if "context" in state else self.step1
        return await fake.ask(state, questions)


# --- data dir resolution, unit-tested directly so it never depends on data/raw existing ---


def test_resolve_data_dir_defaults_to_data_raw():
    assert _resolve_data_dir(None, sample=False) == DEFAULT_DATA_DIR


def test_resolve_data_dir_sample_flag_uses_data_sample():
    assert _resolve_data_dir(None, sample=True) == SAMPLE_DATA_DIR


def test_resolve_data_dir_explicit_data_wins_over_sample_flag():
    assert _resolve_data_dir("some/dir", sample=True) == Path("some/dir")


# --- end to end on data/sample/ with a fake Jev ---


def test_run_end_to_end_on_sample_writes_all_three_outputs(tmp_path, capsys):
    out_dir = tmp_path / "out"
    fake = FakeJev(values=dict(names_safety_hazard=0.1, names_physical_damage=0.1, names_routine=0.9, cause="planned", safety_related=0.1))

    main(["run", "--sample", "--out", str(out_dir)], ask=fake.ask)

    assert (out_dir / "judgments.json").exists()
    assert (out_dir / "triage.jsonl").exists()
    assert (out_dir / "evaluation.json").exists()
    assert (out_dir / "summary.json").exists()

    payload = json.loads((out_dir / "judgments.json").read_text(encoding="utf-8"))
    assert payload["questions_hash"] == hashlib.sha256(QUESTIONS_PATH.read_bytes()).hexdigest()
    assert payload["cache"]  # at least one distinct non-informational pair was cached

    lines = (out_dir / "triage.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(load_events(SAMPLE_DIR))
    row = json.loads(lines[0])
    assert set(row) == {
        "turbine",
        "start",
        "end",
        "duration_seconds",
        "status",
        "message",
        "triage",
        "reasons",
        "chattering",
        "flood",
        "step1_triage",
        "step1_reasons",
        "context",
        "step2_judgments",
    }
    assert "+00:00" in row["start"] or row["start"].endswith("Z")

    evaluation = json.loads((out_dir / "evaluation.json").read_text(encoding="utf-8"))
    assert "accuracy_by_event" in evaluation
    assert "iec_to_cause" in evaluation

    captured = capsys.readouterr()
    assert "act_now" in captured.out or "monitor" in captured.out or "no_action" in captured.out
    assert "Jev calls" in captured.out


def test_run_end_to_end_uses_duration_seconds_key_not_duration(tmp_path):
    out_dir = tmp_path / "out"
    fake = FakeJev(values=dict(names_safety_hazard=0.1, names_physical_damage=0.1, names_routine=0.9, cause="planned", safety_related=0.1))

    main(["run", "--sample", "--out", str(out_dir)], ask=fake.ask)

    lines = (out_dir / "triage.jsonl").read_text(encoding="utf-8").splitlines()
    for line in lines:
        row = json.loads(line)
        assert "duration_seconds" in row
        assert "duration" not in row or "duration_seconds" in row


def test_second_run_reuses_the_cache_and_makes_no_new_jev_calls(tmp_path):
    out_dir = tmp_path / "out"
    first = FakeJev(values=dict(names_safety_hazard=0.1, names_physical_damage=0.1, names_routine=0.9, cause="planned", safety_related=0.1))
    main(["run", "--sample", "--out", str(out_dir)], ask=first.ask)
    assert len(first.calls) > 0

    second = FakeJev(values=dict(names_safety_hazard=0.1, names_physical_damage=0.1, names_routine=0.9, cause="planned", safety_related=0.1))
    main(["run", "--sample", "--out", str(out_dir)], ask=second.ask)

    assert len(second.calls) == 0


def test_summary_reports_jev_usage_and_wall_time(tmp_path, capsys):
    out_dir = tmp_path / "out"
    fake = FakeJev(values=dict(names_safety_hazard=0.1, names_physical_damage=0.1, names_routine=0.9, cause="planned", safety_related=0.1))

    main(["run", "--sample", "--out", str(out_dir)], ask=fake.ask)

    captured = capsys.readouterr()
    assert "Jev calls" in captured.out
    assert "input tokens" in captured.out
    assert "cost" in captured.out
    assert "wall" in captured.out.lower()


def test_run_on_a_custom_data_dir(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    for path in SAMPLE_DIR.glob("Status_*.csv"):
        (data_dir / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    out_dir = tmp_path / "out"
    fake = FakeJev(values=dict(names_safety_hazard=0.1, names_physical_damage=0.1, names_routine=0.9, cause="planned", safety_related=0.1))

    # --no-context: this custom dir has no Turbine_Data CSVs, and a --data dir is
    # independent of --sample, so step 2 would otherwise try to open the persisted
    # data/raw/kelmarsh.duckdb, which a test must not depend on existing.
    main(["run", "--data", str(data_dir), "--out", str(out_dir), "--no-context"], ask=fake.ask)

    assert (out_dir / "triage.jsonl").exists()


# --- out/summary.json ---


def test_summary_json_holds_the_printed_fields_plus_the_model_ids_seen(tmp_path):
    out_dir = tmp_path / "out"
    fake = FakeJev(values=dict(names_safety_hazard=0.1, names_physical_damage=0.1, names_routine=0.9, cause="planned", safety_related=0.1))

    main(["run", "--sample", "--out", str(out_dir)], ask=fake.ask)

    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert set(summary) == {
        "counts",
        "top_act_now_messages",
        "accuracy_by_event",
        "accuracy_by_message",
        "accuracy_by_event_with_context",
        "jev_calls",
        "input_tokens",
        "cost_usd",
        "wall_seconds",
        "model_ids",
        "cache_ignored",
        "step2_jev_calls",
        "step2_input_tokens",
        "step2_cost_usd",
        "step2_wall_seconds",
        "step2_model_ids",
        "step2_cache_ignored",
    }
    assert summary["jev_calls"] == len(fake.calls)
    assert summary["model_ids"] == ["fake-jev"]
    assert summary["cache_ignored"] is False
    # This fixture's fixed answers (cause=planned, confident) never leave step 1
    # uncertain, so nothing is escalated: step 2 made no calls at all.
    assert summary["step2_jev_calls"] == 0
    assert summary["step2_cache_ignored"] is False


# --- Jev() is built lazily, only on an actual cache miss ---


def test_a_run_whose_cache_covers_every_pair_needs_no_key_and_builds_no_jev(tmp_path, monkeypatch):
    out_dir = tmp_path / "out"
    warm = FakeJev(values=dict(names_safety_hazard=0.1, names_physical_damage=0.1, names_routine=0.9, cause="planned", safety_related=0.1))
    main(["run", "--sample", "--out", str(out_dir)], ask=warm.ask)
    assert len(warm.calls) > 0

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    # ask=None: main must not build a real Jev() unless something is actually asked;
    # the warmed cache above already covers every pair in the sample. This would raise
    # (Jev() fails fast without a key) if construction were not lazy.
    main(["run", "--sample", "--out", str(out_dir)])


# --- cache guard: a stale questions_hash is ignored, not silently trusted ---


def test_a_cache_with_a_stale_questions_hash_is_ignored_and_reported(tmp_path, capsys):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "judgments.json").write_text(
        json.dumps({"questions_hash": "not-the-real-hash", "cache": {"Stop": {"whatever": {"cause": {"type": "choice", "value": "fault", "probabilities": {"fault": 1.0}, "confidence": 1.0}}}}}),
        encoding="utf-8",
    )
    fake = FakeJev(values=dict(names_safety_hazard=0.1, names_physical_damage=0.1, names_routine=0.9, cause="planned", safety_related=0.1))

    main(["run", "--sample", "--out", str(out_dir)], ask=fake.ask)

    assert len(fake.calls) > 0  # the stale cache was not used, so pairs were asked fresh
    captured = capsys.readouterr()
    assert "ignored" in captured.out.lower()
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["cache_ignored"] is True


# --- --cache FILE seeds the run from an existing cache without ever writing to it ---


def test_cache_flag_seeds_from_a_file_without_writing_back_to_it(tmp_path):
    out_dir = tmp_path / "out"
    warm_dir = tmp_path / "warm"
    warm = FakeJev(values=dict(names_safety_hazard=0.1, names_physical_damage=0.1, names_routine=0.9, cause="planned", safety_related=0.1))
    main(["run", "--sample", "--out", str(warm_dir)], ask=warm.ask)
    seed_path = warm_dir / "judgments.json"
    seed_before = seed_path.read_text(encoding="utf-8")

    second = FakeJev(values=dict(names_safety_hazard=0.1, names_physical_damage=0.1, names_routine=0.9, cause="planned", safety_related=0.1))
    main(["run", "--sample", "--out", str(out_dir), "--cache", str(seed_path)], ask=second.ask)

    assert len(second.calls) == 0  # every pair was already in the seed file
    assert (out_dir / "judgments.json").exists()
    assert seed_path.read_text(encoding="utf-8") == seed_before  # the seed file itself is untouched


# --- step 2 (context), end to end on --sample ---------------------------------------


def test_run_end_to_end_with_step2_on_sample(tmp_path):
    out_dir = tmp_path / "out"
    fake = TwoPhaseFakeJev()

    main(["run", "--sample", "--out", str(out_dir)], ask=fake.ask)

    assert len(fake.step1.calls) > 0
    assert len(fake.step2.calls) > 0  # step 1's uncertainty escalated at least one event

    context_cache_path = out_dir / "judgments-context.json"
    assert context_cache_path.exists()
    payload = json.loads(context_cache_path.read_text(encoding="utf-8"))
    assert payload["questions_hash"] == escalate.questions_hash()
    assert payload["cache"]

    rows = [json.loads(line) for line in (out_dir / "triage.jsonl").read_text(encoding="utf-8").splitlines()]
    escalated_rows = [r for r in rows if r["step1_triage"] is not None]
    assert escalated_rows  # contexts really were built from the sample's own 10-minute slices
    for row in escalated_rows:
        assert row["context"] is not None
        assert row["context"] != ""
        assert row["step1_reasons"] is not None
        assert row["step2_judgments"] is not None

    informational_rows = [r for r in rows if r["status"] == "Informational"]
    assert informational_rows
    for row in informational_rows:  # informational events are never escalated
        assert row["context"] is None
        assert row["step1_triage"] is None

    evaluation = json.loads((out_dir / "evaluation.json").read_text(encoding="utf-8"))
    assert "accuracy_by_event_with_context" in evaluation
    assert evaluation["escalated_with_iec_category"]["evaluated"] >= 0
    assert evaluation["triage_counts_before_context"]
    assert evaluation["triage_counts_after_context"]

    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["step2_jev_calls"] == len(fake.step2.calls)
    assert summary["step2_jev_calls"] > 0
    assert summary["step2_model_ids"] == ["fake-jev"]

    # A second run against the same out_dir reuses both caches and asks nothing new.
    second = TwoPhaseFakeJev()
    main(["run", "--sample", "--out", str(out_dir)], ask=second.ask)
    assert len(second.step1.calls) == 0
    assert len(second.step2.calls) == 0


# --- --no-context: no escalation, no DuckDB, context always null --------------------


def test_no_context_flag_skips_step2_entirely(tmp_path):
    out_dir = tmp_path / "out"
    fake = TwoPhaseFakeJev()

    main(["run", "--sample", "--out", str(out_dir), "--no-context"], ask=fake.ask)

    assert len(fake.step1.calls) > 0
    assert len(fake.step2.calls) == 0  # step 2 never runs at all
    assert not (out_dir / "judgments-context.json").exists()

    rows = [json.loads(line) for line in (out_dir / "triage.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows
    for row in rows:
        assert row["context"] is None
        assert row["step1_triage"] is None
        assert row["step1_reasons"] is None
        assert row["step2_judgments"] is None

    evaluation = json.loads((out_dir / "evaluation.json").read_text(encoding="utf-8"))
    assert evaluation["accuracy_by_event_with_context"] == evaluation["accuracy_by_event"]

    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["step2_jev_calls"] == 0
    assert summary["step2_input_tokens"] == 0
    assert summary["step2_cost_usd"] == 0
    assert summary["step2_model_ids"] == []


# --- step-2 cache is saved in a finally, so a mid-run failure loses nothing already answered ---


def test_step2_cache_is_saved_even_when_one_ask_raises(tmp_path):
    out_dir = tmp_path / "out"
    ask = SlowRaisingAsk(
        step1_values=dict(names_safety_hazard=0.5),
        step2_values=dict(names_safety_hazard=0.1, names_physical_damage=0.1, names_routine=0.9, cause="planned", safety_related=0.1),
    )

    with pytest.raises(RuntimeError):
        main(["run", "--sample", "--out", str(out_dir)], ask=ask)

    # Step 1's own cache is unaffected: it is saved before step 2 ever starts.
    assert (out_dir / "judgments.json").exists()

    context_cache_path = out_dir / "judgments-context.json"
    assert context_cache_path.exists()
    payload = json.loads(context_cache_path.read_text(encoding="utf-8"))
    assert payload["questions_hash"] == escalate.questions_hash()

    # More than one distinct state was actually in flight together (otherwise this
    # test would not be exercising concurrency at all), exactly one of them raised,
    # and every other one, still sleeping at the moment it raised, still made it
    # into the saved file: nothing already answered, or already in flight, is lost.
    assert ask.step2_attempts > 1
    assert len(ask.step2.calls) == ask.step2_attempts - 1
    assert len(payload["cache"]) == len(ask.step2.calls)

    # Step-2 failures otherwise stay fail-loud: no triage.jsonl/evaluation.json/
    # summary.json from an incomplete run.
    assert not (out_dir / "triage.jsonl").exists()
