"""Loads Kelmarsh Status CSVs into Event records.

Each file starts with `#` comment lines (including `# Turbine: <name>`), then a header
row `Timestamp start,Timestamp end,Duration,Status,Code,Message,Comment,Service
contract category,IEC category`. Durations look like `211:08:29` (hours can exceed
24). `Timestamp end` and `Duration` are `-` for events with no recorded end.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path

from .models import Event

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


class LoadError(RuntimeError):
    """A Status CSV did not match the expected shape."""


def load_events(folder: Path) -> list[Event]:
    """Load every Status_*.csv file in `folder`, sorted by start time."""
    events: list[Event] = []
    for path in sorted(Path(folder).glob("Status_*.csv")):
        events.extend(_load_file(path))
    events.sort(key=lambda e: e.start)
    return events


def _load_file(path: Path) -> list[Event]:
    lines = path.read_text(encoding="utf-8").splitlines()
    turbine = _turbine_name(lines, path)
    body = [line for line in lines if not line.startswith("#")]
    reader = csv.DictReader(body)
    return [_load_row(turbine, row, path) for row in reader]


def _turbine_name(lines: list[str], path: Path) -> str:
    for line in lines:
        if line.startswith("# Turbine:"):
            return line.split(":", 1)[1].strip()
    raise LoadError(f"{path}: no '# Turbine:' header line")


def _load_row(turbine: str, row: dict[str, str], path: Path) -> Event:
    try:
        return Event(
            turbine=turbine,
            start=datetime.strptime(row["Timestamp start"], TIMESTAMP_FORMAT),
            end=_parse_timestamp(row["Timestamp end"]),
            duration_seconds=_parse_duration(row["Duration"]),
            status=row["Status"],
            code=row["Code"],
            message=row["Message"],
            iec_category=row["IEC category"] or None,
        )
    except (KeyError, ValueError) as exc:
        raise LoadError(f"{path}: bad row {row!r}") from exc


def _parse_timestamp(value: str) -> datetime | None:
    return None if value == "-" else datetime.strptime(value, TIMESTAMP_FORMAT)


def _parse_duration(value: str) -> float | None:
    if value == "-":
        return None
    hours, minutes, seconds = (int(part) for part in value.split(":"))
    return timedelta(hours=hours, minutes=minutes, seconds=seconds).total_seconds()
