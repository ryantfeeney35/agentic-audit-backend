from .base_agent import run_agent
from .schemas import AgentOutput

def hvac_agent(context: str, bootstrap: bool = False) -> AgentOutput:
    """Run the HVAC agent with structured output via LangChain."""
    return run_agent("hvac", context, bootstrap=bootstrap)