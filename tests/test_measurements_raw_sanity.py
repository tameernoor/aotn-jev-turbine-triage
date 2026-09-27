"""A sanity check against the full 2016 Turbine_Data CSVs, if data/raw/ happens to
hold them (as data/raw/kelmarsh.duckdb, built by fetch.fetch_kelmarsh, or as loose
CSVs this builds a fresh in-memory database from). data/raw/ is git-ignored and not
part of a fresh checkout, so this whole file skips when neither is present. When
present, it checks the loader's totals against the real 2016 data and the known
2016 coverage for Kelmarsh 1.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from jev_turbine.context import build_context
from jev_turbine.loader import load_events
from jev_turbine.measurements import DB_FILENAME, build_database_from_dir, connect, connect_in_memory

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
DB_PATH = RAW_DIR / DB_FILENAME

pytestmark = pytest.mark.skipif(
    not DB_PATH.exists() and not list(RAW_DIR.glob("Turbine_Data_Kelmarsh_*.csv")),
    reason="data/raw/ has no Turbine_Data CSVs or kelmarsh.duckdb; run scripts/fetch_kelmarsh.py",
)


@pytest.fixture(scope="module")
def con():
    if DB_PATH.exists():
        connection = connect(DB_PATH)
    else:
        connection = connect_in_memory()
        build_database_from_dir(connection, RAW_DIR)
    yield connection
    connection.close()


def test_full_2016_kelmarsh_1_has_one_row_per_ten_minutes(con):
    [(count,)] = con.execute("SELECT COUNT(*) FROM measurements WHERE turbine = 1").fetchall()
    assert count == 52416


def test_full_2016_kelmarsh_1_column_coverage_matches_2016(con):
    [row] = con.execute(
        """
        SELECT
            COUNT(power_kw), COUNT(wind_ms), COUNT(grid_hz), COUNT(grid_v), COUNT(rotor_rpm)
        FROM measurements WHERE turbine = 1
        """
    ).fetchall()
    power, wind, freq, volt, rotor = row

    assert power == 48485
    assert wind == 48485
    assert freq == 48485
    assert volt == 48485
    assert rotor == 34302


@pytest.fixture(scope="module")
def events():
    return load_events(RAW_DIR)


def _find(events, turbine, start, message):
    return next(e for e in events if e.turbine == turbine and e.start == start and e.message == message)


def test_kelmarsh_3_manual_stop_remote_reports_a_data_gap_not_24_hours(con, events):
    # Real gap: one known reading shortly after the start, then no more data for
    # days. Previously wrongly rendered "stayed below 50 kW for 24 h".
    event = _find(
        events,
        "Kelmarsh 3",
        datetime(2016, 2, 22, 11, 25, 30, tzinfo=timezone.utc),
        "Manual stop - remote",
    )

    text = build_context(event, con, events)

    assert "power stayed below 50 kW for at least 10 min, then no power data." in text
    assert "24 h" not in text


def test_kelmarsh_2_externally_stopped_reports_a_data_gap_not_24_hours(con, events):
    event = _find(
        events,
        "Kelmarsh 2",
        datetime(2016, 4, 21, 14, 25, 46, tzinfo=timezone.utc),
        "Externally stopped",
    )

    text = build_context(event, con, events)

    assert "power stayed below 50 kW for at least 10 min, then no power data." in text
    assert "24 h" not in text


def test_kelmarsh_2_farm_wide_flood_event_counts_one_other_turbine(con, events):
    # Real case: Kelmarsh 1 logs two qualifying Stops in the +/-10 minute window
    # (17:33:14 and 17:37:14), but that is one turbine, not two.
    event = _find(
        events,
        "Kelmarsh 2",
        datetime(2016, 3, 1, 17, 30, 3, tzinfo=timezone.utc),
        "Frequency converter not ready",
    )

    text = build_context(event, con, events)

    assert "Other turbines stopped in the same 10 minutes: yes (1)." in text
