"""Step 2: re-ask Jev with SCADA context for events step 1 left uncertain.

Selects events step 1 left uncertain, builds their context in one
context.build_contexts call, asks Jev again with the step-2 questions
(questions/event_with_context.yaml) and re-applies triage.apply_rules to the
answers. See escalate()'s own docstring for the full contract, and
EscalationCache below for how answers are keyed and persisted.
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

# cache[key] holds the raw judgments dict Jev returned for that key's state (the
# same shape as JevResult.judgments). `key` is sha256 of the exact state sent to
# Jev (see _cache_key/_state), not turbine|start: two events on the same turbine
# starting at the same instant are common in the real data and are different
# questions whenever their message differs, and a context.py change changes the
# state (hence the key) too, so it can never serve a stale answer. Two events
# that truly send Jev the same status/message/context legitimately share one
# answer. Pass a dict in (even {}) to have it filled in place, so it can be
# persisted and reused without asking Jev again for the same state.
EscalationCache = dict[str, dict[str, dict]]


def _state(event: Event, context: str) -> dict:
    return {"status": event.status, "message": event.message, "context": context}


def _cache_key(state: dict) -> str:
    payload = json.dumps(state, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _needs_escalation(result: TriageResult) -> bool:
    """Step 1 left this event uncertain: monitor with an "uncertain: ..."
    reason. Never true for an informational, chattering-only, long-stop-only
    or warning-while-running event, since none of those ever carries that
    reason (see triage.apply_rules and triage.triage)."""
    return result.triage == MONITOR and any(reason.startswith("uncertain:") for reason in result.reasons)


async def _ask_one(
    state: dict,
    ask: AskFn,
    questions: dict[str, dict],
    cache: EscalationCache,
    semaphore: asyncio.Semaphore,
) -> dict[str, dict]:
    key = _cache_key(state)
    cached = cache.get(key)
    if cached is not None:
        return cached
    async with semaphore:
        result = await ask(state, questions)
    cache[key] = result.judgments
    return result.judgments


def _reapply_code_checks(step1_result: TriageResult, triage_class: str, reasons: list[str]) -> tuple[str, list[str]]:
    """Reapplies step 1's own code-check reasons (chattering, long stop) to a
    fresh (triage_class, reasons) pair computed from step 2's judgments, the
    same way triage() applies them to step 1's: chattering only adds a reason;
    a long stop also floors no_action up to monitor before adding its own."""
    if step1_result.chattering:
        reasons = [*reasons, "chattering"]
    if "long stop" in step1_result.reasons:  # the exact string triage()'s is_long_stop branch appends
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
    place with every step-2 judgment actually asked, keyed by the exact state
    sent to Jev (see EscalationCache), so a later call does not ask Jev again
    for the same status/message/context. Two escalated events that render the
    same state within a single call are also only asked once each, not once
    per event (see the dedupe below `contexts` above). `limit` bounds how many
    Jev requests are in flight at once.
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
    states = [_state(event, context) for event, context in zip(escalated_events, contexts)]
    keys = [_cache_key(state) for state in states]

    # Dedupe by state before asking: two escalated events can render the exact same
    # (status, message, context). Real 2016 data has 1,200 uncertain events but only
    # 1,167 distinct states. Gathering over every state (one coroutine per event)
    # would race two lookups of the same not-yet-cached key and ask Jev twice;
    # gathering over unique keys only asks once per distinct state, then the answer
    # is mapped back to every event that shares it.
    unique_states: dict[str, dict] = {}
    for key, state in zip(keys, states):
        unique_states.setdefault(key, state)

    semaphore = asyncio.Semaphore(limit)
    unique_keys = list(unique_states)
    unique_raw = await asyncio.gather(
        *(_ask_one(unique_states[key], ask, questions, cache, semaphore) for key in unique_keys)
    )
    raw_by_key = dict(zip(unique_keys, unique_raw))
    raw_judgments_list = [raw_by_key[key] for key in keys]

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
