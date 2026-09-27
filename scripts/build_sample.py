#!/usr/bin/env python3
"""Builds data/sample/ from data/raw/: three Status CSVs, Kelmarsh 1, 2 and 6,
restricted to a hand-picked set of real 2016 time windows.

The windows were chosen (see docs/plan.md Task 1) by inspecting the real data for
messages that start 3 or more times within 10 minutes on one turbine (chattering),
Stops lasting over 24 hours (long stops), and a window where the whole farm crosses
the "more than 10 non-informational events within 10 minutes" flood threshold. That
threshold needs contributions from more than 2 turbines during the 2016-03-01 grid
event: Kelmarsh 1 and 6 alone reach only 8 non-informational events in any single
10-minute window there, so Kelmarsh 2's real events for that same hour are included
too (it was the smallest addition that pushes a window over 10; Kelmarsh 4 would
also have worked). Run this after scripts/fetch_kelmarsh.py has populated
data/raw/. Prints what it kept.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
SAMPLE_DIR = ROOT / "data" / "sample"

TS_FORMAT = "%Y-%m-%d %H:%M:%S"

# (label, window start, window end); window end is exclusive.
WINDOWS: dict[int, list[tuple[str, str, str]]] = {
    1: [
        ("emergency stop, resolution, 2nd long stop", "2016-01-14 00:00:00", "2016-01-25 00:00:00"),
        ("System OK / Comm. failure FPM chattering", "2016-02-19 11:30:00", "2016-02-19 13:00:00"),
        ("farm-wide flood window (grid event)", "2016-03-01 17:00:00", "2016-03-01 18:00:00"),
        ("Brake accumulator defect chattering", "2016-04-05 16:00:00", "2016-04-05 18:00:00"),
        ("Brake accumulator defect chattering", "2016-04-06 06:30:00", "2016-04-06 08:00:00"),
        ("Manual stop long stop + chattering", "2016-04-07 05:00:00", "2016-04-07 16:30:00"),
        ("Grid loss long stop", "2016-04-21 00:00:00", "2016-04-22 00:00:00"),
        ("Feedback brake 1 long stop + chattering", "2016-04-25 14:00:00", "2016-04-25 17:30:00"),
        ("Data communication unavailable", "2016-05-10 12:00:00", "2016-05-10 13:00:00"),
        ("Data communication unavailable", "2016-06-01 03:00:00", "2016-06-01 04:00:00"),
        ("Data communication unavailable", "2016-07-01 15:00:00", "2016-07-01 16:00:00"),
        ("Data communication unavailable", "2016-08-17 00:30:00", "2016-08-17 01:30:00"),
        ("Data communication unavailable", "2016-09-01 07:00:00", "2016-09-01 08:30:00"),
        ("Park master stop long stop", "2016-10-31 06:00:00", "2016-10-31 10:00:00"),
    ],
    2: [
        ("farm-wide flood window (grid event), tips it over the flood threshold", "2016-03-01 17:00:00", "2016-03-01 18:00:00"),
    ],
    6: [
        ("Safety chain open long stop", "2016-02-07 08:00:00", "2016-02-07 14:00:00"),
        ("Grid loss long stop + System OK chattering", "2016-02-22 00:00:00", "2016-02-26 00:00:00"),
        ("farm-wide flood window (grid event)", "2016-03-01 17:00:00", "2016-03-01 18:00:00"),
        ("Frequency converter not ready chattering", "2016-04-22 10:00:00", "2016-04-22 12:00:00"),
        ("Overload generator fan 1 chattering", "2016-05-05 11:00:00", "2016-05-05 13:00:00"),
        ("System OK chattering", "2016-05-25 16:30:00", "2016-05-25 18:30:00"),
        ("Maximum grid frequency long stop + comm event", "2016-07-29 03:00:00", "2016-07-29 07:00:00"),
        ("Park master stop long stop", "2016-10-31 06:00:00", "2016-10-31 10:00:00"),
        ("Data communication unavailable", "2016-05-10 12:00:00", "2016-05-10 13:00:00"),
        ("Data communication unavailable", "2016-06-01 03:00:00", "2016-06-01 04:00:00"),
        ("Data communication unavailable", "2016-08-17 00:30:00", "2016-08-17 01:30:00"),
        ("Data communication unavailable", "2016-09-01 07:00:00", "2016-09-01 08:30:00"),
    ],
}


def _in_any_window(start: datetime, windows: list[tuple[str, str, str]]) -> bool:
    return any(
        datetime.strptime(a, TS_FORMAT) <= start < datetime.strptime(b, TS_FORMAT) for _, a, b in windows
    )


def build(turbine_number: int) -> tuple[int, dict[str, int]]:
    [source] = sorted(RAW_DIR.glob(f"Status_Kelmarsh_{turbine_number}_*.csv"))
    lines = source.read_text(encoding="utf-8").splitlines(keepends=True)
    header_idx = next(i for i, line in enumerate(lines) if not line.startswith("#"))
    comment_lines = lines[:header_idx]
    rows = list(csv.DictReader(lines[header_idx:]))
    windows = WINDOWS[turbine_number]

    kept = [row for row in rows if _in_any_window(datetime.strptime(row["Timestamp start"], TS_FORMAT), windows)]
    status_counts: dict[str, int] = {}
    for row in kept:
        status_counts[row["Status"]] = status_counts.get(row["Status"], 0) + 1

    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    dest = SAMPLE_DIR / f"Status_Kelmarsh_{turbine_number}_sample.csv"
    fieldnames = list(rows[0].keys())
    with open(dest, "w", newline="", encoding="utf-8") as f:
        f.writelines(comment_lines)
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept)

    return len(kept), status_counts


if __name__ == "__main__":
    total = 0
    for n in (1, 2, 6):
        count, status_counts = build(n)
        total += count
        print(f"Kelmarsh {n}: kept {count} events across {len(WINDOWS[n])} windows -> {status_counts}")
        for label, a, b in WINDOWS[n]:
            print(f"    {a} .. {b}  {label}")
    print(f"total sample events: {total}")
