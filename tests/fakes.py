"""A stand-in for Jev in tests. Answers every question it is asked, records every call."""

from jev_turbine.jev import JevResult


def answers(**values) -> dict:
    """Judgments in the shape jev.py returns. A float is a noul, a str is a choice
    (confidence 1.0), a dict passes through unchanged (for a specific confidence or
    probabilities)."""
    out = {}
    for qid, value in values.items():
        if isinstance(value, dict):
            out[qid] = value
        elif isinstance(value, str):
            out[qid] = {"type": "choice", "value": value, "probabilities": {value: 1.0}, "confidence": 1.0}
        else:
            out[qid] = {"type": "noul", "value": float(value)}
    return out


class FakeJev:
    """values maps question id to a fixed answer (see answers()); every distinct
    (status, message) pair asked in a test gets the same answers, since the pairs a
    single test cares about are usually just one. error, if set, is raised on every
    call instead of answering."""

    def __init__(self, values: dict | None = None, error: Exception | None = None):
        self.values = values or {}
        self.error = error
        self.calls: list[dict] = []

    async def ask(self, state: dict, questions: dict[str, dict]) -> JevResult:
        self.calls.append({"state": state, "questions": questions})
        if self.error is not None:
            raise self.error
        judgments = {}
        for qid, question in questions.items():
            value = self.values.get(qid)
            if question["type"] == "noul":
                judgments[qid] = {"type": "noul", "value": 0.05 if value is None else float(value)}
            else:  # choice
                options = question["criteria"]
                picked = value if value is not None else next(iter(options))
                judgments[qid] = {
                    "type": "choice",
                    "value": picked,
                    "probabilities": {picked: 1.0},
                    "confidence": 1.0,
                }
        meta = {
            "model": "fake-jev",
            "request_id": None,
            "latency_ms": 1.0,
            "question_count": len(questions),
            "input_tokens": 100,
            "price_per_mtok_usd": 0.042,
            "cost_usd": 100 * 0.042 / 1_000_000,
        }
        return JevResult(judgments=judgments, meta=meta)
