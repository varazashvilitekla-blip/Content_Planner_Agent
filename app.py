"""Content Planner Agent - Streamlit chat page.

Run:  streamlit run app.py
Secrets: put GROQ_API_KEY, CALENDAR_URL, CALENDAR_TOKEN in .streamlit/secrets.toml
(or in environment variables). Never commit that file.
"""

import os
import uuid

import streamlit as st
from langgraph.types import Command

# Copy Streamlit secrets into environment variables before the agent is built
try:
    for key in ("GROQ_API_KEY", "CALENDAR_URL", "CALENDAR_TOKEN", "GROQ_MODEL"):
        if key in st.secrets:
            os.environ[key] = st.secrets[key]
except Exception:
    pass  # no secrets.toml: use environment variables

from agent import build_graph, list_posts  # noqa: E402

st.set_page_config(page_title="Content Planner Agent", page_icon="🗓️")
st.title("🗓️ Content Planner Agent")
st.caption("Post ideas from your brand and upcoming Georgian holidays, "
           "saved to Google Calendar after your approval.")

if not os.environ.get("GROQ_API_KEY"):
    st.error("GROQ_API_KEY is not set. Add it to .streamlit/secrets.toml.")
    st.stop()

# One graph and one conversation (thread_id) per browser session
if "graph" not in st.session_state:
    st.session_state.graph = build_graph()
    st.session_state.config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    st.session_state.chat = []          # list of (role, text) for display
    st.session_state.pending = None     # approval question, if the agent stopped

graph = st.session_state.graph
config = st.session_state.config


def handle(result):
    """Show the agent reply or remember the approval question."""
    if result.get("__interrupt__"):
        st.session_state.pending = result["__interrupt__"][0].value
    else:
        st.session_state.pending = None
        reply = result["messages"][-1].content
        if reply:
            st.session_state.chat.append(("assistant", reply))


def run(payload, spinner_text):
    try:
        with st.spinner(spinner_text):
            handle(graph.invoke(payload, config))
    except Exception as e:  # e.g. Groq rate limit (429)
        st.session_state.chat.append(("assistant", f"⚠️ Error: {e}"))


with st.sidebar:
    st.subheader("Planned posts")
    posts = list_posts.invoke({})
    if not posts:
        st.write("No posts yet.")
    for p in posts:
        mark = " 📅" if p.get("event_id") else ""
        st.write(f'#{p["id"]} · {p["publish_date"]} {p["publish_time"]}{mark}  \n{p["idea"][:50]}')
    if st.button("New conversation"):
        for k in ("graph", "config", "chat", "pending"):
            st.session_state.pop(k, None)
        st.rerun()

for role, text in st.session_state.chat:
    with st.chat_message(role):
        st.markdown(text)

# Approval flow: the agent stopped before a calendar / delete action
if st.session_state.pending:
    with st.chat_message("assistant"):
        st.warning(f"⏸ {st.session_state.pending}")
        col1, col2 = st.columns(2)
        if col1.button("✅ კი, ვადასტურებ"):
            st.session_state.chat.append(("user", "✅ კი"))
            run(Command(resume=True), "Working...")
            st.rerun()
        if col2.button("❌ არა"):
            st.session_state.chat.append(("user", "❌ არა"))
            run(Command(resume=False), "Working...")
            st.rerun()

prompt = st.chat_input("მაგ.: პოსტის იდეა მჭირდება", disabled=bool(st.session_state.pending))
if prompt:
    st.session_state.chat.append(("user", prompt))
    run({"messages": [("user", prompt)]}, "Thinking...")
    st.rerun()
