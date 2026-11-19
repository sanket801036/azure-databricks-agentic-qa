import json

from policypilot.college.agent import REFUSAL_MESSAGE, SIGN_IN_MESSAGE, ask_college
from policypilot.college.erp import ERPError
from policypilot.retrieval.base import SearchResult

RULE = SearchResult(
    text="A student must attend at least 75% of the classes held in a course.",
    score=0.9,
    metadata={
        "title": "Attendance Regulations",
        "section": "Minimum attendance for examinations",
        "effective_date": "2026-06-01",
    },
)

RECORD = [
    {
        "course": "Database Management Systems",
        "attended": 11,
        "held": 18,
        "percentage": 61.11,
        "classes_to_attend": 10,
        "classes_can_skip": 0,
        "has_classes": True,
    }
]


class FakeStore:
    def __init__(self, results):
        self._results = results
        self.queries = []

    def upsert(self, ids, texts, metadatas):
        pass

    def search(self, query, k=5, ticker=None):
        self.queries.append(query)
        return self._results[:k]


class FakeLLM:
    def __init__(self, answer, needs_record=False, query="attendance rule"):
        self._answer = answer
        self._plan = {"search_query": query, "needs_my_record": needs_record}
        self.answer_prompts = []

    def complete(self, system, messages):
        if system.startswith("You route a student's question"):
            return json.dumps(self._plan)
        if system.startswith("You are the college policy assistant"):
            self.answer_prompts.append(system)
            return self._answer
        raise AssertionError(f"Unexpected system prompt: {system[:50]!r}")


class FakeERP:
    def __init__(self, record=RECORD, signed_in=True, error=None):
        self._record = record
        self.signed_in = signed_in
        self._error = error
        self.calls = 0

    def attendance(self):
        self.calls += 1
        if self._error:
            raise ERPError(self._error)
        return self._record


def test_a_general_rule_question_is_answered_from_policy_without_the_erp():
    erp = FakeERP()
    llm = FakeLLM("You need at least 75% in each course [1].")

    result = ask_college(llm, FakeStore([RULE]), "What is the attendance rule?", erp=erp)

    assert result["grounded"] is True
    assert result["final_answer"] == "You need at least 75% in each course [1]."
    assert erp.calls == 0


def test_a_personal_question_puts_the_live_record_in_context_as_its_own_source():
    erp = FakeERP()
    llm = FakeLLM(
        "The rule is 75% [1]. You are at 61.11% in DBMS and must attend the next 10 classes [2].",
        needs_record=True,
    )

    result = ask_college(llm, FakeStore([RULE]), "Can I sit the DBMS exam?", erp=erp)

    assert erp.calls == 1
    assert result["grounded"] is True
    record_item = result["context_items"][1]
    assert "attended 11 of 18 classes (61.11%)" in record_item
    assert "must attend the next 10 classes" in record_item
    assert "[2] Student's attendance in College ERP" in llm.answer_prompts[0]


def test_a_personal_question_while_signed_out_asks_to_sign_in_rather_than_refusing():
    llm = FakeLLM("never used [1]", needs_record=True)

    result = ask_college(llm, FakeStore([RULE]), "Am I short anywhere?", erp=None)

    assert result["final_answer"] == SIGN_IN_MESSAGE
    assert result["escalate"] is False
    assert llm.answer_prompts == []


def test_an_erp_outage_is_reported_not_papered_over():
    erp = FakeERP(error="College ERP could not be reached (ConnectTimeout).")
    llm = FakeLLM("never used [1]", needs_record=True)

    result = ask_college(llm, FakeStore([RULE]), "Am I short anywhere?", erp=erp)

    assert "could not be reached" in result["final_answer"]
    assert result["escalate"] is False


def test_an_uncited_answer_is_refused_and_offered_to_a_teacher():
    llm = FakeLLM("You need 75% in each course.")

    result = ask_college(llm, FakeStore([RULE]), "What is the attendance rule?")

    assert result["grounded"] is False
    assert result["final_answer"] == REFUSAL_MESSAGE
    assert result["escalate"] is True


def test_a_citation_to_a_source_that_was_never_given_counts_as_none():
    llm = FakeLLM("You need 75% [1], says the dean [3].")

    result = ask_college(llm, FakeStore([RULE]), "What is the attendance rule?")

    assert result["grounded"] is False
    assert result["escalate"] is True


def test_nothing_retrieved_is_refused_without_calling_the_model():
    llm = FakeLLM("never used [1]")

    result = ask_college(llm, FakeStore([]), "When does the canteen open?")

    assert result["final_answer"] == REFUSAL_MESSAGE
    assert result["escalate"] is True
    assert llm.answer_prompts == []
