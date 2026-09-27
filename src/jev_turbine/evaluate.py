"""Score Jev's `cause` judgments against the operator's own IEC 61400-26 category.

The mapping from IEC category to the expected `cause` bucket below is ours, chosen to
match the four `cause` values `questions/event.yaml` asks Jev to pick between; the IEC
category itself is the operator's own label, recorded before Jev was ever asked about
these events. Only non-informational events that carry an IEC category are scored;
informational events, events with a blank category, and any (status, message) pair not
present in `cache` (never asked, or asked in a run whose cache was not passed in) are
silently left out, not counted as wrong.

`evaluate(events, cache)` works on the same `Cache` shape `triage()` fills: `cache[status]
[message]` holds the raw judgments dict Jev returned for that pair, so a cache saved to
out/judgments.json and reloaded works here unchanged.

`results`, if given, is step 1 and step 2's combined output (escalate()'s return value,
or plain triage() output when step 2 was skipped): the same events, in the same order,
each carrying `step1_triage`/`step1_reasons`/`step2_judgments` (None when that event was
never escalated). When given, `evaluate` also scores the "step 1 plus step 2" cause (the
step-2 cause where an event was escalated, the step-1 cause otherwise), how escalation
moved events with an IEC category towards or away from the operator's answer, and triage
counts before versus after step 2. `results=None` (the default, and every pre-step-2
caller) reports those same fields with nothing escalated: identical to the step-1-alone
numbers, and empty triage counts.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence

from .judgments import CHOICE_MIN_CONFIDENCE
from .models import Event
from .triage import Cache, INFORMATIONAL, MONITOR, TriageResult

# Forced outage is the operator's label for an unplanned turbine fault; Scheduled
# Maintenance, Technical Standby and Requested Shutdown are all planned by the operator
# or park controller; the two "Out of ... Specification" categories are conditions
# outside the turbine (grid or weather); Full and Partial Performance both mean the
# turbine kept running. A blank category (not a key here) is not evaluated.
IEC_TO_CAUSE: dict[str, str] = {
    "Forced outage": "fault",
    "Scheduled Maintenance": "planned",
    "Technical Standby": "planned",
    "Requested Shutdown": "planned",
    "Out of Electrical Specification": "external",
    "Out of Environmental Specification": "external",
    "Full Performance": "running",
    "Partial Performance": "running",
}

NOTE = (
    "The mapping from the operator's IEC 61400-26 category to `cause` (IEC_TO_CAUSE) is "
    "ours; the IEC category itself is the operator's own label, not something Jev or this "
    "code assigned."
)


def _cause_judgment(cache: Cache, status: str, message: str) -> dict | None:
    raw = cache.get(status, {}).get(message)
    if raw is None:
        return None
    return raw.get("cause")


def _still_uncertain(result: TriageResult) -> bool:
    """True if step 2's own result (result.triage/reasons; already the step-2
    read for an escalated event) is still an uncertain monitor, the same
    predicate escalate.py uses on step 1's result to decide whether to
    escalate in the first place."""
    return result.triage == MONITOR and any(reason.startswith("uncertain:") for reason in result.reasons)


def evaluate(events: Sequence[Event], cache: Cache, results: Sequence[TriageResult] | None = None) -> dict:
    """Accuracy of Jev's `cause` against the operator's IEC category, counted per event
    and per distinct (status, message), a confusion matrix, the distinct messages where
    Jev disagreed (with Jev's probabilities), and how many distinct messages were
    uncertain (confidence < CHOICE_MIN_CONFIDENCE). See the module docstring for what is
    included and what is silently skipped, and for what `results` adds."""
    per_event: list[tuple[str, str]] = []  # (expected, got), one per scored event
    expected_by_pair: dict[tuple[str, str], list[str]] = defaultdict(list)
    cause_by_pair: dict[tuple[str, str], dict] = {}

    with_context_pairs: list[tuple[str, str]] = []  # (expected, got), step1+step2 cause
    escalated_evaluated = 0
    escalated_cause_changed = 0
    escalated_wrong_to_right = 0
    escalated_right_to_wrong = 0
    escalated_still_uncertain = 0
    triage_counts_before: Counter[str] = Counter()
    triage_counts_after: Counter[str] = Counter()

    result_seq: Sequence[TriageResult | None] = results if results is not None else [None] * len(events)
    for event, result in zip(events, result_seq):
        if result is not None:
            before = result.step1_triage if result.step1_triage is not None else result.triage
            triage_counts_before[before] += 1
            triage_counts_after[result.triage] += 1

        if event.status == INFORMATIONAL:
            continue
        expected = IEC_TO_CAUSE.get(event.iec_category) if event.iec_category else None
        if expected is None:
            continue
        cause = _cause_judgment(cache, event.status, event.message)
        if cause is None:
            continue

        got = cause["value"]
        per_event.append((expected, got))
        key = (event.status, event.message)
        expected_by_pair[key].append(expected)
        cause_by_pair[key] = cause

        escalated = result is not None and result.step1_triage is not None
        step2_cause = None
        if escalated and result.step2_judgments is not None:
            step2_value = result.step2_judgments.get("cause")
            step2_cause = step2_value["value"] if step2_value is not None else None
        with_context_pairs.append((expected, step2_cause if step2_cause is not None else got))

        if escalated:
            escalated_evaluated += 1
            if step2_cause is not None and step2_cause != got:
                escalated_cause_changed += 1
            if got != expected and step2_cause == expected:
                escalated_wrong_to_right += 1
            if got == expected and step2_cause is not None and step2_cause != expected:
                escalated_right_to_wrong += 1
            if _still_uncertain(result):
                escalated_still_uncertain += 1

    events_correct = sum(1 for expected, got in per_event if expected == got)
    events_total = len(per_event)

    with_context_correct = sum(1 for expected, got in with_context_pairs if expected == got)
    with_context_total = len(with_context_pairs)

    # One (expected, got) pair per distinct message. `expected` is the majority of that
    # message's own scored events (always unanimous in the real 2016 data; the mode just
    # gives a deterministic answer if a hand-built fixture disagrees with itself).
    message_results: dict[tuple[str, str], tuple[str, str]] = {}
    for key, expecteds in expected_by_pair.items():
        expected = Counter(expecteds).most_common(1)[0][0]
        got = cause_by_pair[key]["value"]
        message_results[key] = (expected, got)

    messages_correct = sum(1 for expected, got in message_results.values() if expected == got)
    messages_total = len(message_results)

    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for expected, got in per_event:
        confusion[expected][got] += 1

    disagreements = [
        {
            "status": status,
            "message": message,
            "expected": expected,
            "got": got,
            "probabilities": cause_by_pair[(status, message)].get("probabilities", {}),
        }
        for (status, message), (expected, got) in sorted(message_results.items())
        if expected != got
    ]

    uncertain_messages_evaluated = sum(
        1 for key in message_results if cause_by_pair[key].get("confidence", 1.0) < CHOICE_MIN_CONFIDENCE
    )
    uncertain_messages_asked = sum(
        1
        for messages in cache.values()
        for raw in messages.values()
        if raw.get("cause", {}).get("confidence", 1.0) < CHOICE_MIN_CONFIDENCE
    )

    return {
        "note": NOTE,
        "iec_to_cause": dict(IEC_TO_CAUSE),
        "events_evaluated": events_total,
        "messages_evaluated": messages_total,
        "accuracy_by_event": {
            "correct": events_correct,
            "total": events_total,
            "accuracy": events_correct / events_total if events_total else None,
        },
        "accuracy_by_message": {
            "correct": messages_correct,
            "total": messages_total,
            "accuracy": messages_correct / messages_total if messages_total else None,
        },
        # Step 1 plus step 2: the step-2 cause where an event was escalated, the step-1
        # cause otherwise. Same events as accuracy_by_event, so same total when nothing
        # was escalated (results=None, or an empty run of step 2).
        "accuracy_by_event_with_context": {
            "correct": with_context_correct,
            "total": with_context_total,
            "accuracy": with_context_correct / with_context_total if with_context_total else None,
        },
        # Among escalated events that also carry an IEC category (so both a step-1 and
        # a step-2 cause can be scored against the operator): how many changed cause at
        # all, how many moved towards or away from the operator's answer, and how many
        # were still an uncertain monitor after step 2's own read.
        "escalated_with_iec_category": {
            "evaluated": escalated_evaluated,
            "cause_changed": escalated_cause_changed,
            "wrong_to_right": escalated_wrong_to_right,
            "right_to_wrong": escalated_right_to_wrong,
            "still_uncertain": escalated_still_uncertain,
        },
        # Triage counts over every event (not only IEC-scored ones): before is step 1's
        # own triage (step1_triage where escalated, else the unescalated triage); after
        # is the final triage. Empty when `results` was not given.
        "triage_counts_before_context": dict(triage_counts_before),
        "triage_counts_after_context": dict(triage_counts_after),
        "confusion_matrix": {expected: dict(got_counts) for expected, got_counts in confusion.items()},
        "disagreements": disagreements,
        # ...evaluated: distinct messages IEC-scored above with a low-confidence cause read.
        # ...asked: the same count over every distinct pair in `cache`, scored or not, so it
        # also covers messages with no IEC category at all (e.g. most of the 98 distinct
        # pairs in a full 2016 run, only 61 of which carry a category).
        "uncertain_messages_evaluated": uncertain_messages_evaluated,
        "uncertain_messages_asked": uncertain_messages_asked,
    }
