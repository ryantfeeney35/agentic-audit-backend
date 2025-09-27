# agents/utils.py
from openai import OpenAI
import os

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# agents/utils.py
def merge_agent_outputs(agent_replies: list, bootstrap: bool = False) -> str:
    """
    Merge structured agent outputs.
    """
    if not agent_replies:
        return "✅ No further follow-up questions. Proceed to recommendations."

    summaries = []
    questions = []

    for reply in agent_replies:
        if bootstrap and reply.get("summary"):
            summaries.append(reply["summary"])
        if reply.get("follow_up_questions"):
            questions.extend(reply["follow_up_questions"])

    questions = list(dict.fromkeys(q.strip() for q in questions if q.strip()))  # dedupe

    if bootstrap:
        return (
            "Summary:\n"
            + "\n".join(summaries)
            + "\n\nFollow-up Questions:\n"
            + "\n".join(f"- {q}" for q in questions) if questions else
            "✅ No further follow-up questions. Proceed to recommendations."
        )
    else:
        if not questions:
            return "✅ No further follow-up questions. Proceed to recommendations."
        return "Follow-up Questions:\n" + "\n".join(f"- {q}" for q in questions)