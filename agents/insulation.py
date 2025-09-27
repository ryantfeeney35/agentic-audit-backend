from .utils import call_llm

def insulation_agent(context: str, bootstrap: bool = False) -> str:
    if bootstrap:
        system_prompt = (
            "You are the Insulation Agent. Focus ONLY on insulation.\n"
            "- Review provided context.\n"
            "- Provide a short summary of insulation findings.\n"
            "- List clear follow-up questions if info is incomplete.\n"
            "- Do not make upgrade recommendations yet."
        )
    else:
        system_prompt = (
            "You are the Insulation Agent. Focus ONLY on insulation.\n"
            "- DO NOT summarize.\n"
            "- DO NOT repeat previously answered questions.\n"
            "- ONLY output NEW follow-up questions that remain unanswered.\n"
            "- If you have no further questions, respond exactly with: 'No further insulation questions.'"
        )

    return call_llm([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": context},
    ])