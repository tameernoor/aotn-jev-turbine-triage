"""Triage the event stream: Jev answers three questions per distinct (status, message)
pair, plain code decides act_now / monitor / no_action from the cached judgments plus
the code checks in checks.py. See docs/plan.md's "Questions" and "Rules" sections.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from .checks import chattering, floods, long_stops
from .jev import AskFn
from .judgments import Judgments
from .models import Event

QUESTIONS_PATH = Path(__file__).resolve().parents[2] / "questions" / "event.yaml"

ACT_NOW = "act_now"
MONITOR = "monitor"
NO_ACTION = "no_action"

INFORMATIONAL = "Informational"
WARNING = "Warning"

FAULT = "fault"
RUNNING = "running"

# cache[status][message] holds the raw judgments dict Jev returned for that pair (the
# same shape as JevResult.judgments): one entry per distinct (status, message) asked.
# Pass a dict in (even {}) to have it filled in place, so it can be persisted and used
# to pre-seed a later run without asking Jev again for pairs already answered.
Cache = dict[str, dict[str, dict[str, dict]]]


def load_questions(path: Path = QUESTIONS_PATH) -> dict[str, dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@dataclass(frozen=True, slots=True)
class TriageResult:
    turbine: str
    start: datetime
    end: datetime | None
    duration_seconds: float | None
    status: str
    message: str
    triage: str
    reasons: list[str]
    chattering: bool
    flood: bool


def apply_rules(j: Judgments, status: str) -> tuple[str, list[str]]:
    """Rules 2-7 for a non-informational event, given a Judgments already wrapping the
    raw judgments for its (status, message) pair. Read order matters for the fan-out:
    cause first, then safety_related always, then needs_site_visit only when cause is
    fault. A confident safety_related yes or a confident fault-needing-a-site-visit
    wins even over an uncertain read elsewhere; the uncertainty check runs after both,
    so it still catches an uncertain needs_site_visit read on a fault."""
    cause = j.choice("cause")
    safety = j.yes("safety_related")

    if safety:
        return ACT_NOW, ["safety"]
    if cause == FAULT and j.yes("needs_site_visit"):
        return ACT_NOW, ["fault needing a site visit"]
    if j.uncertain:
        return MONITOR, [f"uncertain: {', '.join(j.uncertain)}"]
    if cause == FAULT:
        return MONITOR, ["fault, remote reset may clear it"]
    if cause == RUNNING and status == WARNING:
        return MONITOR, ["warning while running"]
    return NO_ACTION, [cause]


async def _judgments_for(event: Event, ask: AskFn, questions: dict[str, dict], cache: Cache) -> dict[str, dict]:
    by_message = cache.setdefault(event.status, {})
    cached = by_message.get(event.message)
    if cached is not None:
        return cached
    result = await ask({"status": event.status, "message": event.message}, questions)
    by_message[event.message] = result.judgments
    return result.judgments


async def triage(events: Sequence[Event], ask: AskFn, cache: Cache | None = None) -> list[TriageResult]:
    """Triage every event, in the order given. Jev is asked at most once per distinct
    (status, message) pair that is not Informational; `cache` (pass a dict, even {}, to
    have it filled in place) holds the answers so a caller can persist and reuse them."""
    if cache is None:
        cache = {}
    questions = load_questions()
    chattering_idx = chattering(events)
    flood_idx = floods(events)
    long_stop_idx = long_stops(events)

    results = []
    for i, event in enumerate(events):
        is_chattering = i in chattering_idx
        is_long_stop = i in long_stop_idx

        if event.status == INFORMATIONAL:
            if is_chattering:
                triage_class, reasons = MONITOR, ["chattering"]
            else:
                triage_class, reasons = NO_ACTION, ["informational"]
        else:
            raw = await _judgments_for(event, ask, questions, cache)
            triage_class, reasons = apply_rules(Judgments(raw), event.status)
            if is_chattering:
                reasons = [*reasons, "chattering"]

        if is_long_stop:
            if triage_class == NO_ACTION:
                triage_class = MONITOR
            reasons = [*reasons, "long stop"]

        results.append(
            TriageResult(
                turbine=event.turbine,
                start=event.start,
                end=event.end,
                duration_seconds=event.duration_seconds,
                status=event.status,
                message=event.message,
                triage=triage_class,
                reasons=reasons,
                chattering=is_chattering,
                flood=i in flood_idx,
            )
        )
    return results
