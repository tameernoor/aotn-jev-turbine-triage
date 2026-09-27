"""A sanity check against the full 2016 Turbine_Data CSVs, if data/raw/ happens to
hold them (as data/raw/kelmarsh.duckdb, built by fetch.fetch_kelmarsh, or as loose
CSVs this builds a fresh in-memory database from). data/raw/ is git-ignored and not
part of a fresh checkout, so this whole file skips when neither is present. When
present, it checks the loader's totals against the real 2016 data and the coverage
facts in docs/plan-v2-context.md's "Data" section.
"""

from pathlib import Path

import pytest

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


def test_full_2016_kelmarsh_1_column_coverage_matches_the_plan(con):
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
