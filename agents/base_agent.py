import logging
import base64
import json
from langchain_core.output_parsers import PydanticOutputParser
from langchain_openai import ChatOpenAI
from .schemas import (
    AgentOutput,
    BootstrapOutput,
    StepType,
    ExteriorSidingSchema,
    ExteriorMediaSchema,
    InteriorRoomSchema,
    HVACSchema,
    InsulationSchema,
    InterviewSchema,
    RoofMediaSchema,
)
from .roi import enrich_recommendations_with_roi
from .services.prompt_helpers import get_service_taxonomy_prompt
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
    "roof": RoofMediaSchema,
    # energy_usage uses EnergyUsageAgentOutput via dedicated analyze function
    # but we map it here for potential future media processing
    "energy_usage": None,  # Uses custom schema via energy_usage.py
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

    STRICT EVIDENCE MODE (Option A):
    - The app NEVER has access to the human auditor's EA report.
    - The model may ONLY use information present in:
        * Photos / image batches
        * Audio transcripts
        * Structured context objects provided by the app
        * Prior agent outputs included in `context`
    - The model MUST NOT invent generic best-practice recommendations that are
      not directly justified by explicit evidence in the provided context.

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
        # MEDIA MODE = DESCRIPTIVE + EVIDENCE-ONLY FLAGS.
        # No generalized upgrade ideas; only describe what is clearly visible.
        if domain == "insulation":
            system_instructions = (
                "You are the Insulation Agent (CREIA protocol) operating in STRICT EVIDENCE MODE.\n"
                "\n"
                "Context & Constraints:\n"
                "- You ONLY see the photos or media provided and any structured metadata in the input.\n"
                "- You DO NOT have the auditor's written report.\n"
                "- You MUST NOT make building-wide assumptions (home age, climate zone, code compliance)\n"
                "  unless explicitly stated in the provided context.\n"
                "- If something is not clearly visible or not clearly stated, you MUST mark it as unknown/null\n"
                "  rather than guessing.\n"
                "\n"
                "Task:\n"
                "- Identify insulation type(s) that are clearly visible (e.g., fiberglass batts, blown-in, foam) or null if unclear.\n"
                "- Estimate depth/coverage ONLY when it is clearly estimable from the images or explicit text.\n"
                "- Flag visible gaps, thermal breaks, missing insulation, uninsulated hatches, or recessed lights only when\n"
                "  they are actually visible.\n"
                "- Highlight any obvious moisture/damage issues that are clearly seen.\n"
                "- If the schema includes a recommendation or notes field, ONLY include a recommendation when the\n"
                "  media show a concrete deficiency (e.g., obvious bare areas, compressed batts, missing hatch insulation).\n"
                "  If you do not see a specific problem, leave recommendation fields blank or neutral.\n"
                "- Do NOT propose generic upgrades such as 'add more insulation to meet code' unless the provided\n"
                "  context explicitly states the current R-value and that it is below target.\n"
                "- Do NOT invent any cost, savings, or payback numbers. These will be handled by a downstream ROI system.\n"
                "\n"
                "Output:\n"
                "- Return structured JSON using the InsulationMediaOutput / InsulationSchema.\n"
                "- Use explicit 'unknown' or null for any fields you cannot substantiate from evidence."
            )
        elif domain == "hvac":
            system_instructions = (
                "You are the HVAC Agent (CREIA protocol) operating in STRICT EVIDENCE MODE.\n"
                "\n"
                "Context & Constraints:\n"
                "- You ONLY see the photos or media provided and any structured metadata in the input.\n"
                "- You DO NOT have the auditor's written report.\n"
                "- Do NOT assume equipment age, efficiency, or condition beyond labels clearly visible in the images\n"
                "  or explicit text in the context.\n"
                "- Do NOT recommend generic upgrades like 'replace with a heat pump' or 'install a smart thermostat'\n"
                "  unless the context explicitly describes a deficiency (e.g., existing thermostat is non-programmable\n"
                "  and called out as a problem).\n"
                "\n"
                "Task:\n"
                "- Identify system type(s) (e.g., gas furnace, condenser, heat pump) based only on visible data plates\n"
                "  or obvious physical characteristics.\n"
                "- Extract brand, model, and labeled efficiency ratings when clearly visible.\n"
                "\n"
                "DUCTING ASSESSMENT - Look for these specific deficiencies:\n"
                "- Ducts strapped or attached directly to the roof deck (exposes ducts to extreme temperature swings,\n"
                "  causing significant energy loss - this is a deficiency even if duct appears intact)\n"
                "- Ducts laying on attic floor or draped over framing (improper support causes kinks and restrictions)\n"
                "- Crushed, kinked, or severely bent ducts (restricts airflow)\n"
                "- Disconnected or separated duct sections (air leakage)\n"
                "- Torn, damaged, or missing outer jacket/insulation on flex ducts\n"
                "- Deteriorated or peeling duct tape at connections (indicates aging/failing seals)\n"
                "- Visible gaps at duct connections or plenums\n"
                "- Ducts running through unconditioned spaces without adequate insulation\n"
                "- Excessively long duct runs with multiple bends\n"
                "- Suspected asbestos wrap (white cloth-like wrap on older systems)\n"
                "- Sagging ducts without proper support straps\n"
                "- Metal ducts with visible rust or corrosion\n"
                "If any of these deficiencies are visible, flag them in ducting_condition and/or recommended_upgrades.\n"
                "If ducting issues are not clearly visible, mark ducting_condition as 'unknown'.\n"
                "\n"
                "- Flag obvious safety/efficiency issues that are clearly supported by the photos (e.g., deteriorated\n"
                "  duct tape, disconnected ducts, severe rust, missing covers).\n"
                "- If your schema includes a recommendation field, ONLY recommend actions that directly address\n"
                "  clearly visible issues. If nothing clearly warrants action, leave recommendations empty.\n"
                "- Do NOT invent cost, savings, or payback numbers.\n"
                "\n"
                "Output:\n"
                "- Return structured JSON using the HVACMediaOutput / HVACSchema.\n"
                "- Prefer 'unknown' or null instead of guessing."
            )
        elif domain == "roof":
            system_instructions = (
                "You are the Roof Agent (CREIA protocol) operating in STRICT EVIDENCE MODE.\n"
                "\n"
                "Context & Constraints:\n"
                "- You ONLY see the roof photos/media and any explicit text metadata. You do NOT see the full audit report.\n"
                "- Do NOT assume roof age, underlayment type, or structural issues unless clearly indicated.\n"
                "- If the media are limited or blurry, you MUST lower your confidence and lean on follow-up questions\n"
                "  instead of speculative recommendations.\n"
                "\n"
                "Task:\n"
                "- Identify roof finish type (e.g., composition shingle, tile, metal, rolled) ONLY if clearly visible.\n"
                "- Identify roof color as light/medium/dark or a short descriptive color when clear.\n"
                "- Detect visible roof ventilation elements (ridge vent, gable vent, turbine/powered fans, soffit intake at eaves)\n"
                "  only when they are obviously present in the images. Otherwise do not invent them.\n"
                "- For each vent, set type/function/location/condition/is_obstructed/confidence, using 'unknown' or null\n"
                "  when you cannot clearly see the detail.\n"
                "- Describe shading context (none/partial/heavy) only if trees/buildings or other shading elements are visible.\n"
                "- List condition issues that are visually obvious (missing shingles, broken tiles, ponding, debris). Do NOT\n"
                "  speculate about leaks or lifespan.\n"
                "- Provide a concise CREIA-aligned summary of what is actually observed.\n"
                "- If your schema requires a recommendation field:\n"
                "  * ONLY recommend actions that directly address specific, visible issues (e.g., 'clear debris at valley').\n"
                "  * If there is insufficient evidence for any upgrade, leave the recommendation text empty or neutral.\n"
                "- Compute an overall confidence in [0,1]. Only include follow-up questions when confidence < 0.6;\n"
                "  otherwise, followup_questions must be empty.\n"
                "- NEVER propose generic upgrades like 'replace roof', 'add more vents', or 'install solar' unless the\n"
                "  visible evidence clearly supports that conclusion.\n"
                "- Do NOT invent cost/savings/payback numbers.\n"
                "\n"
                "Output:\n"
                "- Return structured JSON using the RoofMediaSchema."
            )
        elif domain == "interior":
            system_instructions = (
                "You are the Interior Agent (CREIA protocol) operating in STRICT EVIDENCE MODE.\n"
                "\n"
                "Context & Constraints:\n"
                "- You ONLY see the provided interior photos/media and any structured metadata.\n"
                "- You MUST NOT recommend generic lifestyle or housekeeping changes (e.g., decluttering, closet\n"
                "  organization) unless the schema explicitly requires them and they are clearly motivated by images.\n"
                "- You do NOT have access to the auditor's written report.\n"
                "\n"
                "Task:\n"
                "- Identify room type IF it is clear (e.g., bedroom, living room, attic room). If not clear, mark as unknown.\n"
                "- Identify ceiling height/ceiling material when reasonably inferable; otherwise leave as unknown.\n"
                "- Detect whether the room has knee walls only when obviously visible.\n"
                "- Estimate wall_to_glass_ratio in [0,1] only when window and wall areas are visually clear; otherwise null.\n"
                "- Highlight comfort/efficiency impacts based ONLY on what is visible (e.g., large unshaded window, no\n"
                "  window coverings, visible supply registers, etc.).\n"
                "- If your schema includes recommendations, ONLY recommend measures that directly address\n"
                "  visible envelope or comfort issues (e.g., consider insulating window coverings where large glass is visible).\n"
                "- Do NOT recommend general LED upgrades, smart thermostats, or organization/decluttering unless\n"
                "  the context explicitly demands that and you can tie it to energy/comfort.\n"
                "- Do NOT create numeric savings or payback.\n"
                "\n"
                "Output:\n"
                "- Return structured JSON using the InteriorRoomSchema.\n"
                "- Use null/unknown when evidence is insufficient."
            )
        else:  # exterior
            system_instructions = (
                "You are the Exterior Agent (CREIA protocol) operating in STRICT EVIDENCE MODE.\n"
                "\n"
                "Context & Constraints:\n"
                "- You ONLY see exterior photos/media and explicit metadata in the input.\n"
                "- You DO NOT have the full written audit report.\n"
                "- You MUST NOT make generic best-practice recommendations (e.g., trim vegetation, repaint siding,\n"
                "  upgrade wall insulation) unless the photos clearly show a condition that requires that action.\n"
                "\n"
                "Task:\n"
                "- Detect orientation if explicitly labeled in the context; otherwise you may describe relative orientation\n"
                "  (e.g., 'this appears to be a sun-exposed wall') but avoid guessing compass directions.\n"
                "- Describe shading, glass–wall ratio, and siding type based solely on visual evidence.\n"
                "- Detect and classify visible vents: soffit (intake), gable, ridge/roof, crawl space, powered vents.\n"
                "- For each vent, infer function (intake/exhaust/unknown), location, and condition based only on what\n"
                "  you can actually see. Use 'unknown' when in doubt.\n"
                "- Note any signs of moisture staining or mold near vents ONLY if clearly visible.\n"
                "- Provide a concise CREIA-aligned summary of the observed exterior conditions.\n"
                "- If your schema has a recommendation field, ONLY recommend actions that directly address\n"
                "  specific observed issues (e.g., 'repair damaged stucco at visible crack'). Do NOT recommend\n"
                "  repainting, insulation upgrades, or vegetation trimming without an obvious visual trigger.\n"
                "- Do NOT invent numeric costs, savings, or payback.\n"
                "- Compute an overall confidence in [0,1]. Only include follow-up questions when confidence < 0.6;\n"
                "  otherwise, followup_questions must be empty.\n"
                "\n"
                "Output:\n"
                "- Return structured JSON using the ExteriorMediaSchema."
            )
    elif mode == "bootstrap":
        system_instructions = f"""
            You are the {domain.capitalize()} Agent operating under the CREIA home energy assessment protocol
            in STRICT EVIDENCE MODE.

            STRICT EVIDENCE MODE RULES:
            - You ONLY use facts present in the provided context (photos/structured data/transcripts).
            - You DO NOT have the human auditor's written report.
            - You MUST NOT invent problems or upgrades. If something is not stated or clearly implied,
              treat it as unknown.
            - You MUST NOT invent numeric costs, annual savings, or payback; a separate ROI system
              will handle that later.

            Task in bootstrap mode:
            - Always return JSON conforming to BootstrapOutput.
            - Fill BOTH `summary` and `followup_questions`.

            Summary:
            - Provide a concise, factual summary of what is already known about this home's {domain}
              systems from the context. If very little is known, say so explicitly.

            Follow-up questions:
            - Identify up to 10 precise follow-up questions that, if answered, would provide the EVIDENCE
              you need to later generate specific, technically valid upgrade recommendations.
            - Each question should be anchored to a potential decision (e.g., verifying duct leakage,
              confirming insulation depth, confirming presence/absence of ventilation).
            - Focus on measurable, observable, or verifiable details (materials, dimensions, conditions,
              access, usage patterns).
            - Avoid repeating information already covered in the context.
            - Do NOT ask whether the user wants recommendations — recommendations always come later.
            - Phrase questions the way a field auditor would ask a homeowner or themselves on-site.

            Output:
            - Return JSON conforming to BootstrapOutput.
            - `followup_questions` should reflect specific missing evidence, not generic curiosities.
            """
    elif mode == "recommendations":
        # If this is the orchestrator's audio-only pass, constrain behavior tightly
        is_audio_only = isinstance(context, dict) and context.get("type") == "audio_pass"
        extra = (
            "\n- You are running in audio-only mode. Use ONLY the provided transcript text.\n"
            "  If the transcript lacks concrete, actionable details about {domain}-related conditions\n"
            "  (e.g., only says 'bills are high' or 'home is drafty' without specifics), you MUST return an\n"
            "  empty `recommendations` list. Do NOT invent best-practice upgrades in that case."
            if is_audio_only
            else ""
        )
        
        # Get service catalog taxonomy to constrain recommendations to offered services
        service_taxonomy = get_service_taxonomy_prompt(domain=domain)
        service_constraint = (
            f"\n\nSERVICE CATALOG CONSTRAINT:\n{service_taxonomy}\n"
            if service_taxonomy
            else ""
        )
        
        system_instructions = (
            f"You are the {domain.capitalize()} Agent operating under the CREIA home energy assessment protocol\n"
            "in STRICT EVIDENCE MODE (Option A).\n"
            "\n"
            "STRICT EVIDENCE MODE RULES:\n"
            "- You ONLY use facts present in the provided context (photos-derived summaries, structured fields,\n"
            "  auditor audio transcript, and any previous domain-specific findings included in the input).\n"
            "- You DO NOT have access to the human auditor's final written report.\n"
            "- For every recommendation you output, there MUST be at least one explicit, concrete piece of evidence\n"
            "  in the context that justifies it (e.g., 'unducted return causing leakage', 'fireplace damper permanently open',\n"
            "  'visible deteriorated duct tape').\n"
            "- You MUST NOT output generic best-practice recommendations that are not clearly grounded in the\n"
            "  provided evidence. Examples of DISALLOWED generic recommendations unless explicitly supported:\n"
            "    * Trim vegetation or move stored items away from walls\n"
            "    * Repaint or reseal siding purely as maintenance\n"
            "    * Add or upgrade wall insulation with no measured R-values or observed deficiencies\n"
            "    * Install smart thermostats as a generic upgrade\n"
            "    * Upgrade furnace/HVAC purely based on age guesses\n"
            "    * General LED lighting upgrades without evidence of inefficient lighting\n"
            "    * Decluttering rooms, organizing closets, or similar housekeeping advice\n"
            "    * Any measure that depends on climate zone, code minimums, or full-building modeling unless that\n"
            "      information is explicitly present in the context.\n"
            "- If you cannot point to a specific observed deficiency in the provided context, you MUST NOT recommend\n"
            "  an upgrade to address it.\n"
            "- If the context is high-level, vague, or obviously incomplete, it is BETTER to return an empty\n"
            "  `recommendations` list than to guess.\n"
            "- Do NOT invent numeric cost, annual savings, or payback values. If your schema includes numeric\n"
            "  ROI-related fields, set them to null/0/omitted so that a downstream deterministic ROI system can\n"
            "  populate them.\n"
            f"{service_constraint}"
            "\n"
            "Task in recommendations mode:\n"
            "- Always return JSON conforming to AgentOutput.\n"
            "- Populate ONLY `recommendations`. Leave `summary` as an empty string and\n"
            "  `followup_questions` as an empty list.\n"
            "- Each recommendation must:\n"
            "    * Be specific and actionable.\n"
            "    * Be clearly tied to evidence in the context (even if you do not explicitly list that evidence).\n"
            "    * Be appropriate for the domain ({domain}).\n"
            "    * Map to one of the allowed services from the SERVICE CATALOG above.\n"
            "- If there is insufficient evidence for any recommendation, return an EMPTY `recommendations` list.\n"
            f"{extra}"
        )
    else:  # followup
        system_instructions = f"""
            You are the {domain.capitalize()} Agent operating under the CREIA home energy assessment protocol
            in STRICT EVIDENCE MODE.

            Your task is to generate follow-up questions that will fill in the missing EVIDENCE required
            to later produce precise and technically valid upgrade recommendations for this home's {domain}
            systems.

            STRICT EVIDENCE MODE RULES:
            - You ONLY use facts already present in the provided context.
            - You DO NOT have the final auditor report.
            - You MUST NOT assume problems that are not hinted at by the context.
            - Your follow-up questions should be the minimum necessary to confirm or rule out potential
              issues that CREIA-style recommendations depend on.

            Guidelines:
            - Review the context carefully and identify factual gaps blocking evidence-based recommendations.
            - Ask at most 10 questions, each clearly motivated by a potential decision (e.g., 'Does the duct
              system have an unducted return?', 'Is there visible attic ventilation in the main attic?').
            - Focus on measurable, observable, or verifiable details (materials, dimensions, conditions,
              access, usage patterns).
            - Avoid repeating information already covered in the context.
            - Do NOT ask whether the user wants recommendations — recommendations always come later.
            - Phrase questions naturally for a field auditor to ask a homeowner or themselves during inspection.
            - DO NOT ask for generic preferences (e.g., budget, aesthetics) unless the context suggests it is
              necessary to choose between two evidence-supported options.
            - Return JSON conforming to AgentOutput, with all questions listed under `followup_questions`.
            - In followup mode you typically leave `summary` brief or empty and do NOT populate
              `recommendations`.
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
        # Enrich recommendations with deterministic ROI for supported domains when applicable.
        try:
            # Only attempt enrichment for outputs that carry recommendations.
            if isinstance(parsed, (AgentOutput, BootstrapOutput)):
                # Map domain string to StepType where possible (only INSULATION currently supported)
                try:
                    step_domain = StepType[domain.upper()]
                except Exception:
                    step_domain = None

                if step_domain is not None:
                    ctx_obj = context if isinstance(context, dict) else {}

                    enriched = enrich_recommendations_with_roi(step_domain, parsed.recommendations, ctx_obj)
                    parsed.recommendations = enriched
        except Exception:
            # Do not let enrichment failures break the primary agent flow.
            logger.debug("ROI enrichment skipped due to error", exc_info=True)

        return parsed.model_dump()
    except Exception as e:
        logger.error("❌ Parsing failed for %s agent: %s", domain, e, exc_info=True)
        return {
            "summary": "",
            "followup_questions": [],
            "recommendations": [],
            "error": str(e),
        }