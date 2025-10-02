import logging
from langchain.output_parsers import PydanticOutputParser
from langchain_openai import ChatOpenAI
from .schemas import AgentOutput
from models import AgentConversation, db

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

llm = ChatOpenAI(model="gpt-4.1", temperature=0.3)


def run_orchestrator_chat(messages: list[dict], domain: str) -> str:
    """Wrapper around llm.invoke that logs inputs + outputs clearly."""
    try:
        logger.debug("=== [%s] Sending to LLM ===", domain)
        for msg in messages:
            logger.debug("[%s] %s", msg["role"], msg["content"][:500])  # log first 500 chars
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
            "- If important quantitative or qualitative details are missing "
            "(e.g., R-values, SEER/HSPF, duct insulation, filter status, etc.), "
            "ALWAYS ask clear follow-up questions.\n"
            "- Even if context includes recommendations, do not assume the investigation is complete — "
            "verify by asking targeted questions.\n"
            "- Only return an empty list if absolutely all critical details for {domain} assessment are fully known.\n"
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

    system_message = system_instructions + "\n\n" + parser.get_format_instructions()

    # Build final messages
    messages = [
        {"role": "system", "content": system_message},
        {"role": "user", "content": context},
    ]

    # 🔍 Debug log context explicitly
    logger.info("➡️ Running %s agent (bootstrap=%s, ctx_len=%d)", domain, bootstrap, len(context))
    logger.debug("=== %s Context Preview ===\n%s", domain, context[:1000])  # first 1000 chars

    resp_text = run_orchestrator_chat(messages, domain)

    # ✅ Persist everything
    if audit_id:
        # Save system instructions and user context separately for clarity
        db.session.add(AgentConversation(
            audit_id=audit_id,
            domain=domain,
            role="system",
            content=system_message,
        ))
        db.session.add(AgentConversation(
            audit_id=audit_id,
            domain=domain,
            role="user",
            content=context,
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