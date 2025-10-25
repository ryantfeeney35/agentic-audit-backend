from .base_agent import run_agent
from .schemas import AgentOutput

def interior_agent(context: str, bootstrap: bool = False) -> AgentOutput:
    """Run the interior agent with structured output via LangChain."""
    return run_agent("interior", context, bootstrap=bootstrap)