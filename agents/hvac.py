from .utils import call_llm

def hvac_agent(context: str, bootstrap: bool = False) -> str:
    if bootstrap:
        system_prompt = (
            "You are the HVAC Agent. Focus ONLY on heating, cooling, and ducting systems.\n"
            "- Review provided context.\n"
            "- Provide a short summary of HVAC findings.\n"
            "- List clear follow-up questions if info is incomplete.\n"
            "- Do not make upgrade recommendations yet."
        )
    else:
        system_prompt = (
            "You are the HVAC Agent. Focus ONLY on heating, cooling, and ducting systems.\n"
            "- DO NOT summarize.\n"
            "- ONLY output NEW follow-up questions that remain unanswered.\n"
            "- If you have no further questions, respond exactly with: 'No further HVAC questions.'"
        )

    return call_llm([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": context},
    ])