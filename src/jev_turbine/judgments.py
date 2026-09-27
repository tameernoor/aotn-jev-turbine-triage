"""Read access to Jev's answers that remembers which ones the rules actually used.

Only answers a rule actually reads count towards uncertainty (see `uncertain`); an
answer the rules never look at is ignored even if its own confidence was low.
"""

YES = 0.8  # thresholds from TypeSafe's Noul page
NO = 0.2
CHOICE_MIN_CONFIDENCE = 0.6  # a choice read with less confidence than this is uncertain


class Judgments:
    def __init__(self, raw: dict[str, dict]):
        self._raw = raw
        self.read: list[str] = []
        self.uncertain: list[str] = []

    def _mark_read(self, qid: str) -> None:
        if qid not in self.read:
            self.read.append(qid)

    def _noul(self, qid: str) -> float:
        self._mark_read(qid)
        value = self._raw[qid]["value"]
        if NO < value < YES and qid not in self.uncertain:
            self.uncertain.append(qid)
        return value

    def yes(self, qid: str) -> bool:
        return self._noul(qid) >= YES

    def no(self, qid: str) -> bool:
        return self._noul(qid) <= NO

    def choice(self, qid: str) -> str:
        self._mark_read(qid)
        answer = self._raw[qid]
        if answer.get("confidence", 1.0) < CHOICE_MIN_CONFIDENCE and qid not in self.uncertain:
            self.uncertain.append(qid)
        return answer["value"]
