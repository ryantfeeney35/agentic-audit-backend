import logging
import base64
import json
from langchain.output_parsers import PydanticOutputParser
from langchain_openai import ChatOpenAI
from .schemas import (AgentOutput,
    ExteriorSidingSchema,
    HVACSchema,
    InsulationSchema,
    InterviewSchema)
from models import AgentConversation, db

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

llm = ChatOpenAI(model="gpt-4.1", temperature=0.3)

# Map domains to media schemas
MEDIA_SCHEMAS = {
    "exterior": ExteriorSidingSchema,
    "hvac": HVACSchema,
    "insulation": InsulationSchema,
    "interview": InterviewSchema,
}

def run_orchestrator_chat(messages: list[dict], domain: str) -> str:
    """Wrapper around llm.invoke that logs inputs + outputs clearly."""
    try:
        logger.debug("=== [%s] Sending to LLM ===", domain)
        for msg in messages:
            logger.debug("[%s] %s", msg["role"], str(msg["content"])[:500])
        resp = llm.invoke(messages)
        logger.debug("=== [%s] LLM Response ===", domain)
        logger.debug(resp.content)
        return resp.content
    except Exception as e:
        logger.error("❌ LLM call failed for [%s]: %s", domain, e, exc_info=True)
        raise


def run_agent(
    domain: str,
    context: str | dict,
    bootstrap: bool = False,
    audit_id: int | None = None,
    mode: str = "followup",   # "bootstrap" | "followup" | "recommendations" | "media"
) -> dict:
    # Pick parser based on mode/domain
    if mode == "media" and domain in MEDIA_SCHEMAS:
        parser = PydanticOutputParser(pydantic_object=MEDIA_SCHEMAS[domain])
    else:
        parser = PydanticOutputParser(pydantic_object=AgentOutput)

    # -------------------------
    # System instructions
    # -------------------------
    if mode == "media":
        if domain == "insulation":
            system_instructions = (
                "You are the Insulation Agent (CREIA protocol).\n"
                "- Identify insulation type, depth, and condition\n"
                "- Flag gaps/thermal breaks, attic cover, recessed lights\n"
                "- Return structured JSON using the InsulationMediaOutput schema."
            )
        elif domain == "hvac":
            system_instructions = (
                "You are the HVAC Agent (CREIA protocol).\n"
                "- Identify system type, brand/model, efficiency ratings\n"
                "- Assess ducting (sealing, insulation, asbestos tape)\n"
                "- Flag safety/efficiency issues\n"
                "- Return structured JSON using the HVACMediaOutput schema."
            )
        else:  # exterior
            system_instructions = (
                "You are the Exterior Agent (CREIA protocol).\n"
                "- Detect orientation (if possible)\n"
                "- Note shading and glass–wall ratio\n"
                "- Identify siding type\n"
                "- Highlight comfort/efficiency impacts\n"
                "- Return structured JSON using the ExteriorMediaOutput schema."
            )
    elif mode == "bootstrap":
        system_instructions = (
            f"You are the {domain.capitalize()} Agent. Focus ONLY on {domain}.\n"
            "- Always return JSON conforming to AgentOutput.\n"
            "- Fill BOTH `summary` and `followup_questions`."
        )
    elif mode == "recommendations":
        system_instructions = (
            f"You are the {domain.capitalize()} Agent. Focus ONLY on {domain}.\n"
            "- Always return JSON conforming to AgentOutput.\n"
            "- Populate ONLY `recommendations`."
        )
    else:  # followup
        system_instructions = (
            f"You are the {domain.capitalize()} Agent. Focus ONLY on {domain}.\n"
            "- Always return JSON conforming to AgentOutput.\n"
            "- Only output `followup_questions`."
        )

    system_message = system_instructions + "\n\n" + parser.get_format_instructions()

    # -------------------------
    # User message construction
    # -------------------------
    if isinstance(context, dict) and context.get("type") == "image":
        messages = [
            {"role": "system", "content": system_message},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{context['b64']}" }}
            ]},
        ]
    elif isinstance(context, dict) and context.get("type") == "audio":
        # TODO: add transcription before feeding back in
        transcript = context.get("transcript", "")
        messages = [
            {"role": "system", "content": system_message},
            {"role": "user", "content": transcript},
        ]
    else:  # plain text context
        messages = [
            {"role": "system", "content": system_message},
            {"role": "user", "content": context},
        ]

    resp_text = run_orchestrator_chat(messages, domain)

    # ✅ Persist conversation for interactive modes
    if audit_id and mode in ["bootstrap", "followup"]:
        db.session.add(AgentConversation(
            audit_id=audit_id, domain=domain, role="system", content=system_message
        ))
        db.session.add(AgentConversation(
            audit_id=audit_id, domain=domain, role="user", content=str(context)[:2000]
        ))
        db.session.add(AgentConversation(
            audit_id=audit_id, domain=domain, role="assistant", content=resp_text
        ))
        db.session.commit()

    # ✅ Parse with schema
    try:
        parsed = parser.parse(resp_text)
        return parsed.model_dump()
    except Exception as e:
        logger.error("❌ Parsing failed for %s agent: %s", domain, e, exc_info=True)
        return {"summary": "", "followup_questions": [], "recommendations": []}