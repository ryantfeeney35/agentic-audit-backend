# agents/orchestrator.py
import logging
from .base_agent import run_agent
from .context_builder import build_audit_context
from .schemas import StepType
from models import AgentConversation, AuditRecommendation, db
import tempfile
import os
import traceback
from openai import OpenAI
from urllib.request import urlopen

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class OrchestratorAgent:
    """Coordinates domain-specific agents (insulation, siding, hvac, etc)."""

    def __init__(self, audit_id: int):
        self.audit_id = audit_id

    # --- Conversation utilities ---
    def _save_message(self, role: str, domain: str, content: str):
        logger.debug("💾 Saving message: role=%s, domain=%s, content=%s", role, domain, content[:300])
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
        context = build_audit_context(self.audit_id)
        logger.info("📄 Context built (len=%d)", len(context))

        outputs = {}
        for domain in ["insulation", "siding", "hvac"]:
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
            final_reply += "\n\nFollow-up Questions:\n- " + "\n- ".join(list(dict.fromkeys(followups)))
        else:
            final_reply += "\n\n✅ No further follow-up questions. Proceed to recommendations."

        self._save_message("assistant", "orchestrator", final_reply)
        logger.info("📝 Bootstrap orchestration complete.")
        return final_reply

    # --- Handle user follow-ups ---
    def handle_user_answer(self, user_answer: str) -> str:
        logger.info("💬 Handling user answer (audit_id=%s)", self.audit_id)
        self._save_message("user", "orchestrator", user_answer)

        full_context = build_audit_context(self.audit_id)
        self._save_message("system", "orchestrator", f"[FULL CONTEXT SNAPSHOT]\n{full_context[:2000]}...")

        history = self._get_history()
        agent_context = f"Conversation so far:\n{history}\n\nLatest user answer:\n{user_answer}"

        outputs = {}
        for domain in ["insulation", "siding", "hvac"]:
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
            final_reply = "Follow-up Questions:\n- " + "\n- ".join(list(dict.fromkeys(followups)))
        else:
            final_reply = "✅ No further follow-up questions. Proceed to recommendations."

        self._save_message("assistant", "orchestrator", final_reply)
        logger.info("📝 Follow-up orchestration complete.")
        return final_reply

    # --- Generate upgrade recommendations ---
    def generate_recommendations(self):
        logger.info("🧮 Generating recommendations (audit_id=%s)", self.audit_id)
        context = build_audit_context(self.audit_id)

        outputs = {}
        for domain in ["insulation", "siding", "hvac"]:
            try:
                outputs[domain] = run_agent(domain, context, audit_id=self.audit_id, mode="recommendations")
            except Exception:
                logger.exception("❌ %s agent failed during recommendations", domain)
                outputs[domain] = {}

        all_recs = []
        for domain, result in outputs.items():
            recs = result.get("recommendations") or []
            for r in recs:
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
            raw_step = rec.get("step_type", domain)

            # Normalize step_type: accept StepType enum members or strings like 'exterior'/'Exterior'/'EXTERIOR'
            def _normalize_step(s):
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
                return str(s)

            step_type = _normalize_step(raw_step)
            r = AuditRecommendation(
                audit_id=self.audit_id,
                step_type=step_type or "General",
                summary=str(summary or ""),
                annual_savings_usd=safe_float(rec.get("annual_savings_usd")),
                upgrade_cost_usd=safe_float(rec.get("upgrade_cost_usd")),
                payback_years=safe_float(rec.get("payback_years")),
            )
            db.session.add(r)
            saved.append(r)
        db.session.commit()

        logger.info("💾 Saved %d recommendations.", len(saved))
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
                client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
                with open(tmp_path, "rb") as fh:
                    transcript = client.audio.transcriptions.create(model="gpt-4o-mini-transcribe", file=fh).text.strip()
                logger.info("process_recommendation_audio: transcription complete (len=%d)", len(transcript) if transcript else 0)
            except Exception as e:
                logger.exception("Transcription failed: %s", e)
                # keep transcript empty on failure
                transcript = ""

            # Choose domain based on recommendation.step_type (normalize to lowercase)
            domain = (rec.step_type or "general").lower()

            # Run the domain agent in media mode with the transcript and the original AI summary as context
            try:
                context = {"type": "audio", "transcript": transcript, "rec_summary": rec.summary}
                logger.info("process_recommendation_audio: calling run_agent domain=%s audit_id=%s", domain, self.audit_id)
                parsed = run_agent(domain, context, audit_id=self.audit_id, mode="media")
                logger.info("process_recommendation_audio: run_agent returned keys=%s", list(parsed.keys()) if isinstance(parsed, dict) else type(parsed))
                refined = None
                if isinstance(parsed, dict):
                    # many media schemas include a `summary` field
                    refined = parsed.get("summary") or parsed.get("refined_text")
                    logger.info("process_recommendation_audio: refined length=%s", len(refined) if refined else 0)
            except Exception as e:
                logger.exception("Agent refinement failed: %s", e)
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