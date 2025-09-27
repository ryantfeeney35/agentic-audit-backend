from .base_agent import run_agent

def insulation_agent(context: str, bootstrap: bool = False):
    """Run the insulation agent with structured output via LangChain."""
    return run_agent("insulation", context, bootstrap=bootstrap)