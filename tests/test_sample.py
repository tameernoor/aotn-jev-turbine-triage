"""Checks the committed data/sample/ has the properties data/sample/README.md describes."""

from pathlib import Path

from jev_turbine.checks import chattering, floods, long_stops
from jev_turbine.context import build_context
from jev_turbine.loader import load_events
from jev_turbine.measurements import FIELD_NAMES, build_database_from_dir, connect_in_memory

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


def test_sample_measurements_load_into_duckdb_and_cover_all_three_turbines():
    con = connect_in_memory()
    build_database_from_dir(con, SAMPLE_DIR)

    turbines = {row[0] for row in con.execute("SELECT DISTINCT turbine FROM measurements").fetchall()}
    assert turbines == {1, 2, 6}
    for n in (1, 2, 6):
        [(count,)] = con.execute("SELECT COUNT(*) FROM measurements WHERE turbine = ?", [n]).fetchall()
        assert count > 0
    # Only the five allowed columns exist at all, structurally (see test_measurements.py).
    columns = {row[1] for row in con.execute("PRAGMA table_info('measurements')").fetchall()}
    assert columns == {"turbine", "ts", *FIELD_NAMES.values()}


def test_sample_measurements_are_under_the_one_megabyte_target():
    for n in (1, 2, 6):
        [path] = SAMPLE_DIR.glob(f"Turbine_Data_Kelmarsh_{n}_sample.csv")
        assert path.stat().st_size < 1_000_000


def test_sample_measurements_build_real_context_for_a_sample_event():
    events = load_events(SAMPLE_DIR)
    con = connect_in_memory()
    build_database_from_dir(con, SAMPLE_DIR)
    stop = next(e for e in events if e.turbine == "Kelmarsh 1" and e.status == "Stop")

    context = build_context(stop, con, events)

    assert context
    assert "previous 7 days" in context
