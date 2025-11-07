# agents/orchestrator.py
import logging
from .base_agent import run_agent
from .context_builder import build_audit_context, build_audio_context, get_audit_memory_context
from .schemas import StepType
from models import AgentConversation, AuditRecommendation, db, AuditMedia, AuditStep, Audit
from memory.config import memory_enabled
from memory import chat_memory as chatmem
from sqlalchemy import func
import tempfile
import os
import traceback
# OpenAI is optional at runtime; guard import for environments without the package
try:
    from openai import OpenAI  # type: ignore
except Exception:  # pragma: no cover - import guard
    OpenAI = None  # type: ignore
from urllib.request import urlopen
from memory.config import semantic_enabled
from memory.semantic import upsert_embeddings_for_audit

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class OrchestratorAgent:
    """Coordinates domain-specific agents (insulation, siding, hvac, etc)."""

    def __init__(self, audit_id: int):
        self.audit_id = audit_id

    # --- Conversation utilities ---
    def _save_message(self, role: str, domain: str, content: str):
        logger.debug("💾 Saving message: role=%s, domain=%s, content=%s", role, domain, content[:300])
        if memory_enabled():
            try:
                chatmem.save_message(self.audit_id, domain, role, content)
                return None
            except Exception:
                pass
        msg = AgentConversation(
            audit_id=self.audit_id,
            role=role,
            domain=domain,
            content=content,
        )
        db.session.add(msg)
        db.session.commit()
        return msg

    def _get_history(self) -> str:
        """Return formatted conversation history excluding orchestrator summaries."""
        # Prefer memory-backed recent messages if enabled
        if memory_enabled():
            try:
                msgs = chatmem.get_recent_messages(self.audit_id, limit=24)
                # Filter orchestrator assistant summaries (assistant role, orchestrator domain)
                filtered = [m for m in msgs if not (m.startswith("[assistant]") and "[orchestrator]" in m)]
                if filtered:
                    return "\n".join(filtered)
            except Exception:
                pass
        rows = (
            AgentConversation.query
            .filter_by(audit_id=self.audit_id)
            .order_by(AgentConversation.created_at.asc())
            .all()
        )
        history_lines = []
        for r in rows:
            if r.domain == "orchestrator" and r.role == "assistant":
                continue
            history_lines.append(f"[{r.role}/{r.domain}] {r.content}")
        return "\n".join(history_lines)

    # --- Bootstrap orchestration ---
    def bootstrap(self) -> str:
        logger.info("🚀 Orchestrator bootstrap started (audit_id=%s)", self.audit_id)
        context = get_audit_memory_context(self.audit_id)
        logger.info("📄 Context built (len=%d)", len(context))

        outputs = {}
        for domain in ["insulation", "siding", "hvac", "interior"]:
            logger.info("➡️ Dispatching bootstrap to %s agent", domain)
            try:
                outputs[domain] = run_agent(domain, context, bootstrap=True, audit_id=self.audit_id)
                logger.debug("✅ %s agent output keys: %s", domain, list(outputs[domain].keys()))
            except Exception:
                logger.exception("❌ %s agent failed during bootstrap", domain)
                outputs[domain] = {}

        # --- Merge results ---
        summary_parts, followups = [], []
        for domain, result in outputs.items():
            if not result:
                continue
            if result.get("summary"):
                summary_parts.append(f"{domain.title()}: {result['summary']}")
            if result.get("followup_questions"):
                followups.extend(result["followup_questions"])

        final_reply = "Summary:\n" + "\n".join(summary_parts or ["No summaries produced."])
        if followups:
            # Dedupe and limit to 10 total follow-up questions
            deduped = list(dict.fromkeys(followups))[:10]
            if deduped:
                final_reply += "\n\nFollow-up Questions:\n- " + "\n- ".join(deduped)
            else:
                final_reply += "\n\n✅ No further follow-up questions. Proceed to recommendations."
        else:
            final_reply += "\n\n✅ No further follow-up questions. Proceed to recommendations."

        self._save_message("assistant", "orchestrator", final_reply)
        logger.info("📝 Bootstrap orchestration complete.")
        return final_reply

    # --- Handle user follow-ups ---
    def handle_user_answer(self, user_answer: str) -> str:
        logger.info("💬 Handling user answer (audit_id=%s)", self.audit_id)
        self._save_message("user", "orchestrator", user_answer)

        memory_context = get_audit_memory_context(self.audit_id)
        self._save_message("system", "orchestrator", f"[FULL CONTEXT SNAPSHOT]\n{memory_context[:2000]}...")

        agent_context = f"{memory_context}\n\nLatest user answer:\n{user_answer}"

        outputs = {}
        for domain in ["insulation", "siding", "hvac", "interior"]:
            logger.info("➡️ Dispatching follow-up to %s agent", domain)
            try:
                outputs[domain] = run_agent(domain, agent_context, bootstrap=False, audit_id=self.audit_id)
                logger.debug("✅ %s agent output keys: %s", domain, list(outputs[domain].keys()))
            except Exception:
                logger.exception("❌ %s agent failed during follow-up", domain)
                outputs[domain] = {}

        followups = []
        for result in outputs.values():
            if result.get("followup_questions"):
                followups.extend(result["followup_questions"])

        if followups:
            # Dedupe and limit to 10 total follow-up questions
            deduped = list(dict.fromkeys(followups))[:10]
            if deduped:
                final_reply = "Follow-up Questions:\n- " + "\n- ".join(deduped)
            else:
                final_reply = "✅ No further follow-up questions. Proceed to recommendations."
        else:
            final_reply = "✅ No further follow-up questions. Proceed to recommendations."

        self._save_message("assistant", "orchestrator", final_reply)
        logger.info("📝 Follow-up orchestration complete.")
        return final_reply

    # --- Generate upgrade recommendations ---
    def generate_recommendations(self):
        logger.info("🧮 Generating recommendations (audit_id=%s)", self.audit_id)
        # Run two ordered passes: (1) audio-only, (2) full-context AI (excluding audio).
        domains = ["insulation", "siding", "hvac", "interior"]

        # --- ROI-aware context scaffolding (minimal change)
        # Pull audit-level defaults and property sqft so ROI enrichment can run deterministically
        try:
            audit_rec = Audit.query.get(self.audit_id)
        except Exception:
            audit_rec = None

        roi_defaults = (audit_rec.roi_defaults if audit_rec and isinstance(audit_rec.roi_defaults, dict) else {})
        property_sqft = None
        try:
            if audit_rec and audit_rec.property and audit_rec.property.sqft:
                property_sqft = float(audit_rec.property.sqft)
        except Exception:
            property_sqft = None

        def _roi_payload(text: str, is_audio: bool = False) -> dict:
            payload = {"text": text}
            if is_audio:
                payload["type"] = "audio_pass"
            # Thread through ROI-related keys expected by enrich_recommendations_with_roi
            if property_sqft is not None:
                payload["property_sqft"] = property_sqft
            try:
                payload["energy_rate_usd_per_kwh"] = float(roi_defaults.get("energy_rate_usd_per_kwh", 0.20))
            except Exception:
                payload["energy_rate_usd_per_kwh"] = 0.20
            try:
                payload["analysis_horizon_years"] = int(roi_defaults.get("analysis_horizon_years", 25))
            except Exception:
                payload["analysis_horizon_years"] = 25
            try:
                payload["climate"] = str(roi_defaults.get("climate", "mild"))
            except Exception:
                payload["climate"] = "mild"
            return payload

        # --- Pass 1: Audio-derived recommendations ---
        audio_context = build_audio_context(self.audit_id)
        logger.info("🔊 Audio pass context length=%d", len(audio_context or ""))
        audio_outputs = {}
        for domain in domains:
            try:
                # Pass a dict with ROI keys so enrichment has inputs available.
                audio_outputs[domain] = run_agent(
                    domain,
                    _roi_payload(audio_context, is_audio=True),
                    audit_id=self.audit_id,
                    mode="recommendations",
                )
            except Exception:
                logger.exception("❌ %s agent failed during audio recommendations", domain)
                audio_outputs[domain] = {}

        # --- Pass 2: Contextual AI recommendations (build full context but explicitly exclude audio-derived summaries) ---
        # Refresh semantic embedding index before building context (Phase 3)
        if semantic_enabled():
            try:
                upsert_embeddings_for_audit(self.audit_id)
            except Exception:
                logger.debug("upsert_embeddings_for_audit skipped due to error", exc_info=True)
        context = get_audit_memory_context(self.audit_id, exclude_audio=True)
        logger.info("🤖 Contextual AI pass context length=%d", len(context or ""))
        context_outputs = {}
        for domain in domains:
            try:
                # Send the structured context inside a dict along with ROI keys.
                context_outputs[domain] = run_agent(
                    domain,
                    _roi_payload(context, is_audio=False),
                    audit_id=self.audit_id,
                    mode="recommendations",
                )
            except Exception:
                logger.exception("❌ %s agent failed during contextual recommendations", domain)
                context_outputs[domain] = {}

        # Merge audio-first then AI context outputs preserving order
        all_recs = []
        for domain in domains:
            for r in (audio_outputs.get(domain, {}) or {}).get("recommendations", []) or []:
                r["_source_pass"] = "audio"
                all_recs.append(r)
        for domain in domains:
            for r in (context_outputs.get(domain, {}) or {}).get("recommendations", []) or []:
                r["_source_pass"] = "ai"
                all_recs.append(r)

        # Coerce numeric fields safely
        def safe_float(val):
            try:
                return float(val)
            except (TypeError, ValueError):
                return None

        # Clear old recs
        AuditRecommendation.query.filter_by(audit_id=self.audit_id).delete()

        saved = []
        for rec in all_recs:
            summary = rec.get("summary", "")
            # Prefer an explicit step_type from the agent output; do not
            # fall back to the outer `domain` variable (which would be the
            # last loop value). If missing, leave it None so normalization
            # can produce a sensible default.
            raw_step = rec.get("step_type")

            # Normalize step_type: accept StepType enum members or strings like 'exterior'/'Exterior'/'EXTERIOR'
            def _normalize_step(s):
                # None -> no step type provided
                if s is None:
                    return None
                # If it's already a StepType enum member, return its value
                try:
                    if isinstance(s, StepType):
                        return s.value
                except Exception:
                    pass
                # If it's a string, try to match by name or value (case-insensitive)
                if isinstance(s, str):
                    s_str = s.strip()
                    # try name lookup (e.g., 'exterior' -> EXTERIOR)
                    try:
                        return StepType[s_str.upper()].value
                    except KeyError:
                        # try matching by value case-insensitively
                        for m in StepType:
                            if m.value.lower() == s_str.lower():
                                return m.value
                    # fallback: title-case the string
                    return s_str.title()
                # any other type: stringify
                try:
                    return str(s)
                except Exception:
                    return None

            step_type = _normalize_step(raw_step)
            # Build the ORM row
            r = AuditRecommendation(
                audit_id=self.audit_id,
                step_type=step_type or "General",
                summary=str(summary or ""),
                annual_savings_usd=safe_float(rec.get("annual_savings_usd")),
                upgrade_cost_usd=safe_float(rec.get("upgrade_cost_usd")),
                payback_years=safe_float(rec.get("payback_years")),
                # Persist source (audio | ai). Use only our internal pass tag to avoid
                # accepting arbitrary freeform 'source' strings that agents may return
                # (agents sometimes use `source` to cite references or URLs). Default to 'ai'.
                source=(rec.get("_source_pass") or "ai"),
            )

            # Opportunistically persist ROI inputs for attic insulation so the UI/API
            # can refine or compute later, even when deterministic enrichment didn't run.
            try:
                if (r.step_type or "").lower() == "insulation" and "attic" in (summary or "").lower():
                    roi_inputs: dict = {}

                    # Derive attic area from property sqft when available (35% heuristic)
                    if property_sqft is not None:
                        try:
                            roi_inputs["attic_area_sqft"] = round(float(property_sqft) * 0.35, 2)
                        except Exception:
                            pass

                    # Prefer explicit fields from the agent output if present
                    try:
                        if rec.get("attic_current_r") is not None:
                            roi_inputs["attic_current_r"] = float(rec.get("attic_current_r"))
                    except Exception:
                        pass
                    try:
                        if rec.get("attic_target_r") is not None:
                            roi_inputs["attic_target_r"] = float(rec.get("attic_target_r"))
                    except Exception:
                        pass

                    # Parse summary for ranges like "R-13 to R-38" or "R13–R38"
                    try:
                        import re
                        rng = re.search(r"\bR[-\s]?(\d+(?:\.\d+)?)\s*(?:to|–|—|-|>)\s*R[-\s]?(\d+(?:\.\d+)?)\b", str(summary), re.IGNORECASE)
                        if rng:
                            cur_v = float(rng.group(1))
                            tgt_v = float(rng.group(2))
                            if "attic_current_r" not in roi_inputs:
                                roi_inputs["attic_current_r"] = cur_v
                            if "attic_target_r" not in roi_inputs:
                                roi_inputs["attic_target_r"] = tgt_v
                    except Exception:
                        pass

                    # Parse explicit mentions like "current R-13" or "existing R-13"
                    try:
                        import re
                        mcur = re.search(r"(?:current|existing)\s*R[-\s]?(\d+(?:\.\d+)?)", str(summary), re.IGNORECASE)
                        if mcur and "attic_current_r" not in roi_inputs:
                            roi_inputs["attic_current_r"] = float(mcur.group(1))
                    except Exception:
                        pass

                    # If only a single R value is present and target not set, assume it refers to target
                    try:
                        import re
                        msingle = re.search(r"\bR[-\s]?(\d+(?:\.\d+)?)\b", str(summary))
                        if msingle and "attic_target_r" not in roi_inputs:
                            roi_inputs["attic_target_r"] = float(msingle.group(1))
                    except Exception:
                        pass

                    # Net cost: prefer explicit field from agent; fallback to existing upgrade_cost_usd
                    try:
                        net_cost_val = rec.get("net_upgrade_cost_usd")
                        if net_cost_val is None:
                            net_cost_val = rec.get("upgrade_cost_usd")
                        if net_cost_val is not None:
                            roi_inputs["net_upgrade_cost_usd"] = float(net_cost_val)
                    except Exception:
                        pass

                    # Audit defaults
                    try:
                        roi_inputs["energy_rate_usd_per_kwh"] = float(roi_defaults.get("energy_rate_usd_per_kwh", 0.20))
                    except Exception:
                        pass
                    try:
                        roi_inputs["analysis_horizon_years"] = int(roi_defaults.get("analysis_horizon_years", 25))
                    except Exception:
                        pass
                    try:
                        roi_inputs["climate"] = str(roi_defaults.get("climate", "mild"))
                    except Exception:
                        pass

                    if roi_inputs:
                        r.roi_inputs = roi_inputs
            except Exception:
                # Non-fatal if roi_inputs hydration fails
                pass
            db.session.add(r)
            saved.append(r)
        db.session.commit()

        logger.info("💾 Saved %d recommendations.", len(saved))
        # --- Phase 3: Auto-associate a suggested photo per recommendation using embeddings.
        # Strategy: compute an embedding for each recommendation summary and pick the
        # AuditMedia item (photo/video) with the highest cosine similarity between
        # the rec embedding and media.ai_embedding.vector. Fallback to the previous
        # 'most recent' heuristic when embeddings are missing or similarity cannot be computed.
        def _cosine(a, b):
            try:
                dot = sum(x * y for x, y in zip(a, b))
                lena = sum(x * x for x in a) ** 0.5
                lenb = sum(y * y for y in b) ** 0.5
                if lena == 0 or lenb == 0:
                    return 0.0
                return dot / (lena * lenb)
            except Exception:
                return 0.0

        try:
            # create OpenAI client for embedding calls (if library available)
            emb_client = OpenAI(api_key=os.getenv('OPENAI_API_KEY')) if OpenAI else None
        except Exception:
            emb_client = None

        for r in saved:
            try:
                rec_text = (r.summary_override or r.summary or "").strip()
                rec_vector = None

                # compute embedding for recommendation text if client available
                if emb_client and rec_text:
                    try:
                        resp = emb_client.embeddings.create(model="text-embedding-3-large", input=rec_text)
                        rec_vector = resp.data[0].embedding if getattr(resp, 'data', None) else None
                    except Exception:
                        rec_vector = None

                best = None
                best_score = -1.0

                # candidate media: photos/videos for this audit and same step_type
                candidates = (
                    AuditMedia.query
                    .join(AuditStep, AuditMedia.step_id == AuditStep.id)
                    .filter(
                        AuditMedia.audit_id == self.audit_id,
                        AuditMedia.media_type.in_(["photo", "video"]),
                        func.lower(AuditStep.step_type) == (r.step_type or "").lower(),
                    )
                    .all()
                )

                if rec_vector and candidates:
                    for m in candidates:
                        try:
                            med_emb = None
                            if isinstance(m.ai_embedding, dict):
                                med_emb = m.ai_embedding.get('vector') or m.ai_embedding.get('embedding')
                            # If ai_embedding stored as list directly
                            if med_emb is None and isinstance(m.ai_embedding, list):
                                med_emb = m.ai_embedding
                            if not med_emb:
                                continue
                            score = _cosine(rec_vector, med_emb)
                            if score > best_score:
                                best_score = score
                                best = m
                        except Exception:
                            continue

                # If we found a best by embeddings, accept it (optionally require a min threshold)
                if best and best_score > 0.0:
                    r.recommended_media_id = best.id
                    r.recommended_media_source = 'suggested'
                    db.session.add(r)
                    continue

                # Fallback to Phase 1 heuristic: most recent media for the same step_type
                try:
                    candidate = (
                        AuditMedia.query
                        .join(AuditStep, AuditMedia.step_id == AuditStep.id)
                        .filter(
                            AuditMedia.audit_id == self.audit_id,
                            AuditMedia.media_type.in_(["photo", "video"]),
                            func.lower(AuditStep.step_type) == (r.step_type or "").lower(),
                        )
                        .order_by(AuditMedia.created_at.desc())
                        .first()
                    )
                    if candidate:
                        r.recommended_media_id = candidate.id
                        r.recommended_media_source = 'suggested'
                        db.session.add(r)
                except Exception:
                    logger.exception("Failed fallback auto-association for recommendation %s", getattr(r, 'id', None))

            except Exception:
                logger.exception("Failed to compute media association for recommendation %s", getattr(r, 'id', None))

        try:
            db.session.commit()
            logger.info("🔗 Auto-associated media for %d recommendations.", len(saved))
        except Exception:
            db.session.rollback()
            logger.exception("Failed to commit media associations for recommendations")

        return saved

    def process_recommendation_audio(self, rec_id: int, media_url: str, local_path: str | None = None) -> dict:
        """Transcribe an uploaded recommendation audio file, run the domain agent to refine it,
        and persist the result to AuditRecommendation.summary_override.

        This method is safe to call asynchronously (it commits its own DB changes).
        Returns a dict with status and summary_override on success.
        """
        try:
            logger.info("process_recommendation_audio: start rec_id=%s audit_id=%s media_url=%s", rec_id, self.audit_id, media_url)
            rec = AuditRecommendation.query.get(rec_id)
            if not rec:
                logger.error("process_recommendation_audio: recommendation %s not found", rec_id)
                return {"status": "error", "error": "recommendation_not_found"}

            if rec.audit_id != self.audit_id:
                logger.error("process_recommendation_audio: audit_id mismatch (expected %s, got %s)", self.audit_id, rec.audit_id)
                return {"status": "error", "error": "audit_id_mismatch"}

            # Ensure we have a local file to transcribe
            tmp_path = local_path
            if not tmp_path:
                # Download the file
                tmp_fd, tmp_path = tempfile.mkstemp(suffix=os.path.basename(media_url))
                os.close(tmp_fd)
                try:
                    resp = urlopen(media_url)
                    with open(tmp_path, "wb") as f:
                        data = resp.read()
                        f.write(data)
                    logger.info("process_recommendation_audio: downloaded media to %s (%d bytes)", tmp_path, len(data))
                except Exception:
                    logger.exception("Failed to download media_url for recommendation audio")
                    return {"status": "error", "error": "download_failed"}

            # Transcribe using OpenAI speech-to-text helper (same model used elsewhere)
            try:
                client = OpenAI(api_key=os.getenv("OPENAI_API_KEY")) if OpenAI else None
                with open(tmp_path, "rb") as fh:
                    if client:
                        transcript = client.audio.transcriptions.create(model="gpt-4o-mini-transcribe", file=fh).text.strip()
                    else:
                        transcript = ""
                logger.info("process_recommendation_audio: transcription complete (len=%d)", len(transcript) if transcript else 0)
            except Exception as e:
                logger.exception("Transcription failed: %s", e)
                # keep transcript empty on failure
                transcript = ""

            # Choose domain based on recommendation.step_type (normalize to lowercase)
            domain = (rec.step_type or "general").lower()

            # Run the domain agent in media mode with the transcript and the original AI summary as context
            try:
                # Use the recommendations mode to ask the agent to professionalize the transcript
                # into a single concise recommendation summary. This avoids media-mode behavior
                # that asks for additional diagnostic details.
                prompt = (
                    "You will be given an auditor's transcript. Your job is to produce ONE concise, "
                    "professional recommendation summary suitable for an audit report. Do NOT ask for more information; "
                    "if details are missing, produce the best conservative recommendation you can from the transcript.\n\n"
                    f"Transcript:\n{transcript}\n\n"
                    f"Existing AI summary (for reference):\n{rec.summary or ''}\n\n"
                    "Return JSON conforming to AgentOutput and populate only `recommendations` with one item."
                )

                logger.info("process_recommendation_audio: calling run_agent (recommendations mode) domain=%s audit_id=%s", domain, self.audit_id)
                parsed = run_agent(domain, prompt, audit_id=self.audit_id, mode="recommendations")
                logger.info("process_recommendation_audio: run_agent returned type=%s", type(parsed))

                refined = None
                if isinstance(parsed, dict):
                    recs_list = parsed.get("recommendations") or []
                    if isinstance(recs_list, list) and len(recs_list) > 0:
                        first = recs_list[0]
                        if isinstance(first, dict):
                            refined = first.get("summary")
                        elif isinstance(first, str):
                            refined = first
                logger.info("process_recommendation_audio: refined length=%s", len(refined) if refined else 0)
            except Exception as e:
                logger.exception("Agent refinement (recommendations mode) failed: %s", e)
                refined = None

            # Persist the override (prefer refined text, fallback to transcript)
            try:
                rec.summary_override = refined or (transcript if transcript else None)
                db.session.commit()
                logger.info("process_recommendation_audio: persisted summary_override for rec_id=%s (len=%s)", rec_id, len(rec.summary_override) if rec.summary_override else 0)
            except Exception as e:
                db.session.rollback()
                logger.exception("Failed to persist summary_override: %s", e)
                return {"status": "error", "error": "db_commit_failed"}

            # cleanup downloaded temp file if we created one
            try:
                if local_path and os.path.exists(local_path):
                    os.remove(local_path)
                    logger.info("process_recommendation_audio: removed temp file %s", local_path)
            except Exception:
                logger.exception("Failed to remove temp audio file: %s", local_path)

            return {"status": "ok", "summary_override": rec.summary_override}

        except Exception as e:
            logger.exception("Unexpected error in process_recommendation_audio: %s", e)
            return {"status": "error", "error": "unexpected", "detail": str(e)}