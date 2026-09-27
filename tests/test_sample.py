"""Checks the committed data/sample/ has the properties data/sample/README.md describes."""

from pathlib import Path

from jev_turbine.checks import chattering, floods, long_stops
from jev_turbine.loader import load_events

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "sample"


def test_sample_dir_has_a_readme_with_attribution():
    text = (SAMPLE_DIR / "README.md").read_text(encoding="utf-8")

    assert "Cubico" in text
    assert "16807551" in text
    assert "CC-BY-4.0" in text
    assert "https://creativecommons.org/licenses/by/4.0/" in text


def test_sample_loads_and_covers_all_three_turbines():
    events = load_events(SAMPLE_DIR)
    turbines = {e.turbine for e in events}

    assert turbines == {"Kelmarsh 1", "Kelmarsh 2", "Kelmarsh 6"}
    assert 200 <= len(events) <= 400


def test_sample_includes_stops_warnings_informational_and_communication():
    events = load_events(SAMPLE_DIR)
    statuses = {e.status for e in events}

    assert statuses == {"Stop", "Warning", "Informational", "Communication"}


def test_sample_has_a_real_chattering_run():
    events = load_events(SAMPLE_DIR)

    assert len(chattering(events)) > 0


def test_sample_has_a_real_long_stop():
    events = load_events(SAMPLE_DIR)
    flagged = long_stops(events)

    assert len(flagged) > 0
    assert all(events[i].duration_seconds > 24 * 3600 for i in flagged)


def test_sample_has_a_real_flood_window():
    events = load_events(SAMPLE_DIR)
    flagged = floods(events)

    assert len(flagged) > 0
    # The 2016-03-01 grid event is the only window dense enough in this sample;
    # it should involve more than one turbine.
    assert len({events[i].turbine for i in flagged}) > 1
