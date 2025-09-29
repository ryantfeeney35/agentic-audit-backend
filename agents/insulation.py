from .base_agent import run_agent
from .schemas import AgentOutput

def insulation_agent(context: str, bootstrap: bool = False) -> AgentOutput:
    """Run the insulation agent with structured output via LangChain."""
    return run_agent("insulation", context, bootstrap=bootstrap)