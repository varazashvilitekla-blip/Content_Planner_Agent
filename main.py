"""Content Planner Agent - terminal (CLI) version.

Run:  python main.py
Type 'exit' to quit.
"""

import os
import uuid

from langgraph.types import Command


def load_dotenv_file(path=".env"):
    """Load KEY=value lines from a local .env file (if it exists) into environment variables."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"'))


def print_reply(result):
    last = result["messages"][-1]
    if getattr(last, "content", ""):
        print(f"\nAgent: {last.content}\n")


def main():
    load_dotenv_file()  # must run before the agent reads the environment
    if not os.environ.get("GROQ_API_KEY"):
        print("GROQ_API_KEY is not set. Add it to .env or your environment.")
        return

    from agent import build_graph

    graph = build_graph()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    print("Content Planner Agent. Type 'exit' to quit.\n")

    while True:
        try:
            text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in {"exit", "quit"}:
            break
        if not text:
            continue
        try:
            result = graph.invoke({"messages": [("user", text)]}, config)
            # Approval flow: the agent stops before calendar / delete actions
            while result.get("__interrupt__"):
                question = result["__interrupt__"][0].value
                answer = input(f"\n⏸ {question} (yes/no): ").strip().lower()
                approved = answer in {"yes", "y", "კი", "ki"}
                result = graph.invoke(Command(resume=approved), config)
            print_reply(result)
        except Exception as e:  # e.g. Groq rate limit (429)
            print(f"\n⚠️ Error: {e}\n")


if __name__ == "__main__":
    main()
