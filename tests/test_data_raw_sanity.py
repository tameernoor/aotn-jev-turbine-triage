"""A sanity check against the full 2016 dataset, if data/raw/ happens to be populated.

data/raw/ is git-ignored and not part of a fresh checkout, so this whole file skips
when it is absent. When present (after running scripts/fetch_kelmarsh.py), it checks
the loader's totals, and the checks described in README.md's "Triage rules" section,
against the real 2016 data.
"""

from collections import Counter
from pathlib import Path

import pytest

from jev_turbine.checks import floods, long_stops
from jev_turbine.loader import load_events

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

pytestmark = pytest.mark.skipif(
    not list(RAW_DIR.glob("Status_*.csv")), reason="data/raw/ is not populated; run scripts/fetch_kelmarsh.py"
)


def test_full_2016_event_and_status_counts_match_2016():
    events = load_events(RAW_DIR)
    statuses = Counter(e.status for e in events)

    assert len(events) == 14019
    assert statuses["Informational"] == 12549
    assert statuses["Stop"] == 751
    assert statuses["Warning"] == 553
    assert statuses["Communication"] == 166


def test_full_2016_data_has_a_real_farm_wide_flood():
    events = load_events(RAW_DIR)

    assert len(floods(events)) > 0


def test_full_2016_data_has_24_long_stops():
    events = load_events(RAW_DIR)

    assert len(long_stops(events)) == 24
