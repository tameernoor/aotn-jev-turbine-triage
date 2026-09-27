import asyncio
import inspect
import json
from datetime import datetime, timedelta, timezone

from fakes import FakeJev, answers

from jev_turbine.escalate import EscalationCache, escalate, load_cache, save_cache
from jev_turbine.measurements import connect_in_memory
from jev_turbine.models import Event
from jev_turbine.triage import ACT_NOW, MONITOR, NO_ACTION, triage

T0 = datetime(2016, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

# Step-1 judgments (fakes.answers shape) pre-seeded straight into the step-1 cache, so
# triage() never needs an actual Jev call and each test controls exactly which of its
# events step 1 leaves uncertain.
UNCERTAIN_CAUSE = answers(
    cause={"type": "choice", "value": "fault", "probabilities": {"fault": 0.5}, "confidence": 0.5},
    safety_related=0.1,
    needs_site_visit=0.1,
)
PLANNED = answers(cause="planned", safety_related=0.1)
RUNNING = answers(cause="running", safety_related=0.1)


def ev(turbine="Kelmarsh 1", start=T0, end=None, duration=None, status="Warning", code="1", message="msg", iec_category=None):
    return Event(
        turbine=turbine,
        start=start,
        end=end,
        duration_seconds=duration,
        status=status,
        code=code,
        message=message,
        iec_category=iec_category,
    )


def step1(events, cache):
    return asyncio.run(triage(events, FakeJev().ask, cache))


class TrackingAsk:
    """Wraps a FakeJev's ask, recording the largest number of calls ever in
    flight at once (a small sleep forces genuine overlap under asyncio)."""

    def __init__(self, fake: FakeJev, delay: float = 0.01):
        self._fake = fake
        self._delay = delay
        self.current = 0
        self.max_seen = 0

    async def __call__(self, state, questions):
        self.current += 1
        self.max_seen = max(self.max_seen, self.current)
        try:
            await asyncio.sleep(self._delay)
            return await self._fake.ask(state, questions)
        finally:
            self.current -= 1


# --- selection: only step-1-uncertain events escalate ------------------------------


def test_only_uncertain_events_escalate():
    uncertain_event = ev(status="Stop", message="Pitch fault", start=T0)
    long_stop_only = ev(status="Stop", message="Cable unwind", start=T0 + timedelta(hours=2), duration=25 * 3600)
    warning_while_running = ev(status="Warning", message="Warm-up", start=T0 + timedelta(hours=4))
    plain_informational = ev(status="Informational", message="System OK", start=T0 + timedelta(hours=6))
    chattering_informational = [
        ev(status="Informational", message="Substation grid failure", start=T0 + timedelta(hours=8)),
        ev(status="Informational", message="Substation grid failure", start=T0 + timedelta(hours=8, minutes=1)),
        ev(status="Informational", message="Substation grid failure", start=T0 + timedelta(hours=8, minutes=2)),
    ]
    events = [
        uncertain_event,
        long_stop_only,
        warning_while_running,
        plain_informational,
        *chattering_informational,
    ]
    cache = {
        "Stop": {"Pitch fault": UNCERTAIN_CAUSE, "Cable unwind": PLANNED},
        "Warning": {"Warm-up": RUNNING},
    }
    results = step1(events, cache)

    # Sanity: confirm each scenario landed where the test intends before escalating.
    assert results[0].triage == MONITOR and results[0].reasons == ["uncertain: cause"]
    assert results[1].triage == MONITOR and results[1].reasons == ["planned", "long stop"]
    assert results[2].triage == MONITOR and results[2].reasons == ["warning while running"]
    assert results[3].triage == NO_ACTION and results[3].reasons == ["informational"]
    for r in results[4:]:
        assert r.triage == MONITOR and r.reasons == ["chattering"]

    con = connect_in_memory()
    step2_fake = FakeJev(values=dict(cause="external", safety_related=0.1))
    final = asyncio.run(escalate(results, events, events, con, step2_fake.ask, cache={}))

    assert len(step2_fake.calls) == 1
    assert step2_fake.calls[0]["state"]["status"] == "Stop"
    assert step2_fake.calls[0]["state"]["message"] == "Pitch fault"

    # The escalated event's final triage comes from step 2.
    assert final[0].triage == NO_ACTION
    assert final[0].reasons == ["external"]
    assert final[0].step1_triage == MONITOR

    # Every other event, including both informational shapes, comes back untouched.
    for i in range(1, len(events)):
        assert final[i] == results[i]
        assert final[i].step1_triage is None


# --- state carries the context ------------------------------------------------------


def test_state_carries_the_context():
    event = ev(status="Stop", message="Pitch fault")
    cache = {"Stop": {"Pitch fault": UNCERTAIN_CAUSE}}
    results = step1([event], cache)

    con = connect_in_memory()
    con.executemany(
        "INSERT INTO measurements VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(1, T0 - timedelta(minutes=10), 600.0, None, None, None, None)],
    )
    step2_fake = FakeJev(values=dict(cause="external", safety_related=0.1))
    final = asyncio.run(escalate(results, [event], [event], con, step2_fake.ask, cache={}))

    sent_context = step2_fake.calls[0]["state"]["context"]
    assert "power 600 kW" in sent_context
    assert set(step2_fake.calls[0]["state"]) == {"status", "message", "context"}
    assert final[0].context == sent_context


# --- final triage comes from step 2 --------------------------------------------------


def test_final_triage_comes_from_step2():
    event = ev(status="Stop", message="Pitch fault")
    cache = {"Stop": {"Pitch fault": UNCERTAIN_CAUSE}}
    results = step1([event], cache)
    assert results[0].triage == MONITOR  # step 1 was uncertain, as the test intends

    con = connect_in_memory()
    step2_fake = FakeJev(values=dict(cause="fault", safety_related=0.1, needs_site_visit=0.9))
    final = asyncio.run(escalate(results, [event], [event], con, step2_fake.ask, cache={}))

    assert final[0].triage == ACT_NOW
    assert final[0].reasons == ["fault needing a site visit"]


# --- step-1 fields are kept alongside the step-2 result ------------------------------


def test_step1_fields_are_kept():
    event = ev(status="Stop", message="Pitch fault")
    cache = {"Stop": {"Pitch fault": UNCERTAIN_CAUSE}}
    results = step1([event], cache)

    con = connect_in_memory()
    step2_fake = FakeJev(values=dict(cause="fault", safety_related=0.1, needs_site_visit=0.9))
    final = asyncio.run(escalate(results, [event], [event], con, step2_fake.ask, cache={}))

    assert final[0].step1_triage == MONITOR
    assert final[0].step1_reasons == ["uncertain: cause"]
    assert final[0].turbine == event.turbine
    assert final[0].start == event.start
    assert final[0].status == event.status
    assert final[0].message == event.message
    assert final[0].step2_judgments is not None


# --- code-check reasons from step 1 survive onto the step-2 result -------------------


def test_long_stop_reason_and_floor_are_reapplied_to_the_step2_result():
    event = ev(status="Stop", message="Pitch fault", duration=30 * 3600)
    cache = {"Stop": {"Pitch fault": UNCERTAIN_CAUSE}}
    results = step1([event], cache)
    assert results[0].reasons == ["uncertain: cause", "long stop"]

    con = connect_in_memory()
    # Step 2 resolves the uncertainty to a confident, otherwise no_action cause.
    step2_fake = FakeJev(values=dict(cause="planned", safety_related=0.1))
    final = asyncio.run(escalate(results, [event], [event], con, step2_fake.ask, cache={}))

    # no_action would floor to monitor on its own account of the long stop.
    assert final[0].triage == MONITOR
    assert final[0].reasons == ["planned", "long stop"]


# --- step-2 result shapes: still uncertain, chattering kept, flood kept -------------


def test_step2_still_uncertain_stays_monitor_with_the_step2_reason():
    event = ev(status="Stop", message="Pitch fault")
    cache = {"Stop": {"Pitch fault": UNCERTAIN_CAUSE}}
    results = step1([event], cache)

    con = connect_in_memory()
    # Step 2's own answer is uncertain too (a different question, needs_site_visit,
    # this time), so the event stays monitor with step 2's own "uncertain: ..." reason.
    step2_fake = FakeJev(
        values=dict(
            cause="fault",
            safety_related=0.1,
            needs_site_visit=0.5,
        )
    )
    final = asyncio.run(escalate(results, [event], [event], con, step2_fake.ask, cache={}))

    assert final[0].triage == MONITOR
    assert final[0].reasons == ["uncertain: needs_site_visit"]
    assert final[0].step1_reasons == ["uncertain: cause"]


def test_chattering_and_uncertain_event_keeps_the_chattering_reason_after_step2():
    events = [ev(status="Stop", message="Yaw error", start=T0 + timedelta(minutes=i)) for i in range(3)]
    cache = {"Stop": {"Yaw error": UNCERTAIN_CAUSE}}
    results = step1(events, cache)
    assert all(r.chattering for r in results)
    assert all(r.reasons == ["uncertain: cause", "chattering"] for r in results)

    con = connect_in_memory()
    step2_fake = FakeJev(values=dict(cause="external", safety_related=0.1))
    final = asyncio.run(escalate(results, events, events, con, step2_fake.ask, cache={}))

    for r in final:
        assert r.triage == NO_ACTION
        assert r.reasons == ["external", "chattering"]


def test_flood_flag_is_carried_over_to_the_step2_result():
    events = [ev(status="Warning", message=f"m{i}", start=T0 + timedelta(seconds=i)) for i in range(11)]
    cache = {"Warning": {f"m{i}": PLANNED for i in range(11)}}
    cache["Warning"]["m0"] = UNCERTAIN_CAUSE
    results = step1(events, cache)
    assert results[0].flood is True
    assert results[0].triage == MONITOR and results[0].reasons == ["uncertain: cause"]

    con = connect_in_memory()
    step2_fake = FakeJev(values=dict(cause="fault", safety_related=0.1, needs_site_visit=0.9))
    final = asyncio.run(escalate(results, events, events, con, step2_fake.ask, cache={}))

    assert final[0].flood is True
    assert final[0].triage == ACT_NOW


def test_default_limit_is_20():
    assert inspect.signature(escalate).parameters["limit"].default == 20


# --- code/iec_category never reach the state or the context --------------------------


def test_event_code_and_iec_category_never_reach_the_state_or_context():
    sentinel_code = "SENTINEL-CODE-DO-NOT-LEAK"
    sentinel_iec = "SENTINEL-IEC-DO-NOT-LEAK"
    event = ev(status="Stop", message="Pitch fault", code=sentinel_code, iec_category=sentinel_iec)
    cache = {"Stop": {"Pitch fault": UNCERTAIN_CAUSE}}
    results = step1([event], cache)

    con = connect_in_memory()
    step2_fake = FakeJev(values=dict(cause="external", safety_related=0.1))
    final = asyncio.run(escalate(results, [event], [event], con, step2_fake.ask, cache={}))

    state = step2_fake.calls[0]["state"]
    assert set(state) == {"status", "message", "context"}
    serialised_state = json.dumps(state)
    assert sentinel_code not in serialised_state
    assert sentinel_iec not in serialised_state
    assert sentinel_code not in final[0].context
    assert sentinel_iec not in final[0].context


# --- cache: reused across calls, invalidated on a questions-hash mismatch ------------


def test_cache_is_reused_no_second_ask():
    event = ev(status="Stop", message="Pitch fault")
    cache = {"Stop": {"Pitch fault": UNCERTAIN_CAUSE}}
    results = step1([event], cache)

    con = connect_in_memory()
    context_cache: EscalationCache = {}
    step2a = FakeJev(values=dict(cause="external", safety_related=0.1))
    first = asyncio.run(escalate(results, [event], [event], con, step2a.ask, context_cache))
    assert len(step2a.calls) == 1

    # A differently configured Jev would answer differently, so an unchanged result
    # proves the second call served the cache instead of asking again.
    step2b = FakeJev(values=dict(cause="fault", safety_related=0.9))
    second = asyncio.run(escalate(results, [event], [event], con, step2b.ask, context_cache))

    assert step2b.calls == []
    assert second[0].triage == first[0].triage
    assert second[0].reasons == first[0].reasons


def test_events_sharing_turbine_and_start_get_distinct_answers_after_a_round_trip(tmp_path):
    # Real Kelmarsh data has many turbine+start collisions (different messages, same
    # instant): the cache must key on the whole state sent to Jev, not turbine|start.
    shared_start = T0
    event_a = ev(status="Stop", message="Pitch fault", start=shared_start)
    event_b = ev(status="Stop", message="Yaw fault", start=shared_start)
    events = [event_a, event_b]
    cache = {"Stop": {"Pitch fault": UNCERTAIN_CAUSE, "Yaw fault": UNCERTAIN_CAUSE}}
    results = step1(events, cache)

    con = connect_in_memory()
    context_cache: EscalationCache = {}
    step2a = FakeJev(values=dict(cause="external", safety_related=0.1))
    first = asyncio.run(escalate(results, events, events, con, step2a.ask, context_cache))

    assert len(step2a.calls) == 2
    assert {call["state"]["message"] for call in step2a.calls} == {"Pitch fault", "Yaw fault"}

    path = tmp_path / "judgments-context.json"
    save_cache(path, context_cache)
    loaded_cache, ignored = load_cache(path)
    assert ignored is False

    # A differently configured Jev would answer differently, so zero calls and an
    # unchanged result on this cache-seeded run prove both answers were served.
    step2b = FakeJev(values=dict(cause="fault", safety_related=0.9, needs_site_visit=0.9))
    second = asyncio.run(escalate(results, events, events, con, step2b.ask, loaded_cache))

    assert step2b.calls == []
    assert [r.triage for r in second] == [r.triage for r in first]
    assert [r.reasons for r in second] == [r.reasons for r in first]


def test_missing_cache_file_is_not_treated_as_ignored(tmp_path):
    cache, ignored = load_cache(tmp_path / "does-not-exist.json")
    assert cache == {}
    assert ignored is False


def test_hash_mismatch_invalidates_the_cache(tmp_path):
    path = tmp_path / "judgments-context.json"
    save_cache(path, {"Kelmarsh 1|2016-01-01T12:00:00+00:00": answers(cause="external", safety_related=0.1)})

    cache, ignored = load_cache(path)
    assert ignored is False
    assert cache

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["questions_hash"] = "not-the-real-hash"
    path.write_text(json.dumps(payload), encoding="utf-8")

    cache2, ignored2 = load_cache(path)
    assert ignored2 is True
    assert cache2 == {}


# --- concurrency never exceeds the limit ---------------------------------------------


# --- dedupe: identical states are asked once, even under real interleaving ----------


class YieldingAsk:
    """Wraps a FakeJev's ask, awaiting asyncio.sleep(0) before delegating, so two
    coroutines asking for the same state genuinely interleave. The plain
    synchronous FakeJev never yields control between the cache check and the
    cache write, which would hide a missing dedupe (both coroutines would run
    to completion before either got a chance to race the other)."""

    def __init__(self, fake: FakeJev):
        self._fake = fake

    async def __call__(self, state, questions):
        await asyncio.sleep(0)
        return await self._fake.ask(state, questions)


def test_identical_states_are_asked_once_under_concurrent_interleaving():
    # Same turbine, same start, same status/message, no measurements loaded: both
    # events render to the exact same (status, message, context) state. Without
    # dedupe, both coroutines would race cache.get(key) -> None -> ask() twice.
    event_a = ev(status="Stop", message="Pitch fault", start=T0)
    event_b = ev(status="Stop", message="Pitch fault", start=T0)
    events = [event_a, event_b]
    cache = {"Stop": {"Pitch fault": UNCERTAIN_CAUSE}}
    results = step1(events, cache)
    assert results[0].triage == MONITOR and results[1].triage == MONITOR

    con = connect_in_memory()
    fake = FakeJev(values=dict(cause="external", safety_related=0.1))
    final = asyncio.run(escalate(results, events, events, con, YieldingAsk(fake), cache={}))

    assert len(fake.calls) == 1  # one distinct state, asked exactly once
    assert final[0].triage == NO_ACTION
    assert final[1].triage == NO_ACTION
    assert final[0].context == final[1].context


def test_distinct_states_sharing_turbine_and_start_are_still_both_asked_under_interleaving():
    event_a = ev(status="Stop", message="Pitch fault", start=T0)
    event_b = ev(status="Stop", message="Yaw fault", start=T0)
    events = [event_a, event_b]
    cache = {"Stop": {"Pitch fault": UNCERTAIN_CAUSE, "Yaw fault": UNCERTAIN_CAUSE}}
    results = step1(events, cache)

    con = connect_in_memory()
    fake = FakeJev(values=dict(cause="external", safety_related=0.1))
    final = asyncio.run(escalate(results, events, events, con, YieldingAsk(fake), cache={}))

    assert len(fake.calls) == 2
    assert final[0].triage == NO_ACTION
    assert final[1].triage == NO_ACTION


def test_concurrency_never_exceeds_the_limit():
    n = 6
    events = [
        ev(turbine=f"Kelmarsh {i + 1}", status="Stop", message=f"Fault {i}", start=T0 + timedelta(minutes=i))
        for i in range(n)
    ]
    cache = {"Stop": {e.message: UNCERTAIN_CAUSE for e in events}}
    results = step1(events, cache)
    assert all(r.triage == MONITOR for r in results)

    con = connect_in_memory()
    fake = FakeJev(values=dict(cause="external", safety_related=0.1))
    tracker = TrackingAsk(fake)
    asyncio.run(escalate(results, events, events, con, tracker, cache={}, limit=2))

    assert len(fake.calls) == n
    assert tracker.max_seen == 2
