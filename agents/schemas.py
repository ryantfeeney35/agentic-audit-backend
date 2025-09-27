from pydantic import BaseModel, Field
from typing import List, Optional

class AgentOutput(BaseModel):
    """Structured output from a domain-specific agent."""

    summary: Optional[str] = Field(
        None,
        description="Short summary of findings (only used during bootstrap)."
    )
    followup_questions: List[str] = Field(
        default_factory=list,
        description="List of follow-up questions (empty if none)."
    )

    def json(self) -> str:
        """Return as a JSON string (for saving to DB)."""
        return self.model_dump_json(indent=2)