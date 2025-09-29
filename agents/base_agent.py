from langchain.prompts import ChatPromptTemplate
from langchain.output_parsers import PydanticOutputParser
from langchain_openai import ChatOpenAI
from .schemas import AgentOutput

llm = ChatOpenAI(model="gpt-4.1", temperature=0.3)

def run_agent(domain: str, context: str, bootstrap: bool = False) -> AgentOutput:
    """Run a domain-specific agent (insulation, siding, hvac) with structured output."""

    parser = PydanticOutputParser(pydantic_object=AgentOutput)

    if bootstrap:
        instructions = (
            f"You are the {domain.capitalize()} Agent. Focus ONLY on {domain}.\n"
            "- Review provided context.\n"
            "- Provide a short summary of findings in `summary`.\n"
            "- Provide clear follow-up questions in `followup_questions`.\n"
            "- Do not make upgrade recommendations yet.\n"
        )
    else:
        instructions = (
            f"You are the {domain.capitalize()} Agent. Focus ONLY on {domain}.\n"
            "- DO NOT summarize.\n"
            "- ONLY output NEW follow-up questions in `followup_questions`.\n"
            "- If no further questions, return an empty list.\n"
        )

    # ✅ Concatenate parser instructions directly (no formatting!)
    full_instructions = instructions + "\n\n" + parser.get_format_instructions()

    # Build prompt
    prompt = ChatPromptTemplate.from_messages([
        ("system", full_instructions),
        ("user", context),
    ])

    # ✅ No kwargs, no formatting — just get messages
    final_prompt = prompt.format_messages()

    # Call model
    resp = llm(final_prompt)

    # Parse into structured object
    return parser.parse(resp.content)