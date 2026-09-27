import json
from pathlib import Path

from fakes import FakeJev

from jev_turbine.__main__ import DEFAULT_DATA_DIR, SAMPLE_DATA_DIR, _resolve_data_dir, main
from jev_turbine.loader import load_events

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "sample"


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
    fake = FakeJev(values=dict(cause="planned", safety_related=0.1))

    main(["run", "--sample", "--out", str(out_dir)], ask=fake.ask)

    assert (out_dir / "judgments.json").exists()
    assert (out_dir / "triage.jsonl").exists()
    assert (out_dir / "evaluation.json").exists()

    cache = json.loads((out_dir / "judgments.json").read_text(encoding="utf-8"))
    assert cache  # at least one distinct non-informational pair was cached

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
    fake = FakeJev(values=dict(cause="planned", safety_related=0.1))

    main(["run", "--sample", "--out", str(out_dir)], ask=fake.ask)

    lines = (out_dir / "triage.jsonl").read_text(encoding="utf-8").splitlines()
    for line in lines:
        row = json.loads(line)
        assert "duration_seconds" in row
        assert "duration" not in row or "duration_seconds" in row


def test_second_run_reuses_the_cache_and_makes_no_new_jev_calls(tmp_path):
    out_dir = tmp_path / "out"
    first = FakeJev(values=dict(cause="planned", safety_related=0.1))
    main(["run", "--sample", "--out", str(out_dir)], ask=first.ask)
    assert len(first.calls) > 0

    second = FakeJev(values=dict(cause="planned", safety_related=0.1))
    main(["run", "--sample", "--out", str(out_dir)], ask=second.ask)

    assert len(second.calls) == 0


def test_summary_reports_jev_usage_and_wall_time(tmp_path, capsys):
    out_dir = tmp_path / "out"
    fake = FakeJev(values=dict(cause="planned", safety_related=0.1))

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
    fake = FakeJev(values=dict(cause="planned", safety_related=0.1))

    main(["run", "--data", str(data_dir), "--out", str(out_dir)], ask=fake.ask)

    assert (out_dir / "triage.jsonl").exists()
