"""Score the step-1 derived `cause` (triage.derive_cause) against the operator's own
IEC 61400-26 category, using the IEC_TO_CAUSE mapping below (ours, not the operator's).
`unclear` is never a value in that mapping, so a derived cause of `unclear` always
counts as wrong on its own; `causes_unclear` separately reports how many landed there.
Only non-informational events with both an IEC category and a cache entry are scored;
everything else is silently left out, not counted as wrong.

`evaluate(events, cache)` works on the same `Cache` shape `triage()` fills: `cache[status]
[message]` holds the raw judgments dict Jev returned for that pair, so a cache saved to
out/judgments.json and reloaded works here unchanged.

`results`, if given, is step 1 and step 2's combined output (escalate()'s return value,
or plain triage() output when step 2 was skipped): the same events, in the same order,
each carrying `step1_triage`/`step1_reasons`/`cause` (step1_triage is None when that
event was never escalated). When given, `evaluate` also scores the "step 1 plus step 2"
cause (the final `cause` on the result where an event was escalated, the step-1 cause
otherwise), how many events step 2 decided by "kept producing" and how many of those
with an IEC category agree with the operator, and triage counts before versus after
step 2. `results=None` (the default, and every pre-step-2 caller) reports those same
fields with nothing escalated: identical to the step-1-alone numbers, and empty triage
counts.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence

from .escalate import KEPT_PRODUCING
from .judgments import Judgments
from .models import Event
from .triage import Cache, INFORMATIONAL, RUNNING, TriageResult, UNCLEAR, derive_cause

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


def _derived_cause(cache: Cache, status: str, message: str) -> tuple[str, list[str]] | None:
    """The step-1 derived cause for this (status, message) pair (triage.derive_cause),
    plus the ids (if any) its Judgments read marked uncertain while deriving it. None if
    this pair was never asked (no cache entry) at all."""
    raw = cache.get(status, {}).get(message)
    if raw is None:
        return None
    j = Judgments(raw)
    cause = derive_cause(j, status)
    return cause, list(j.uncertain)


def evaluate(events: Sequence[Event], cache: Cache, results: Sequence[TriageResult] | None = None) -> dict:
    """Accuracy of the step-1 derived `cause` against the operator's IEC category,
    counted per event and per distinct (status, message), a confusion matrix (with
    `unclear` as a column, since it is a value `cause` can take), the distinct messages
    where the derived cause disagreed (with the ids, if any, its derivation found
    uncertain), and how many distinct messages derived to `unclear`. See the module
    docstring for what is included and what is silently skipped, and for what `results`
    adds."""
    per_event: list[tuple[str, str]] = []  # (expected, got), one per scored event
    expected_by_pair: dict[tuple[str, str], list[str]] = defaultdict(list)
    cause_by_pair: dict[tuple[str, str], tuple[str, list[str]]] = {}

    with_context_pairs: list[tuple[str, str]] = []  # (expected, got), step1+step2 cause
    triage_counts_before: Counter[str] = Counter()
    triage_counts_after: Counter[str] = Counter()
    kept_producing_decided = 0
    kept_producing_with_category = 0
    kept_producing_agreed = 0

    result_seq: Sequence[TriageResult | None] = results if results is not None else [None] * len(events)
    for event, result in zip(events, result_seq):
        if result is not None:
            before = result.step1_triage if result.step1_triage is not None else result.triage
            triage_counts_before[before] += 1
            triage_counts_after[result.triage] += 1

            if KEPT_PRODUCING in result.reasons:
                kept_producing_decided += 1
                kept_producing_expected = IEC_TO_CAUSE.get(event.iec_category) if event.iec_category else None
                if kept_producing_expected is not None:
                    kept_producing_with_category += 1
                    if kept_producing_expected == RUNNING:
                        kept_producing_agreed += 1

        if event.status == INFORMATIONAL:
            continue
        expected = IEC_TO_CAUSE.get(event.iec_category) if event.iec_category else None
        if expected is None:
            continue
        derived = _derived_cause(cache, event.status, event.message)
        if derived is None:
            continue

        got = derived[0]
        per_event.append((expected, got))
        key = (event.status, event.message)
        expected_by_pair[key].append(expected)
        cause_by_pair[key] = derived

        # Escalated: step 1's own read is kept separately (step1_triage/step1_reasons),
        # and result.cause already holds step 2's own final cause (unchanged from step
        # 1's when step 2 decided neither "kept producing" nor "stopped"; see
        # escalate._apply_step2_rules).
        escalated = result is not None and result.step1_triage is not None
        with_context_pairs.append((expected, result.cause if escalated else got))

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
        got = cause_by_pair[key][0]
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
            "uncertain": cause_by_pair[(status, message)][1],
        }
        for (status, message), (expected, got) in sorted(message_results.items())
        if expected != got
    ]

    # How many step-1 causes were unclear: among the IEC-scored pairs above
    # (...evaluated), and among every distinct pair ever asked, scored or not
    # (...asked), the same "evaluated vs. asked" split accuracy_by_message/cache uses
    # elsewhere in this module.
    causes_unclear_evaluated = sum(1 for key in message_results if cause_by_pair[key][0] == UNCLEAR)
    causes_unclear_asked = sum(
        1
        for status, messages in cache.items()
        for message, raw in messages.items()
        if derive_cause(Judgments(raw), status) == UNCLEAR
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
        # How many events step 2 decided were "kept producing" (over every event with
        # a result, not only IEC-scored ones); of those, how many carry an IEC
        # category at all, and how many of that subset agree with the operator (their
        # category maps to `running`, the cause "kept producing" always assigns).
        "step2_kept_producing": {
            "decided": kept_producing_decided,
            "with_iec_category": kept_producing_with_category,
            "agreed": kept_producing_agreed,
        },
        # Triage counts over every event (not only IEC-scored ones): before is step 1's
        # own triage (step1_triage where escalated, else the unescalated triage); after
        # is the final triage. Empty when `results` was not given.
        "triage_counts_before_context": dict(triage_counts_before),
        "triage_counts_after_context": dict(triage_counts_after),
        "confusion_matrix": {expected: dict(got_counts) for expected, got_counts in confusion.items()},
        "disagreements": disagreements,
        # ...evaluated: distinct messages IEC-scored above whose derived cause is
        # `unclear` (always wrong, see the module docstring).
        # ...asked: the same count over every distinct pair in `cache`, scored or not, so
        # it also covers messages with no IEC category at all (e.g. most of the 98
        # distinct pairs in a full 2016 run, only 61 of which carry a category).
        "causes_unclear": {
            "evaluated": causes_unclear_evaluated,
            "asked": causes_unclear_asked,
        },
    }
