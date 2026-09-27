"""Triage the event stream: Jev answers five literal yes/no questions per distinct
(status, message) pair (questions/event.yaml); code derives `cause` from three of them
and decides act_now / monitor / no_action from all five plus the code checks in
checks.py. See docs/plan-v3-literal.md's "Cause, derived in code" and "Triage rules
(step 1)" sections.

`apply_rules` is the old three-question rule set, kept only because escalate.py's step 2
still uses it (questions/event_with_context.yaml); `triage()` no longer calls it. Task 2
removes it.
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

# Cause values (see derive_cause below).
PLANNED = "planned"
EXTERNAL = "external"
FAULT = "fault"
RUNNING = "running"
UNCLEAR = "unclear"

# questions/event.yaml's five ids.
SAFETY_HAZARD = "names_safety_hazard"
PHYSICAL_DAMAGE = "names_physical_damage"
ROUTINE = "names_routine"
OUTSIDE_CONDITION = "names_outside_condition"
TURBINE_PROBLEM = "names_turbine_problem"

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
    # The cause step 1 derived in code (see derive_cause). None for an informational
    # event, which is never asked about and so never gets a cause at all.
    cause: str | None = None
    # Step 2 (escalate.py) fields. None for an event step 1 was not uncertain
    # about, since it was never escalated. When set, `triage`/`reasons` above
    # already hold the step-2 result; step 1's own triage and reasons are kept
    # here instead.
    step1_triage: str | None = None
    step1_reasons: list[str] | None = None
    context: str | None = None
    step2_judgments: dict[str, dict] | None = None


def derive_cause(j: Judgments, status: str) -> str:
    """The cause, read from names_routine, then names_outside_condition, then
    names_turbine_problem, stopping at the first confident yes. An uncertain read also
    stops the chain, returning UNCLEAR without reading the rest. Three confident no's
    give RUNNING when status is Warning, else UNCLEAR. Does not read
    names_safety_hazard or names_physical_damage; the caller reads those itself."""
    for qid, cause in ((ROUTINE, PLANNED), (OUTSIDE_CONDITION, EXTERNAL), (TURBINE_PROBLEM, FAULT)):
        if j.yes(qid):
            return cause
        if qid in j.uncertain:
            return UNCLEAR
    return RUNNING if status == WARNING else UNCLEAR


def apply_step1_rules(j: Judgments, status: str) -> tuple[str, list[str], str]:
    """Triage rules 2-7 for a non-informational event (rule 1, informational, is
    handled in triage() itself). Returns (triage, reasons, cause). Always reads
    names_safety_hazard, names_physical_damage and the cause chain, regardless of which
    rule decides, since evaluate.py scores every event's cause. A confident safety or
    damage yes wins even over an uncertain read elsewhere, since both are checked
    before the uncertainty check."""
    safety = j.yes(SAFETY_HAZARD)
    damage = j.yes(PHYSICAL_DAMAGE)
    cause = derive_cause(j, status)

    if safety:
        return ACT_NOW, ["safety"], cause
    if damage:
        return ACT_NOW, ["damaged part"], cause
    if j.uncertain:
        return MONITOR, [f"uncertain: {', '.join(j.uncertain)}"], cause
    if cause == UNCLEAR:
        return MONITOR, ["cause unclear"], cause
    if cause == FAULT:
        return MONITOR, ["fault, remote reset may clear it"], cause
    if cause == RUNNING:
        return MONITOR, ["warning while running"], cause
    return NO_ACTION, [cause], cause


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
            cause = None
            if is_chattering:
                triage_class, reasons = MONITOR, ["chattering"]
            else:
                triage_class, reasons = NO_ACTION, ["informational"]
        else:
            raw = await _judgments_for(event, ask, questions, cache)
            triage_class, reasons, cause = apply_step1_rules(Judgments(raw), event.status)
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
                cause=cause,
            )
        )
    return results
