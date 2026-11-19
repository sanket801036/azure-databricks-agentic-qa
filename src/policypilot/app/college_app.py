"""College policy assistant. Local: `streamlit run src/policypilot/app/college_app.py`
after `python -m policypilot.college.ingest`.

Answers from the college's policies with a citation per claim, checks the student's own
attendance in College ERP when the question is about them, and - when it can't ground an
answer - offers to send the question to the student's teachers through the ERP instead of
leaving them with a refusal.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from policypilot.agent.llm import get_llm_client
from policypilot.college.agent import ask_college
from policypilot.college.erp import ERPClient, ERPError
from policypilot.college.ingest import get_college_store
from policypilot.config import ERP_BASE_URL, get_settings

# The public demo's student account (printed on the ERP's own sign-in page too).
DEMO_STUDENT = ("teststud", "testpass123")

st.set_page_config(page_title="College Policy Assistant", page_icon="🎓")
st.title("College Policy Assistant")
st.caption(
    "Answers from the college's own policies, cites the section every claim came from, "
    "and checks your attendance in College ERP when the question is about you."
)


@st.cache_resource
def _load_backend():
    settings = get_settings()
    if settings.is_local and not settings.groq_api_key:
        return None, None
    return get_llm_client(), get_college_store()


llm, store = _load_backend()
if llm is None:
    st.error("GROQ_API_KEY is not set. Copy .env.example to .env and add your key, then restart.")
    st.stop()

if "erp" not in st.session_state:
    st.session_state.erp = None
if "history" not in st.session_state:
    st.session_state.history = []

with st.sidebar:
    st.subheader("College ERP")
    erp: ERPClient | None = st.session_state.erp
    if erp and erp.signed_in:
        st.success(f"Signed in as **{erp.username}**")
        if st.button("Sign out"):
            st.session_state.erp = None
            st.rerun()
    else:
        st.caption(
            f"Sign in with your ERP account so I can check your attendance. ({ERP_BASE_URL})"
        )
        with st.form("erp_login"):
            username = st.text_input("Username", key="erp_user")
            password = st.text_input("Password", type="password", key="erp_pass")
            submitted = st.form_submit_button("Sign in")
        st.caption(
            f"Demo student: `{DEMO_STUDENT[0]}` / `{DEMO_STUDENT[1]}` (test data only). "
            "The free demo sleeps when idle, so the first sign-in can take up to a minute."
        )
        if st.button("Use demo student"):
            username, password, submitted = *DEMO_STUDENT, True
        if submitted:
            client = ERPClient(ERP_BASE_URL)
            try:
                with st.spinner("Signing in to College ERP..."):
                    role = client.login(username, password)
            except ERPError as exc:
                st.error(str(exc))
            else:
                if role != "student":
                    st.error("Only student accounts have an attendance record to check.")
                else:
                    st.session_state.erp = client
                    st.rerun()

    st.subheader("Try asking")
    st.markdown(
        "- What is the minimum attendance to sit the exam?\n"
        "- Can I sit the exam in every course?\n"
        "- How long do I have to question a published mark?\n"
        "- Does approved medical leave count as present?"
    )


def _render_sources(items: list[str] | None) -> None:
    if not items:
        return
    with st.expander(f"Sources ({len(items)})"):
        for i, item in enumerate(items, start=1):
            head, _, body = item.partition("\n")
            st.markdown(f"**[{i}]** {head}")
            st.caption(body[:400] + ("…" if len(body) > 400 else ""))


def _offer_teacher(turn_index: int, question: str, answer: str) -> None:
    """The refusal's way out: hand the question to a person, with what was already said."""
    erp: ERPClient | None = st.session_state.erp
    turn = st.session_state.history[turn_index]
    if turn.get("doubt"):
        st.info(f"Sent to your teachers - {turn['doubt']}.")
        return
    if not (erp and erp.signed_in):
        st.caption("Sign in with College ERP to send this question to your teachers.")
        return
    if st.button("Ask my teachers", key=f"doubt-{turn_index}"):
        try:
            doubt = erp.raise_doubt(question, assistant_said=answer)
        except ERPError as exc:
            st.error(str(exc))
        else:
            turn["doubt"] = doubt.get("outcome", "waiting for a teacher").lower()
            st.rerun()


for idx, turn in enumerate(st.session_state.history):
    with st.chat_message(turn["role"]):
        st.markdown(turn["content"])
        _render_sources(turn.get("sources"))
        if turn.get("escalate"):
            _offer_teacher(idx, turn["question"], turn["content"])

if question := st.chat_input("Ask about attendance, leave, exams or fees..."):
    st.session_state.history.append({"role": "user", "content": question})
    with st.spinner("Checking the policies..."):
        result = ask_college(llm, store, question, erp=st.session_state.erp)
    st.session_state.history.append(
        {
            "role": "assistant",
            "content": result["final_answer"],
            "sources": result.get("context_items") if result.get("grounded") else None,
            "escalate": result.get("escalate", False),
            "question": question,
        }
    )
    st.rerun()
