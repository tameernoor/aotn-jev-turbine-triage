import asyncio
import json

import httpx2
import pytest
import typesafe_sdk as ts

from jev_turbine.jev import PRICE_PER_MTOK_USD, Jev, JevRejected, JevUnavailable

QUESTIONS = {
    "cause": {
        "type": "choice",
        "instructions": "What most likely caused this event?",
        "criteria": {"fault": None, "planned": None, "external": None, "running": None},
    },
    "safety_related": {"type": "noul", "instructions": "Does this event indicate a risk?"},
}

OK_BODY = {
    "model": "jev-1.13.0",
    "answers": {
        "cause": {
            "type": "choice",
            "choice": "fault",
            "probabilities": {"fault": 0.8, "planned": 0.1, "external": 0.05, "running": 0.05},
            "confidence": 0.8,
        },
        "safety_related": {"type": "noul", "noul": 0.91},
    },
    "usage": {"input_tokens": 1500, "output_tokens": 20},
}


def ask_with(handler, questions=QUESTIONS, state=None):
    async def go():
        client = ts.AsyncTypeSafeClient(
            api_key="test",
            transport=httpx2.MockTransport(handler),
            retry=ts.RetryPolicy(max_retries=0),
        )
        jev = Jev(client)
        try:
            return await jev.ask(state or {"status": "Stop", "message": "Emergency stop nacelle"}, questions)
        finally:
            await jev.aclose()

    return asyncio.run(go())


def test_one_request_carries_every_question_and_answers_are_normalised():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx2.Response(200, headers={"x-typesafe-request-id": "req_123"}, json=OK_BODY)

    result = ask_with(handler)

    assert len(seen) == 1
    assert set(seen[0]["questions"]) == set(QUESTIONS)
    assert seen[0]["state"] == {"status": "Stop", "message": "Emergency stop nacelle"}
    assert result.judgments["cause"] == {
        "type": "choice",
        "value": "fault",
        "probabilities": {"fault": 0.8, "planned": 0.1, "external": 0.05, "running": 0.05},
        "confidence": 0.8,
    }
    assert result.judgments["safety_related"] == {"type": "noul", "value": 0.91}
    assert result.meta["model"] == "jev-1.13.0"
    assert result.meta["request_id"] == "req_123"
    assert result.meta["question_count"] == 2
    assert result.meta["input_tokens"] == 1500
    assert result.meta["price_per_mtok_usd"] == PRICE_PER_MTOK_USD
    assert result.meta["cost_usd"] == pytest.approx(1500 * 0.042 / 1_000_000)
    assert result.meta["latency_ms"] >= 0


def test_missing_request_id_header_becomes_none():
    result = ask_with(lambda request: httpx2.Response(200, json=OK_BODY))
    assert result.meta["request_id"] is None


def test_jev_422_is_rejected():
    with pytest.raises(JevRejected):
        ask_with(lambda request: httpx2.Response(422, json={"detail": "bad question"}))


def test_question_the_sdk_refuses_locally_is_rejected_without_a_call():
    def handler(request):
        raise AssertionError("no request should be sent")

    with pytest.raises(JevRejected):
        ask_with(handler, questions={"q": {"type": "score", "instructions": "x", "criteria": []}})


def test_server_error_is_unavailable():
    with pytest.raises(JevUnavailable):
        ask_with(lambda request: httpx2.Response(500, json={"detail": "boom"}))


def test_connection_error_is_unavailable():
    def handler(request):
        raise httpx2.ConnectError("no route", request=request)

    with pytest.raises(JevUnavailable):
        ask_with(handler)


def test_constructing_without_a_key_fails(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(ts.TypeSafeError):
        Jev()
