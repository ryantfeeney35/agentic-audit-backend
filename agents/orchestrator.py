# agents/orchestrator.py
from .base_agent import run_agent
from .context_builder import build_audit_context  # ✅ fixed import
from models import AgentConversation, Audit, db

class OrchestratorAgent:
    """Coordinates domain-specific agents (insulation, siding, hvac, etc)."""

    def __init__(self, audit_id: int):
        self.audit_id = audit_id

    def _save_message(self, role: str, domain: str, content: str):
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
        context = build_audit_context(self.audit_id)  # ✅ updated call

        # Run specialized agents
        insulation_out = run_agent("insulation", context, bootstrap=True)
        siding_out = run_agent("siding", context, bootstrap=True)
        hvac_out = run_agent("hvac", context, bootstrap=True)

        # Merge results
        summary_parts = []
        followup_questions = []

        for agent_out in [insulation_out, siding_out, hvac_out]:
            if agent_out.summary:
                summary_parts.append(agent_out.summary)
            if agent_out.followup_questions:
                followup_questions.extend(agent_out.followup_questions)

        final_reply = "Summary:\n" + "\n".join(summary_parts)
        if followup_questions:
            final_reply += "\n\nFollow-up Questions:\n- " + "\n- ".join(
                list(dict.fromkeys(followup_questions))  # dedupe
            )
        else:
            final_reply += "\n\n✅ No further follow-up questions. Proceed to recommendations."

        self._save_message("assistant", "orchestrator", final_reply)
        return final_reply

    def handle_user_answer(self, user_answer: str) -> str:
        """Handle a new user answer and return updated follow-up questions."""
        self._save_message("user", "orchestrator", user_answer)

        context = build_audit_context(self.audit_id)  # ✅ updated call

        insulation_out = run_agent("insulation", context, bootstrap=False)
        siding_out = run_agent("siding", context, bootstrap=False)
        hvac_out = run_agent("hvac", context, bootstrap=False)

        followup_questions = []
        for agent_out in [insulation_out, siding_out, hvac_out]:
            if agent_out.followup_questions:
                followup_questions.extend(agent_out.followup_questions)

        if followup_questions:
            final_reply = "Follow-up Questions:\n- " + "\n- ".join(
                list(dict.fromkeys(followup_questions))
            )
        else:
            final_reply = "✅ No further follow-up questions. Proceed to recommendations."

        self._save_message("assistant", "orchestrator", final_reply)
        return final_reply