"""A sanity check against the full 2016 Turbine_Data CSVs, if data/raw/ happens to
hold them. data/raw/ is git-ignored and not part of a fresh checkout, so this whole
file skips when they are absent. When present (after running
scripts/fetch_kelmarsh.py, which now also extracts Turbine_Data), it checks the
loader's totals against the real 2016 data and the coverage facts in
docs/plan-v2-context.md's "Data" section.
"""

from pathlib import Path

import pytest

from jev_turbine.measurements import ALLOWED_COLUMNS, load_measurements

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

pytestmark = pytest.mark.skipif(
    not list(RAW_DIR.glob("Turbine_Data_Kelmarsh_*.csv")),
    reason="data/raw/ has no Turbine_Data CSVs; run scripts/fetch_kelmarsh.py",
)


@pytest.fixture(scope="module")
def kelmarsh_1_rows():
    return load_measurements(RAW_DIR)[1]


def test_full_2016_kelmarsh_1_has_one_row_per_ten_minutes(kelmarsh_1_rows):
    assert len(kelmarsh_1_rows) == 52416


def test_full_2016_kelmarsh_1_column_coverage_matches_the_plan(kelmarsh_1_rows):
    coverage = {
        column: sum(1 for row in kelmarsh_1_rows if row.values[column] is not None)
        for column in ALLOWED_COLUMNS
    }

    assert coverage["Power (kW)"] == 48485
    assert coverage["Wind speed (m/s)"] == 48485
    assert coverage["Grid frequency (Hz)"] == 48485
    assert coverage["Grid voltage (V)"] == 48485
    assert coverage["Rotor speed (RPM)"] == 34302
