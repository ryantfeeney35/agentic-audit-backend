import logging
import base64
import json
from langchain_core.output_parsers import PydanticOutputParser
from langchain_openai import ChatOpenAI
from .schemas import (
    AgentOutput,
    BootstrapOutput,
    ExteriorSidingSchema,
    ExteriorMediaSchema,
    InteriorRoomSchema,
    HVACSchema,
    InsulationSchema,
    InterviewSchema,
)
from models import AgentConversation, db
from memory.config import memory_enabled
from memory import chat_memory as chatmem

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

llm = ChatOpenAI(model="gpt-4.1", temperature=0.3)

# Map domains to media schemas
MEDIA_SCHEMAS = {
    "exterior": ExteriorMediaSchema,
    "hvac": HVACSchema,
    "insulation": InsulationSchema,
    "interview": InterviewSchema,
    "interior": InteriorRoomSchema,
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
    mode: str = "followup",  # "bootstrap" | "followup" | "recommendations" | "media"
) -> dict:
    """Run a domain agent against the provided context.

    Special handling: when invoked in recommendations mode for the orchestrator's
    audio-only pass, we may receive a dict like {"type": "audio_pass", "text": "..."}.
    If the audio text is empty, short-circuit and return an empty recommendations list
    to avoid generic, unsupported suggestions.
    """
    # Pick parser based on mode/domain
    if mode == "media" and domain in MEDIA_SCHEMAS:
        parser = PydanticOutputParser(pydantic_object=MEDIA_SCHEMAS[domain])
    elif mode == "bootstrap":
        # In bootstrap flows, require a summary using a stricter schema
        parser = PydanticOutputParser(pydantic_object=BootstrapOutput)
    else:
        parser = PydanticOutputParser(pydantic_object=AgentOutput)

    # Guard: audio recommendations pass with empty transcript should yield no recs
    if (
        mode == "recommendations"
        and isinstance(context, dict)
        and context.get("type") == "audio_pass"
        and not (context.get("text") or "").strip()
    ):
        logger.debug("run_agent: audio_pass with empty text -> returning empty recommendations")
        return {"summary": "", "followup_questions": [], "recommendations": []}

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
        elif domain == "interior":
            system_instructions = (
                "You are the Interior Agent (CREIA protocol).\n"
                "- Identify room type, ceiling height, and ceiling material\n"
                "- Highlight comfort/efficiency impacts\n"
                "- Return structured JSON using the InteriorMediaOutput schema."
            )
        else:  # exterior
            system_instructions = (
                "You are the Exterior Agent (CREIA protocol).\n"
                "Your task is to analyze exterior photos for both siding context and ventilation.\n"
                "Requirements:\n"
                "- Detect orientation (if possible), shading, glass–wall ratio, and siding type.\n"
                "- Detect and classify visible vents: soffit (intake), gable, ridge/roof, crawl space; identify powered vents/whole-house fan if visible.\n"
                "- For each vent: infer function (intake/exhaust/unknown), location (eave/gable/ridge/crawl space/roof), and condition (good/blocked/painted_over/damaged/missing/unknown).\n"
                "- Evaluate ventilation balance in plain language and note any signs of moisture staining/mold near vents.\n"
                "- Provide a CREIA-aligned recommendation with a short rationale.\n"
                "- Compute an overall confidence in [0,1]. Only include follow-up questions when confidence < 0.6; otherwise, followup_questions must be empty.\n"
                "- Return structured JSON using the ExteriorMediaSchema."
            )
    elif mode == "bootstrap":
        system_instructions = f"""
            You are the {domain.capitalize()} Agent operating under the CREIA home energy assessment protocol. Focus ONLY on {domain}.
            - Always return JSON conforming to AgentOutput.
            - Fill BOTH `summary` and `followup_questions`.

            Start by producing a concise `summary` of the current {domain} findings
            based on the provided context, then identify up to 10 precise follow-up questions
            needed to complete the data required for recommendations.

            Guidelines:
            - Review the provided context carefully and identify factual gaps.
            - Ask at most 10 questions that would help complete your understanding.
            - Focus on measurable, observable, or verifiable details (materials, dimensions, conditions, access, usage patterns).
            - Avoid repeating information already covered in the context.
            - Do NOT ask if the user wants recommendations — recommendations are always the next step.
            - Phrase questions naturally for a field auditor to ask a homeowner or themselves during inspection.
            """
    elif mode == "recommendations":
        # If this is the orchestrator's audio-only pass, constrain behavior tightly
        is_audio_only = isinstance(context, dict) and context.get("type") == "audio_pass"
        extra = (
            "\n- You are running in audio-only mode. Use ONLY the provided transcript text. "
            "If the transcript lacks actionable details, return an empty `recommendations` list."
            if is_audio_only
            else ""
        )
        system_instructions = (
            f"You are the {domain.capitalize()} Agent. Focus ONLY on {domain}.\n"
            "- Always return JSON conforming to AgentOutput.\n"
            f"- Recommendation type should be appropriate for the domain ({domain})\n"
            "- Populate ONLY `recommendations`."
            + extra
        )
    else:  # followup
        system_instructions = f"""
            You are the {domain.capitalize()} Agent operating under the CREIA home energy assessment protocol.

            Your task is to generate follow-up questions that will fill in missing information
            required to produce precise and technically valid upgrade recommendations
            for this home's {domain} systems.

            Guidelines:
            - Review the provided context carefully and identify factual gaps.
            - Ask at most 10 questions that would help complete your understanding.
            - Focus on measurable, observable, or verifiable details (materials, dimensions, conditions, access, usage patterns).
            - Avoid repeating information already covered in the context.
            - Do NOT ask if the user wants recommendations — recommendations are always the next step.
            - Phrase questions naturally for a field auditor to ask a homeowner or themselves during inspection.
            - Return JSON conforming to AgentOutput, with all questions listed under `followup_questions`.
            """

    system_message = system_instructions + "\n\n" + parser.get_format_instructions()

    # -------------------------
    # Build user messages based on context type
    # -------------------------
    messages = [{"role": "system", "content": system_message}]

    if isinstance(context, dict):
        # 🎨 Handle single image
        if context.get("type") == "image" and "b64" in context:
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{context['b64']}"
                            },
                        }
                    ],
                }
            )

        # 🖼️ Handle batch of images (multiple photos/videos)
        elif context.get("type") == "image_batch" and "images" in context:
            content_items = []
            for img in context["images"]:
                b64 = img.get("b64")
                fname = img.get("file_name", "photo.jpg")
                if not b64:
                    continue
                content_items.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{b64}",
                            "detail": "high",
                        },
                    }
                )
                # add text labels for traceability
                content_items.append({"type": "text", "text": f"Image: {fname}"})
            messages.append({"role": "user", "content": content_items})

        # 🔊 Handle audio
        elif context.get("type") == "audio":
            transcript = context.get("transcript", "")
            messages.append({"role": "user", "content": transcript})

        else:
            messages.append({"role": "user", "content": json.dumps(context)})

    else:
        # Default: plain text
        messages.append({"role": "user", "content": str(context)})

    # -------------------------
    # Invoke the model
    # -------------------------
    resp_text = run_orchestrator_chat(messages, domain)

    # -------------------------
    # Persist conversation (only for interactive modes)
    # -------------------------
    if audit_id and mode in ["bootstrap", "followup"]:
        if memory_enabled():
            try:
                chatmem.save_message(audit_id, domain, "system", system_message)
                chatmem.save_message(audit_id, domain, "user", str(context)[:2000])
                chatmem.save_message(audit_id, domain, "assistant", resp_text)
            except Exception:
                # Fallback to legacy DB writes on failure
                db.session.add(
                    AgentConversation(
                        audit_id=audit_id, domain=domain, role="system", content=system_message
                    )
                )
                db.session.add(
                    AgentConversation(
                        audit_id=audit_id,
                        domain=domain,
                        role="user",
                        content=str(context)[:2000],
                    )
                )
                db.session.add(
                    AgentConversation(
                        audit_id=audit_id,
                        domain=domain,
                        role="assistant",
                        content=resp_text,
                    )
                )
                db.session.commit()
        else:
            db.session.add(
                AgentConversation(
                    audit_id=audit_id, domain=domain, role="system", content=system_message
                )
            )
            db.session.add(
                AgentConversation(
                    audit_id=audit_id,
                    domain=domain,
                    role="user",
                    content=str(context)[:2000],
                )
            )
            db.session.add(
                AgentConversation(
                    audit_id=audit_id,
                    domain=domain,
                    role="assistant",
                    content=resp_text,
                )
            )
            db.session.commit()

    # -------------------------
    # Parse response
    # -------------------------
    try:
        parsed = parser.parse(resp_text)
        return parsed.model_dump()
    except Exception as e:
        logger.error("❌ Parsing failed for %s agent: %s", domain, e, exc_info=True)
        return {
            "summary": "",
            "followup_questions": [],
            "recommendations": [],
            "error": str(e),
        }