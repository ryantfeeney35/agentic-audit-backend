import logging
from .insulation import insulation_agent
from .siding import siding_agent
from .hvac import hvac_agent
from .utils import call_llm

logger = logging.getLogger("orchestration")
logger.setLevel(logging.DEBUG)

class OrchestratorAgent:
    """Handles coordination across insulation, siding, HVAC agents."""

    def __init__(self, audit_id=None):
        self.audit_id = audit_id

    def run(self, context: str, bootstrap: bool = False) -> str:
        system_prompt = (
            "You are the Orchestrator Agent for a home energy audit.\n"
            "Bootstrap mode:\n"
            "1. Review all context (interview, notes, utility bill, photos).\n"
            "2. Call insulation, siding, and HVAC agents.\n"
            "3. Return ONE unified summary of findings.\n"
            "4. Return ONE unified, deduplicated list of follow-up questions.\n"
            "Do not generate upgrade recommendations yet."
            if bootstrap
            else
            "You are the Orchestrator Agent for a home energy audit.\n"
            "User has provided new answers.\n"
            "Your task:\n"
            "1. Incorporate ONLY new user answers.\n"
            "2. Call insulation, siding, and HVAC agents with updated context.\n"
            "3. Return ONLY an updated, unified, deduplicated list of remaining follow-up questions."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": context},
        ]

        orchestration_reply = call_llm(messages)
        logger.debug(f"🤖 Orchestration raw reply: {orchestration_reply}")

        # Dispatch to domain agents
        agent_replies = []
        if "insulation" in orchestration_reply.lower():
            agent_replies.append(insulation_agent(context, bootstrap))
        if "siding" in orchestration_reply.lower():
            agent_replies.append(siding_agent(context, bootstrap))
        if "hvac" in orchestration_reply.lower():
            agent_replies.append(hvac_agent(context, bootstrap))

        if not agent_replies:
            return "✅ No further follow-up questions. Proceed to recommendations."

        merge_prompt = [
            {
                "role": "system",
                "content": (
                    "You are the Orchestrator Agent merging outputs from domain agents.\n"
                    "Rules:\n"
                    "- On bootstrap: output ONE unified summary AND ONE list of follow-up questions.\n"
                    "- On later turns: output ONLY the unified list of questions.\n"
                    "- If all agents say 'No further ... questions', return:\n"
                    "  '✅ No further follow-up questions. Proceed to recommendations.'"
                ),
            },
            {"role": "user", "content": "\n\n".join(agent_replies)},
        ]
        return call_llm(merge_prompt)