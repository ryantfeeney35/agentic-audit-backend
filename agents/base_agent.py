import logging
from langchain.output_parsers import PydanticOutputParser
from langchain_openai import ChatOpenAI
from .schemas import AgentOutput
from models import AgentConversation, db  # only if you want to persist

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

llm = ChatOpenAI(model="gpt-4.1", temperature=0.3)


def run_orchestrator_chat(messages: list[dict], domain: str) -> str:
    """Wrapper around llm.invoke that logs/persists inputs + outputs."""
    try:
        logger.debug("=== [%s] Sending to LLM ===", domain)
        for msg in messages:
            logger.debug("[%s] %s", msg["role"], msg["content"])

        resp = llm.invoke(messages)

        logger.debug("=== [%s] LLM Response ===", domain)
        logger.debug(resp.content)

        return resp.content
    except Exception as e:
        logger.error("❌ LLM call failed for [%s]: %s", domain, e, exc_info=True)
        raise


def run_agent(domain: str, context: str, bootstrap: bool = False, audit_id: int | None = None) -> AgentOutput:
    """Run a domain-specific agent (insulation, siding, hvac) with structured output."""

    parser = PydanticOutputParser(pydantic_object=AgentOutput)

    if bootstrap:
        system_instructions = (
            f"You are the {domain.capitalize()} Agent. Focus ONLY on {domain}.\n"
            "- Review provided context.\n"
            "- Provide a short summary of findings in `summary`.\n"
            "- If important quantitative or qualitative details are missing (e.g., R-values, SEER/HSPF, duct insulation, filter status, etc.), ALWAYS ask clear follow-up questions.\n"
            "- Even if context includes recommendations, do not assume the investigation is complete — verify by asking targeted questions.\n"
            "- Only return an empty list if absolutely all critical details for {domain} assessment are fully known."
            "- Provide clear follow-up questions in `followup_questions`.\n"
            "- Do not make upgrade recommendations yet.\n"
        )
    else:
        system_instructions = (
            f"You are the {domain.capitalize()} Agent. Focus ONLY on {domain}.\n"
            "- DO NOT summarize.\n"
            "- ONLY output NEW follow-up questions in `followup_questions`.\n"
            "- If no further questions, return an empty list.\n"
        )

    # ✅ Manually concatenate instructions and parser schema
    system_message = system_instructions + "\n\n" + parser.get_format_instructions()

    # Build final messages
    messages = [
        {"role": "system", "content": system_message},
        {"role": "user", "content": context},
    ]

    # Call wrapped LLM
    resp_text = run_orchestrator_chat(messages, domain)

    # Optional: persist to DB
    if audit_id:
        for msg in messages:
            db.session.add(AgentConversation(
                audit_id=audit_id,
                domain=domain,
                role=msg["role"],
                content=msg["content"],
            ))
        db.session.add(AgentConversation(
            audit_id=audit_id,
            domain=domain,
            role="assistant",
            content=resp_text,
        ))
        db.session.commit()

    # Parse into structured object
    return parser.parse(resp_text)