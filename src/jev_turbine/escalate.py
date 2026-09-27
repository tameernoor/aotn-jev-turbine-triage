"""Step 2: the turbine's own production numbers, as plain code rules, for events step 1
left uncertain. No second Jev call: those numbers are facts code already has, not a
judgment call to weigh.

Selects events whose step-1 triage is `monitor` because the derived cause is
`triage.UNCLEAR`, other than because `names_safety_hazard` or `names_physical_damage`
was itself among the uncertain ids (see `_needs_escalation`): those always stay
`monitor`, untouched. For every selected event, one DuckDB query
(`context.measurement_stats`) fetches the turbine's own production numbers around the
event, and two rules decide the outcome (see `_apply_step2_rules`):

- kept producing: `after_power` known, >= 50 kW, and it never dropped below 50 kW
  (`low_power_recovered_seconds == 0`). Cause becomes `running`, triage `no_action`,
  reason "kept producing".
- stopped: `after_power` known and < 50 kW. Triage stays `monitor`, cause stays
  `unclear`, and the reason "stopped, cause unclear" replaces the uncertainty reason.
- anything else, including no data at all, is left exactly as step 1 had it.

The rendered context text (`context.render_contexts`) is written onto every selected
event's result, along with `step1_triage`/`step1_reasons`, whether or not the rules
above changed anything, so a person reading the monitor pile in `out/triage.jsonl` can
see the numbers step 2 looked at.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

import duckdb

from .context import LOW_POWER_THRESHOLD_KW, MeasurementStats, measurement_stats, render_contexts
from .models import Event
from .triage import MONITOR, NO_ACTION, PHYSICAL_DAMAGE, RUNNING, SAFETY_HAZARD, TriageResult, UNCLEAR

KEPT_PRODUCING = "kept producing"
STOPPED = "stopped, cause unclear"


def _uncertain_ids(reasons: Sequence[str]) -> list[str]:
    """The ids named in step 1's "uncertain: ..." reason (see
    triage.apply_step1_rules), if it has one; [] otherwise, including a plain
    "cause unclear" (three confident no's, nothing read was itself uncertain)."""
    for reason in reasons:
        if reason.startswith("uncertain: "):
            return reason.removeprefix("uncertain: ").split(", ")
    return []


def _needs_escalation(result: TriageResult) -> bool:
    """Step 1 left this event `monitor` with its cause `UNCLEAR`, and that was not
    because `names_safety_hazard` or `names_physical_damage` was itself among the
    uncertain ids: those two always stay `monitor`, whatever the cause chain did."""
    if result.triage != MONITOR or result.cause != UNCLEAR:
        return False
    uncertain_ids = _uncertain_ids(result.reasons)
    return SAFETY_HAZARD not in uncertain_ids and PHYSICAL_DAMAGE not in uncertain_ids


def _reapply_code_checks(step1_result: TriageResult, triage_class: str, reasons: list[str]) -> tuple[str, list[str]]:
    """Reapplies step 1's own chattering/long-stop reasons (and long stop's
    no_action -> monitor floor) to a fresh (triage, reasons) pair computed by
    step 2, the same way triage() applies them to step 1's own result."""
    if step1_result.chattering:
        reasons = [*reasons, "chattering"]
    if "long stop" in step1_result.reasons:  # the exact string triage()'s is_long_stop branch appends
        if triage_class == NO_ACTION:
            triage_class = MONITOR
        reasons = [*reasons, "long stop"]
    return triage_class, reasons


def _apply_step2_rules(step1_result: TriageResult, stats: MeasurementStats) -> tuple[str, list[str], str]:
    """(triage, reasons, cause) for one selected event, from its production numbers
    alone. Anything that is neither "kept producing" nor "stopped" (including no
    data at all) is left exactly as step 1 had it."""
    if (
        stats.after_power is not None
        and stats.after_power >= LOW_POWER_THRESHOLD_KW
        and stats.low_power_recovered_seconds == 0
    ):
        triage_class, reasons = _reapply_code_checks(step1_result, NO_ACTION, [KEPT_PRODUCING])
        return triage_class, reasons, RUNNING
    if stats.after_power is not None and stats.after_power < LOW_POWER_THRESHOLD_KW:
        triage_class, reasons = _reapply_code_checks(step1_result, MONITOR, [STOPPED])
        return triage_class, reasons, UNCLEAR
    return step1_result.triage, step1_result.reasons, step1_result.cause


def escalate(
    results: Sequence[TriageResult],
    events: Sequence[Event],
    all_events: Sequence[Event],
    con: duckdb.DuckDBPyConnection,
) -> list[TriageResult]:
    """Re-triage, with production numbers, every event step 1 left `monitor` on an
    unclear cause (see `_needs_escalation`).

    `results` is step 1's `triage()` output; `events` is the same events, in the same
    order, that produced it (results[i] is events[i]'s step-1 result). `all_events` is
    the full, all-turbine event list `context.render_contexts` computes event history
    from (typically the same list as `events`). `con` is an open DuckDB connection over
    the `measurements` table (`measurements.connect_for_read` or `connect_in_memory`).

    Returns a list the same length and order as `results`: a selected event's entry
    carries the rules' result (`triage`/`reasons`/`cause`, `step1_triage`/
    `step1_reasons` holding step 1's own read, `context` holding the rendered
    production-numbers text), whether or not the rules changed anything; every other
    event is returned unchanged. One DuckDB query covers every selected event.
    """
    to_escalate = [(i, event) for i, (result, event) in enumerate(zip(results, events)) if _needs_escalation(result)]
    out = list(results)
    if not to_escalate:
        return out

    escalated_events = [event for _, event in to_escalate]
    stats = measurement_stats(con, escalated_events)
    contexts = render_contexts(escalated_events, stats, all_events)

    for (i, _), context, event_stats in zip(to_escalate, contexts, stats):
        step1_result = results[i]
        triage_class, reasons, cause = _apply_step2_rules(step1_result, event_stats)
        out[i] = replace(
            step1_result,
            triage=triage_class,
            reasons=reasons,
            cause=cause,
            step1_triage=step1_result.triage,
            step1_reasons=step1_result.reasons,
            context=context,
        )
    return out
