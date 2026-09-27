"""Loads Kelmarsh 10-minute Turbine_Data CSVs into per-turbine measurement rows.

Each file starts with `#` comment lines (including `# Turbine: <name>` and `# Time
zone: UTC`), then the column header line itself starts with `# Date and time,...`.
Column names contain commas (for example `"Wind speed, Standard deviation (m/s)"`),
so the header line is parsed with the `csv` module after stripping the leading
`# `, never by splitting on commas by hand. Turbine_Data files carry several hundred
columns (Lost Production, Availability, IEC, Contractual, Curtailment, Energy
Budget, Potential power, Capacity, Data Availability and more); only ALLOWED_COLUMNS
is ever kept, so an answer-key or operator-classification column can never reach a
Jev prompt through this loader. Missing values are the literal string "NaN" in the
source; they become None.

Timestamps are the END of each 10-minute period, UTC (a row stamped T covers
(T-10min, T]). The source timestamps are naive but the header states UTC, so this
loader attaches UTC directly, the same as loader.py does for the Status CSVs.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
TIMESTAMP_COLUMN = "Date and time"
HEADER_PREFIX = f"# {TIMESTAMP_COLUMN}"

# The only 10-minute columns the context builder (context.py) may ever see. Every
# other column in a Turbine_Data CSV is the operator's own classification or
# production-loss accounting and must never be loaded.
ALLOWED_COLUMNS: tuple[str, ...] = (
    "Power (kW)",
    "Wind speed (m/s)",
    "Rotor speed (RPM)",
    "Grid frequency (Hz)",
    "Grid voltage (V)",
)


class LoadError(RuntimeError):
    """A Turbine_Data CSV did not match the expected shape."""


class MeasurementRow(NamedTuple):
    """One 10-minute row: its end timestamp and the allowed columns only."""

    timestamp: datetime
    values: dict[str, float | None]


def load_measurements(folder: Path) -> dict[int, list[MeasurementRow]]:
    """Load every Turbine_Data_Kelmarsh_<n>_*.csv file in `folder`, keyed by turbine
    number, each turbine's rows sorted by timestamp."""
    by_turbine: dict[int, list[MeasurementRow]] = {}
    for path in sorted(Path(folder).glob("Turbine_Data_Kelmarsh_*.csv")):
        number, rows = _load_file(path)
        by_turbine.setdefault(number, []).extend(rows)
    for rows in by_turbine.values():
        rows.sort(key=lambda r: r.timestamp)
    return by_turbine


def _load_file(path: Path) -> tuple[int, list[MeasurementRow]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    turbine_number = _turbine_number(lines, path)
    header_idx = next((i for i, line in enumerate(lines) if line.startswith(HEADER_PREFIX)), None)
    if header_idx is None:
        raise LoadError(f"{path}: no '{HEADER_PREFIX}' header line")
    header = next(csv.reader([lines[header_idx][2:]]))
    reader = csv.DictReader(lines[header_idx + 1 :], fieldnames=header)
    rows = [_load_row(row, path) for row in reader]
    return turbine_number, rows


def _turbine_number(lines: list[str], path: Path) -> int:
    for line in lines:
        if line.startswith("# Turbine:"):
            name = line.split(":", 1)[1].strip()
            digits = "".join(ch for ch in name if ch.isdigit())
            if digits:
                return int(digits)
            break
    raise LoadError(f"{path}: no '# Turbine:' header line naming a turbine number")


def _load_row(row: dict[str, str], path: Path) -> MeasurementRow:
    try:
        timestamp = datetime.strptime(row[TIMESTAMP_COLUMN], TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except (KeyError, ValueError) as exc:
        raise LoadError(f"{path}: bad row {row!r}") from exc
    values = {column: _parse_float(row.get(column)) for column in ALLOWED_COLUMNS}
    return MeasurementRow(timestamp=timestamp, values=values)


def _parse_float(value: str | None) -> float | None:
    if value is None or value == "" or value == "NaN":
        return None
    return float(value)
