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
    mode: str = "followup",   # "bootstrap" | "followup" | "recommendations" | "media"
) -> AgentOutput:
    parser = PydanticOutputParser(pydantic_object=AgentOutput)

    if mode == "bootstrap":
        system_instructions = (
            f"You are the {domain.capitalize()} Agent. Focus ONLY on {domain}.\n"
            "- Review provided context carefully.\n"
            "- You MUST always return valid JSON conforming to the schema.\n"
            "- Fill BOTH fields: `summary` + `followup_questions`.\n"
            "- If nothing missing, `followup_questions=[]`.\n"
            "- Do NOT generate upgrade recommendations yet.\n"
        )
    elif mode == "recommendations":
        system_instructions = (
            f"You are the {domain.capitalize()} Agent. Focus ONLY on {domain}.\n"
            "- Review the context carefully.\n"
            "- You MUST always return valid JSON conforming to the schema.\n"
            "- Populate ONLY the `recommendations` field.\n"
            "- If no upgrades apply, return [].\n"
        )
    elif mode == "media":
        if domain == "insulation":
            system_instructions = (
                f"You are the {domain.capitalize()} Agent. Focus ONLY on {domain}.\n"
                "You will be given raw context extracted from media (photo/video/audio).\n"
                "Analyze according to CREIA protocol:\n"
                "- Identify insulation type (fiberglass, cellulose, rockwool, foam, etc.)\n"
                "- Estimate thickness/depth if visible\n"
                "- Rate condition: Good (continuous), Fair (minor gaps), Poor (missing/major gaps)\n"
                "- Flag issues: thermal breaks, gaps, attic scuttle covers, recessed lights, air leakage\n"
                "- Mention efficiency/comfort implications.\n"
                "Return valid JSON per the schema. Put your output in `summary`.\n"
                "Do NOT generate follow-up questions or recommendations here."
            )
        elif domain == "hvac":
            system_instructions = (
                f"You are the {domain.upper()} Agent. Focus ONLY on {domain} systems.\n"
                "You will be given raw context extracted from media (photo/video/audio).\n"
                "Analyze according to CREIA protocol:\n"
                "- Identify system type (furnace, heat pump, mini-split, etc.)\n"
                "- Note brand, model, efficiency ratings (AFUE, SEER, HSPF) if visible\n"
                "- Assess age/condition (wear, rust, leaks)\n"
                "- Evaluate ducting: type, sealing, insulation, asbestos tape, filter condition\n"
                "- Flag safety issues: cracked heat exchanger, CO risk, dirty/absent filters\n"
                "- Mention efficiency implications.\n"
                "Return valid JSON per the schema. Put your output in `summary`.\n"
                "Do NOT generate follow-up questions or recommendations here."
            )
        else:  # exterior
            system_instructions = (
                f"You are the Exterior Agent. Focus ONLY on exterior envelope.\n"
                "You will be given raw context extracted from media (photo/video/audio).\n"
                "Analyze according to CREIA protocol:\n"
                "- Note shading (trees, eaves, landscape, nearby structures)\n"
                "- Assess glass–wall ratio (window area vs wall) and implications\n"
                "- Identify siding type (stucco, wood, vinyl, fiberboard, etc.)\n"
                "- Highlight comfort/efficiency impacts based on orientation.\n"
                "Return valid JSON per the schema. Put your output in `summary`.\n"
                "Do NOT generate follow-up questions or recommendations here."
            )
    else:  # followup
        system_instructions = (
            f"You are the {domain.capitalize()} Agent. Focus ONLY on {domain}.\n"
            "- You MUST always return valid JSON conforming to the schema.\n"
            "- Do NOT summarize.\n"
            "- ONLY output new `followup_questions`.\n"
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