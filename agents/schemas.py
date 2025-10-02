# agents/schemas.py
from pydantic import BaseModel, Field
from typing import List, Optional

class Recommendation(BaseModel):
    step_type: str
    summary: str
    annual_savings_usd: float
    upgrade_cost_usd: float
    payback_years: Optional[float]

class AgentOutput(BaseModel):
    summary: Optional[str] = Field(None)
    followup_questions: List[str] = Field(default_factory=list)
    recommendations: List[Recommendation] = Field(default_factory=list)

    def json(self) -> str:
        return self.model_dump_json(indent=2)