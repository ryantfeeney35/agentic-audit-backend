from .utils import call_llm

def siding_agent(context: str, bootstrap: bool = False) -> str:
    if bootstrap:
        system_prompt = (
            "You are the Siding Agent. Focus ONLY on siding/exterior walls.\n"
            "- Review provided context.\n"
            "- Provide a short summary of siding/exterior findings.\n"
            "- List clear follow-up questions if info is incomplete.\n"
            "- Do not make upgrade recommendations yet."
        )
    else:
        system_prompt = (
            "You are the Siding Agent. Focus ONLY on siding/exterior walls.\n"
            "- DO NOT summarize.\n"
            "- ONLY output NEW follow-up questions still unanswered.\n"
            "- If you have no further questions, respond exactly with: 'No further siding questions.'"
        )

    return call_llm([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": context},
    ])