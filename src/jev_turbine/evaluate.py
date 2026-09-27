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
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence

from .judgments import CHOICE_MIN_CONFIDENCE
from .models import Event
from .triage import Cache, INFORMATIONAL

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


def evaluate(events: Sequence[Event], cache: Cache) -> dict:
    """Accuracy of Jev's `cause` against the operator's IEC category, counted per event
    and per distinct (status, message), a confusion matrix, the distinct messages where
    Jev disagreed (with Jev's probabilities), and how many distinct messages were
    uncertain (confidence < CHOICE_MIN_CONFIDENCE). See the module docstring for what is
    included and what is silently skipped."""
    per_event: list[tuple[str, str]] = []  # (expected, got), one per scored event
    expected_by_pair: dict[tuple[str, str], list[str]] = defaultdict(list)
    cause_by_pair: dict[tuple[str, str], dict] = {}

    for event in events:
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

    events_correct = sum(1 for expected, got in per_event if expected == got)
    events_total = len(per_event)

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
        "confusion_matrix": {expected: dict(got_counts) for expected, got_counts in confusion.items()},
        "disagreements": disagreements,
        # ...evaluated: distinct messages IEC-scored above with a low-confidence cause read.
        # ...asked: the same count over every distinct pair in `cache`, scored or not, so it
        # also covers messages with no IEC category at all (e.g. most of the 98 distinct
        # pairs in a full 2016 run, only 61 of which carry a category).
        "uncertain_messages_evaluated": uncertain_messages_evaluated,
        "uncertain_messages_asked": uncertain_messages_asked,
    }
