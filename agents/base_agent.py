# base_agent.py
from langchain.prompts import ChatPromptTemplate
from langchain.output_parsers import PydanticOutputParser
from langchain_openai import ChatOpenAI
from .schemas import AgentOutput

llm = ChatOpenAI(model="gpt-4.1", temperature=0.3)

def run_agent(domain: str, context: str, bootstrap: bool = False) -> AgentOutput:
    parser = PydanticOutputParser(pydantic_object=AgentOutput)

    if bootstrap:
        instructions = (
            f"You are the {domain.capitalize()} Agent. Focus ONLY on {domain}.\n"
            "- Review provided context.\n"
            "- Provide a short summary of findings in `summary`.\n"
            "- Provide clear follow-up questions in `followup_questions`.\n"
            "- Do not make upgrade recommendations yet.\n\n"
            "{format_instructions}"
        )
    else:
        instructions = (
            f"You are the {domain.capitalize()} Agent. Focus ONLY on {domain}.\n"
            "- DO NOT summarize.\n"
            "- ONLY output NEW follow-up questions in `followup_questions`.\n"
            "- If no further questions, return an empty list.\n\n"
            "{format_instructions}"
        )

    prompt = ChatPromptTemplate.from_messages([
        ("system", instructions),
        ("user", context),
    ])

    # ✅ Escape braces from parser output so str.format doesn’t misinterpret them
    fmt_instructions = parser.get_format_instructions().replace("{", "{{").replace("}", "}}")

    final_prompt = prompt.format_messages(format_instructions=fmt_instructions)

    resp = llm(final_prompt)
    return parser.parse(resp.content)