from langchain.prompts import ChatPromptTemplate
from langchain.output_parsers import PydanticOutputParser
from langchain_openai import ChatOpenAI
from .schemas import AgentOutput

llm = ChatOpenAI(model="gpt-4.1", temperature=0.3)

def run_agent(domain: str, context: str, bootstrap: bool = False) -> AgentOutput:
    """Run a domain-specific agent (insulation, siding, hvac) with structured output."""

    parser = PydanticOutputParser(pydantic_object=AgentOutput)
    # Escape braces to avoid KeyError
    format_instructions = parser.get_format_instructions().replace("{", "{{").replace("}", "}}")

    if bootstrap:
        instructions = f"""
        You are the {domain.capitalize()} Agent. Focus ONLY on {domain}.
        - Review provided context.
        - Provide a short summary of findings in `summary`.
        - Provide clear follow-up questions in `followup_questions`.
        - Do not make upgrade recommendations yet.

        {format_instructions}
        """
    else:
        instructions = f"""
        You are the {domain.capitalize()} Agent. Focus ONLY on {domain}.
        - DO NOT summarize.
        - ONLY output NEW follow-up questions in `followup_questions`.
        - If no further questions, return an empty list.

        {format_instructions}
        """

    # Build template (instructions + context)
    prompt = ChatPromptTemplate.from_messages([
        ("system", instructions.strip()),
        ("user", context),
    ])

    # No kwargs substitution — safe now
    resp = llm(prompt.format_messages())
    return parser.parse(resp.content)