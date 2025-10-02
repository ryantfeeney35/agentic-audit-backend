# agents/orchestrator.py
import logging
from .base_agent import run_agent
from .context_builder import build_audit_context
from models import AgentConversation, db

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class OrchestratorAgent:
    """Coordinates domain-specific agents (insulation, siding, hvac, etc)."""

    def __init__(self, audit_id: int):
        self.audit_id = audit_id

    def _save_message(self, role: str, domain: str, content: str):
        logger.debug("💾 Saving message: role=%s, domain=%s, content=%s", role, domain, content[:200])
        msg = AgentConversation(
            audit_id=self.audit_id,
            role=role,
            domain=domain,
            content=content,
        )
        db.session.add(msg)
        db.session.commit()
        return msg

    def bootstrap(self) -> str:
        """Initial run: summarize findings + ask follow-up questions."""
        logger.info("🚀 Orchestrator bootstrap started (audit_id=%s)", self.audit_id)

        context = build_audit_context(self.audit_id)
        logger.info("📄 Context built (len=%d)", len(context))

        # Run specialized agents
        outputs = {}
        for domain in ["insulation", "siding", "hvac"]:
            logger.info("➡️ Dispatching bootstrap to %s agent", domain)
            try:
                outputs[domain] = run_agent(domain, context, bootstrap=True, audit_id=self.audit_id)
                logger.info("✅ %s agent returned summary=%s, followups=%s",
                            domain,
                            outputs[domain].summary,
                            outputs[domain].followup_questions)
            except Exception as e:
                logger.exception("❌ %s agent failed during bootstrap", domain)
                outputs[domain] = None

        # Merge results
        summary_parts, followup_questions = [], []
        for domain, agent_out in outputs.items():
            if not agent_out:
                continue
            if agent_out.summary:
                summary_parts.append(agent_out.summary)
            if agent_out.followup_questions:
                followup_questions.extend(agent_out.followup_questions)

        final_reply = "Summary:\n" + "\n".join(summary_parts)
        if followup_questions:
            final_reply += "\n\nFollow-up Questions:\n- " + "\n- ".join(list(dict.fromkeys(followup_questions)))
        else:
            final_reply += "\n\n✅ No further follow-up questions. Proceed to recommendations."

        logger.info("📝 Final orchestrator bootstrap reply built.")
        self._save_message("assistant", "orchestrator", final_reply)
        return final_reply

    def handle_user_answer(self, user_answer: str) -> str:
        """Handle a new user answer and return updated follow-up questions."""
        logger.info("💬 Orchestrator handling user answer (audit_id=%s)", self.audit_id)
        self._save_message("user", "orchestrator", user_answer)

        context = build_audit_context(self.audit_id)
        logger.info("📄 Context rebuilt (len=%d)", len(context))

        outputs = {}
        for domain in ["insulation", "siding", "hvac"]:
            logger.info("➡️ Dispatching follow-up to %s agent", domain)
            try:
                outputs[domain] = run_agent(domain, context, bootstrap=False, audit_id=self.audit_id)
                logger.info("✅ %s agent followups=%s", domain, outputs[domain].followup_questions)
            except Exception as e:
                logger.exception("❌ %s agent failed during follow-up", domain)
                outputs[domain] = None

        followup_questions = []
        for agent_out in outputs.values():
            if agent_out and agent_out.followup_questions:
                followup_questions.extend(agent_out.followup_questions)

        if followup_questions:
            final_reply = "Follow-up Questions:\n- " + "\n- ".join(list(dict.fromkeys(followup_questions)))
        else:
            final_reply = "✅ No further follow-up questions. Proceed to recommendations."

        logger.info("📝 Final orchestrator follow-up reply built.")
        self._save_message("assistant", "orchestrator", final_reply)
        return final_reply