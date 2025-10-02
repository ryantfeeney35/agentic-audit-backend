from .base_agent import run_agent
from .schemas import AgentOutput

def siding_agent(context: str, bootstrap: bool = False) -> AgentOutput:
    """Run the siding agent with structured output via LangChain."""
    return run_agent("siding", context, bootstrap=bootstrap)