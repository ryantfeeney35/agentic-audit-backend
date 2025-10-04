# agents/orchestrator.py
import logging
from .base_agent import run_agent
from .context_builder import build_audit_context
from models import AgentConversation, AuditRecommendation, db

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
            step_type = rec.get("step_type", domain)
            r = AuditRecommendation(
                audit_id=self.audit_id,
                step_type=str(step_type or "general"),
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