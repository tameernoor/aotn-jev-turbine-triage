"""Event: one row from a Kelmarsh Status CSV, the unit code checks and Jev work on."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Event:
    turbine: str
    start: datetime
    end: datetime | None
    duration_seconds: float | None
    status: str
    code: str
    message: str
    iec_category: str | None
