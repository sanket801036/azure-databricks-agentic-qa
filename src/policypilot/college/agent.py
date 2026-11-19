"""College agent: plan -> [fetch record] -> retrieve -> answer -> verify.

Same shape as the 10-K agent, with one tool the 10-K agent does not have: the student's own
attendance, fetched live from College ERP when the question is about them ("can I sit the
DBMS exam?") rather than about the rules in general ("what is the attendance rule?").

The record is not trusted to the model's arithmetic. The ERP already works out how many
classes a student needs to get back to 75% and how many they can still miss; those numbers
go into the context as a numbered, citable item next to the policy sections, so an answer
has to cite both the rule and the record it applied it to.

The verify step is the gate it is in the 10-K agent, tightened: every [n] in the draft must
point at an item that was actually in the context. A citation to [7] when five items were
given is a made-up source, and is treated as no citation at all. A refused answer comes
back with `escalate=True`, which the UI turns into an offer to send the question to the
student's teachers through the ERP.
"""

from __future__ import annotations

import json
import re
from typing import TypedDict

from langgraph.graph import END, StateGraph

from policypilot.agent.llm import LLMClient
from policypilot.college.erp import ERPClient, ERPError
from policypilot.retrieval.base import SearchResult, VectorStore

CITATION_RE = re.compile(r"\[(\d+)\]")

PLAN_SYSTEM_PROMPT = (
    "You route a student's question for a college policy assistant. Decide (1) a short "
    "search query for a semantic index over the college's policy documents (attendance, "
    "leave, examinations and re-evaluation, fees, accounts), and (2) whether answering "
    "needs the student's OWN attendance record - true for questions about themselves "
    "('can I sit the exam', 'how many classes do I need', 'am I short in DBMS'), false for "
    "questions about the rules in general.\n\n"
    "Reply with ONLY a JSON object of the form "
    '{"search_query": "<short search query>", "needs_my_record": true|false}, nothing else.'
)

ANSWER_SYSTEM_PROMPT = (
    "You are the college policy assistant. Answer using ONLY the numbered context below: "
    "college policy sections and, when present, the student's own attendance record from "
    "College ERP. Every sentence containing a factual claim MUST end with a bracketed "
    "citation like [1] matching the context item it came from. When you apply a rule to the "
    "student's record, cite both the rule and the record. Use the numbers in the record as "
    "given - never recompute percentages or class counts. If the context does not contain "
    "enough to answer, say so plainly instead of guessing; never answer from general "
    "knowledge.\n\nContext:\n{context}"
)

REFUSAL_MESSAGE = (
    "The college policies I have don't cover that, so I won't guess. You can send this "
    "question to your teachers."
)

SIGN_IN_MESSAGE = (
    "That depends on your own attendance, which I can only see once you sign in with your "
    "College ERP account (sidebar)."
)

ERP_UNAVAILABLE_MESSAGE = "I couldn't reach College ERP to check your record: {error}"


class CollegeState(TypedDict, total=False):
    question: str
    search_query: str
    needs_record: bool
    record: list[dict] | None
    record_error: str | None
    results: list[SearchResult]
    context_items: list[str]
    waiting_on_record: bool
    draft_answer: str
    final_answer: str
    grounded: bool
    escalate: bool


def format_record(record: list[dict]) -> str:
    lines = []
    for row in record:
        if not row.get("has_classes"):
            lines.append(f"- {row.get('course')}: no classes held yet")
            continue
        line = (
            f"- {row.get('course')}: attended {row.get('attended')} of {row.get('held')} "
            f"classes ({row.get('percentage')}%)"
        )
        if row.get("classes_to_attend"):
            line += f"; must attend the next {row['classes_to_attend']} classes to reach 75%"
        else:
            line += f"; can miss {row.get('classes_can_skip', 0)} more and stay at or above 75%"
        lines.append(line)
    return "Student's attendance in College ERP, fetched just now:\n" + "\n".join(lines)


def format_section(result: SearchResult) -> str:
    m = result.metadata or {}
    return (
        f"{m.get('title', 'College policy')} > {m.get('section', '')} "
        f"(effective {m.get('effective_date') or 'n/a'}):\n{result.text}"
    )


def build_college_graph(llm: LLMClient, store: VectorStore, erp: ERPClient | None):
    def plan_node(state: CollegeState) -> CollegeState:
        reply = llm.complete(
            system=PLAN_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": state["question"]}],
        ).strip()
        try:
            parsed = json.loads(reply)
            query = str(parsed.get("search_query") or "").strip()
            needs_record = bool(parsed.get("needs_my_record"))
        except (json.JSONDecodeError, AttributeError):
            query, needs_record = reply, False
        return {"search_query": query or state["question"], "needs_record": needs_record}

    def fetch_record_node(state: CollegeState) -> CollegeState:
        if erp is None or not erp.signed_in:
            return {"record": None, "record_error": "signed_out"}
        try:
            return {"record": erp.attendance(), "record_error": None}
        except ERPError as exc:
            return {"record": None, "record_error": str(exc)}

    def retrieve_node(state: CollegeState) -> CollegeState:
        return {"results": store.search(state["search_query"], k=4)}

    def answer_node(state: CollegeState) -> CollegeState:
        if state.get("needs_record") and state.get("record") is None:
            # A personal question with no record behind it is not refused - it is
            # answerable, just not yet - so it gets its own message and no escalation.
            error = state.get("record_error")
            message = (
                SIGN_IN_MESSAGE
                if error == "signed_out"
                else ERP_UNAVAILABLE_MESSAGE.format(error=error)
            )
            return {"draft_answer": message, "context_items": [], "waiting_on_record": True}

        items = [format_section(r) for r in state.get("results") or []]
        if state.get("record") is not None:
            items.append(format_record(state["record"]))
        if not items:
            return {"draft_answer": REFUSAL_MESSAGE, "context_items": []}

        context = "\n\n".join(f"[{i}] {item}" for i, item in enumerate(items, start=1))
        draft = llm.complete(
            system=ANSWER_SYSTEM_PROMPT.format(context=context),
            messages=[{"role": "user", "content": state["question"]}],
        )
        return {"draft_answer": draft, "context_items": items}

    def verify_node(state: CollegeState) -> CollegeState:
        draft = state["draft_answer"]
        items = state.get("context_items") or []
        if state.get("waiting_on_record"):
            return {"final_answer": draft, "grounded": False, "escalate": False}

        cited = {int(n) for n in CITATION_RE.findall(draft)}
        grounded = bool(items) and bool(cited) and all(1 <= n <= len(items) for n in cited)
        if grounded:
            return {"final_answer": draft, "grounded": True, "escalate": False}
        return {"final_answer": REFUSAL_MESSAGE, "grounded": False, "escalate": True}

    def route_after_plan(state: CollegeState) -> str:
        return "fetch_record" if state.get("needs_record") else "retrieve"

    graph = StateGraph(CollegeState)
    graph.add_node("plan", plan_node)
    graph.add_node("fetch_record", fetch_record_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("answer", answer_node)
    graph.add_node("verify", verify_node)

    graph.set_entry_point("plan")
    graph.add_conditional_edges("plan", route_after_plan, ["fetch_record", "retrieve"])
    graph.add_edge("fetch_record", "retrieve")
    graph.add_edge("retrieve", "answer")
    graph.add_edge("answer", "verify")
    graph.add_edge("verify", END)
    return graph.compile()


def ask_college(
    llm: LLMClient, store: VectorStore, question: str, erp: ERPClient | None = None
) -> CollegeState:
    return build_college_graph(llm, store, erp).invoke({"question": question})
