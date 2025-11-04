# agents/utils.py
def merge_agent_outputs(agent_replies: list, bootstrap: bool = False) -> str:
    """
    Merge structured agent outputs.
    """
    if not agent_replies:
        return "✅ No further follow-up questions. Proceed to recommendations."

    summaries: list[str] = []
    questions: list[str] = []

    for reply in agent_replies:
        if not isinstance(reply, dict):
            continue
        if bootstrap and reply.get("summary"):
            summaries.append(str(reply["summary"]))
        # Accept both legacy and current key names
        fq = reply.get("followup_questions") or reply.get("follow_up_questions")
        if isinstance(fq, list):
            questions.extend([str(q) for q in fq])

    questions = list(dict.fromkeys(q.strip() for q in questions if q.strip()))  # dedupe
    # Cap total follow-up questions to 10
    questions = questions[:10]

    if bootstrap:
        summary_block = "Summary:\n" + ("\n".join(summaries) if summaries else "No summaries produced.")
        if questions:
            questions_block = "\n\nFollow-up Questions:\n" + "\n".join(f"- {q}" for q in questions)
        else:
            questions_block = "\n\n✅ No further follow-up questions. Proceed to recommendations."
        return summary_block + questions_block
    # Non-bootstrap: only return questions block
    if not questions:
        return "✅ No further follow-up questions. Proceed to recommendations."
    return "Follow-up Questions:\n" + "\n".join(f"- {q}" for q in questions)