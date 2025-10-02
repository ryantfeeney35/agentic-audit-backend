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
            logger.debug("[%s] %s", msg["role"], msg["content"][:500])
        resp = llm.invoke(messages)
        logger.debug("=== [%s] LLM Response ===", domain)
        logger.debug(resp.content)
        return resp.content
    except Exception as e:
        logger.error("❌ LLM call failed for [%s]: %s", domain, e, exc_info=True)
        raise


def run_agent(
    domain: str,
    context: str,
    bootstrap: bool = False,
    audit_id: int | None = None,
    mode: str = "followup",   # 🔑 new param: "bootstrap" | "followup" | "recommendations"
) -> AgentOutput:
    """
    Run a domain-specific agent (insulation, siding, hvac) with structured output.
    mode:
      - "bootstrap": initial run (summary + follow-up questions)
      - "followup": only new follow-up questions
      - "recommendations": generate upgrade recs with ROI
    """

    parser = PydanticOutputParser(pydantic_object=AgentOutput)

    # === System instructions ===
    if mode == "bootstrap":
        system_instructions = (
            f"You are the {domain.capitalize()} Agent. Focus ONLY on {domain}.\n"
            "- Review provided context carefully.\n"
            "- You MUST always return valid JSON conforming to the schema.\n"
            "- Fill BOTH fields:\n"
            "   • `summary`: 1–3 sentences of findings.\n"
            "   • `followup_questions`: a list of missing details (e.g., R-values, SEER/HSPF, duct insulation).\n"
            "- If nothing missing, set `followup_questions` to [].\n"
            "- Do NOT generate upgrade recommendations yet.\n"
        )
    elif mode == "recommendations":
        system_instructions = (
            f"You are the {domain.capitalize()} Agent. Focus ONLY on {domain}.\n"
            "- Review the context carefully.\n"
            "- You MUST always return valid JSON that conforms exactly to the schema.\n"
            "- Populate ONLY the `recommendations` field. Leave `summary` as null and `followup_questions` as [].\n"
            "- Each recommendation object must include:\n"
            "   • step_type (string)\n"
            "   • summary (string)\n"
            "   • annual_savings_usd (number)\n"
            "   • upgrade_cost_usd (number)\n"
            "   • payback_years (number or null)\n"
            "- If no upgrades apply, return `recommendations: []`.\n"
        )
    else:  # followup
        system_instructions = (
            f"You are the {domain.capitalize()} Agent. Focus ONLY on {domain}.\n"
            "- You MUST always return valid JSON conforming to the schema.\n"
            "- Do NOT summarize.\n"
            "- ONLY output NEW `followup_questions`.\n"
            "- If no further questions, return [].\n"
        )

    system_message = system_instructions + "\n\n" + parser.get_format_instructions()

    messages = [
        {"role": "system", "content": system_message},
        {"role": "user", "content": context},
    ]

    logger.info("➡️ Running %s agent (mode=%s, ctx_len=%d)", domain, mode, len(context))
    logger.debug("=== %s Context Preview ===\n%s", domain, context[:1000])

    resp_text = run_orchestrator_chat(messages, domain)

    # ✅ Persist conversation
    if audit_id and mode in ["bootstrap", "followup"]:
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

    # ✅ Robust parsing
    try:
        parsed = parser.parse(resp_text)
    except Exception as e:
        logger.error("❌ Parsing failed for %s agent: %s", domain, e, exc_info=True)
        parsed = AgentOutput(summary="", followup_questions=[], recommendations=[])

    return parsed