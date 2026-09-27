"""Step 2: re-ask Jev with SCADA context for events step 1 left uncertain.

Selects the events whose step-1 triage is `monitor` with an "uncertain: ..."
reason (see triage.apply_rules); never an informational, chattering-only,
long-stop-only or warning-while-running event, since none of those ever
carries that reason. Builds every selected event's context in one
context.build_contexts call, asks Jev again with the step-2 questions
(questions/event_with_context.yaml) with up to `limit` requests in flight at
once, then re-applies triage.apply_rules to the step-2 judgments. The event's
final triage is the step-2 result; step 1's own triage and reasons are kept on
the result (step1_triage, step1_reasons), the rendered context is kept too,
and the code-check reasons baked into step 1's reasons (chattering, long stop)
are reapplied to the step-2 reasons, the same way triage() applies them to
step 1's. `flood` and `chattering` are plain fields on TriageResult, carried
over unchanged.

The step-2 cache (EscalationCache) is keyed per event, not per (status,
message) pair like step 1's: the context is specific to one event's own
10-minute data, not shared across events with the same message.
load_cache/save_cache persist it with a hash of
questions/event_with_context.yaml, the same guard __main__.py uses for the
step-1 judgments.json cache; Task 3's CLI wires them in.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import duckdb

from .context import build_contexts
from .jev import AskFn
from .judgments import Judgments
from .models import Event
from .triage import MONITOR, NO_ACTION, TriageResult, apply_rules, load_questions

QUESTIONS_PATH = Path(__file__).resolve().parents[2] / "questions" / "event_with_context.yaml"

# cache[f"{turbine}|{start.isoformat()}"] holds the raw judgments dict Jev
# returned for that event's step-2 questions (the same shape as
# JevResult.judgments): one entry per event actually escalated and asked. Pass
# a dict in (even {}) to have it filled in place, so it can be persisted and
# reused without asking Jev again for that event.
EscalationCache = dict[str, dict[str, dict]]


def _cache_key(event: Event) -> str:
    return f"{event.turbine}|{event.start.isoformat()}"


def _needs_escalation(result: TriageResult) -> bool:
    """Step 1 left this event uncertain: monitor with an "uncertain: ..."
    reason. Never true for an informational, chattering-only, long-stop-only
    or warning-while-running event, since none of those ever carries that
    reason (see triage.apply_rules and triage.triage)."""
    return result.triage == MONITOR and any(reason.startswith("uncertain:") for reason in result.reasons)


async def _ask_one(
    event: Event,
    context: str,
    ask: AskFn,
    questions: dict[str, dict],
    cache: EscalationCache,
    semaphore: asyncio.Semaphore,
) -> dict[str, dict]:
    key = _cache_key(event)
    cached = cache.get(key)
    if cached is not None:
        return cached
    async with semaphore:
        result = await ask({"status": event.status, "message": event.message, "context": context}, questions)
    cache[key] = result.judgments
    return result.judgments


def _reapply_code_checks(step1_result: TriageResult, triage_class: str, reasons: list[str]) -> tuple[str, list[str]]:
    """Reapplies step 1's own code-check reasons (chattering, long stop) to a
    fresh (triage_class, reasons) pair computed from step 2's judgments, the
    same way triage() applies them to step 1's: chattering only adds a reason;
    a long stop also floors no_action up to monitor before adding its own."""
    if step1_result.chattering:
        reasons = [*reasons, "chattering"]
    if "long stop" in step1_result.reasons:
        if triage_class == NO_ACTION:
            triage_class = MONITOR
        reasons = [*reasons, "long stop"]
    return triage_class, reasons


async def escalate(
    results: Sequence[TriageResult],
    events: Sequence[Event],
    all_events: Sequence[Event],
    con: duckdb.DuckDBPyConnection,
    ask: AskFn,
    cache: EscalationCache | None = None,
    limit: int = 20,
) -> list[TriageResult]:
    """Re-triage, with SCADA context, every event step 1 left uncertain.

    `results` is step 1's triage() output; `events` is the same events, in the
    same order, that produced it (results[i] is events[i]'s step-1 result).
    `all_events` is the full, all-turbine event list the context builder
    computes event history from (typically the same list as `events`, kept
    separate here since context.build_contexts already takes both). `con` is
    an open DuckDB connection over the `measurements` table
    (measurements.connect_for_read or connect_in_memory).

    Returns a list the same length and order as `results`: an escalated
    event's entry is its step-2 result (triage and reasons from step 2,
    step1_triage/step1_reasons/context/step2_judgments filled in); every other
    event is returned unchanged. `cache`, if given (even {}), is filled in
    place with every step-2 judgment actually asked, keyed per event, so a
    later call does not ask Jev again for the same event. `limit` bounds how
    many Jev requests are in flight at once.
    """
    if cache is None:
        cache = {}
    questions = load_questions(QUESTIONS_PATH)

    to_escalate = [(i, event) for i, (result, event) in enumerate(zip(results, events)) if _needs_escalation(result)]
    out = list(results)
    if not to_escalate:
        return out

    escalated_events = [event for _, event in to_escalate]
    contexts = build_contexts(escalated_events, con, all_events)

    semaphore = asyncio.Semaphore(limit)
    raw_judgments_list = await asyncio.gather(
        *(
            _ask_one(event, context, ask, questions, cache, semaphore)
            for event, context in zip(escalated_events, contexts)
        )
    )

    for (i, event), context, raw in zip(to_escalate, contexts, raw_judgments_list):
        step1_result = results[i]
        triage_class, reasons = apply_rules(Judgments(raw), event.status)
        triage_class, reasons = _reapply_code_checks(step1_result, triage_class, reasons)
        out[i] = replace(
            step1_result,
            triage=triage_class,
            reasons=reasons,
            step1_triage=step1_result.triage,
            step1_reasons=step1_result.reasons,
            context=context,
            step2_judgments=raw,
        )
    return out


# --- cache persistence -------------------------------------------------------------
# Task 3's CLI calls these to seed and save out/judgments-context.json, the same way
# __main__._load_cache/_save_cache handle out/judgments.json for step 1.


def questions_hash() -> str:
    return hashlib.sha256(QUESTIONS_PATH.read_bytes()).hexdigest()


def load_cache(path: Path) -> tuple[EscalationCache, bool]:
    """Returns (cache, ignored). `ignored` is True only when `path` existed but
    its stored questions_hash did not match
    questions/event_with_context.yaml's current hash (or the file predates
    that field), so its judgments were not trusted or used. A `path` that
    does not exist at all is just an empty starting cache, not an ignored
    one."""
    if not path.exists():
        return {}, False
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("questions_hash") != questions_hash():
        return {}, True
    return payload.get("cache", {}), False


def save_cache(path: Path, cache: EscalationCache) -> None:
    payload = {"questions_hash": questions_hash(), "cache": cache}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
