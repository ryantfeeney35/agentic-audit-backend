import logging
from langchain.output_parsers import PydanticOutputParser
from langchain_openai import ChatOpenAI
from .schemas import AgentOutput
from models import AgentConversation, db  # only if you want to persist

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

llm = ChatOpenAI(model="gpt-4.1", temperature=0.3)

def run_agent(domain: str, context: str, bootstrap: bool = False, audit_id: int | None = None) -> AgentOutput:
    """Run a domain-specific agent (insulation, siding, hvac) with structured output."""

    parser = PydanticOutputParser(pydantic_object=AgentOutput)

    if bootstrap:
        system_instructions = (
            f"You are the {domain.capitalize()} Agent. Focus ONLY on {domain}.\n"
            "- Review provided context.\n"
            "- Provide a short summary of findings in `summary`.\n"
            "- Provide clear follow-up questions in `followup_questions`.\n"
            "- Do not make upgrade recommendations yet.\n"
        )
    else:
        system_instructions = (
            f"You are the {domain.capitalize()} Agent. Focus ONLY on {domain}.\n"
            "- DO NOT summarize.\n"
            "- ONLY output NEW follow-up questions in `followup_questions`.\n"
            "- If no further questions, return an empty list.\n"
        )

    # ✅ Manually concatenate instructions and parser schema
    system_message = system_instructions + "\n\n" + parser.get_format_instructions()

    # Build final messages
    messages = [
        {"role": "system", "content": system_message},
        {"role": "user", "content": context},
    ]

    # 🔍 Debug log
    logger.debug("=== Running %s Agent ===", domain)
    for msg in messages:
        logger.debug("[%s] %s", msg["role"], msg["content"])

    # Call LLM
    resp = llm.invoke(messages)

    # 🔍 Log raw LLM response
    logger.debug("=== %s Agent Response ===", domain)
    logger.debug(resp.content)

    # Optional: persist to DB
    if audit_id:
        for msg in messages:
            db.session.add(AgentConversation(
                audit_id=audit_id,
                domain=domain,
                role=msg["role"],
                content=msg["content"],
            ))
        db.session.add(AgentConversation(
            audit_id=audit_id,
            domain=domain,
            role="assistant",
            content=resp.content,
        ))
        db.session.commit()

    # Parse into structured object
    return parser.parse(resp.content)