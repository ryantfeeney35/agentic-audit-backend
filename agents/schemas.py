# agents/schemas.py
from pydantic import BaseModel, Field
from typing import List, Optional

# -------------------------
# Shared structures
# -------------------------
class Recommendation(BaseModel):
    step_type: str
    summary: str
    annual_savings_usd: Optional[float]
    upgrade_cost_usd: Optional[float]
    payback_years: Optional[float]

# -------------------------
# Domain schemas
# -------------------------
class ExteriorSidingSchema(BaseModel):
    orientation: str
    siding_type: str
    shading: str
    glass_wall_ratio: str
    ea_analysis: str
    summary: str

class HVACSchema(BaseModel):
    system_type: str
    brand: Optional[str]
    model: Optional[str]
    efficiency_rating: Optional[str]
    condition: str
    ducting: Optional[str]
    safety_issues: List[str] = Field(default_factory=list)
    summary: str

class InsulationSchema(BaseModel):
    insulation_type: str
    thickness_inches: Optional[float]
    condition: str
    issues: List[str] = Field(default_factory=list)
    recommended_upgrades: List[str] = Field(default_factory=list)
    summary: str

class InterviewSchema(BaseModel):
    transcript: str
    comfort_issues: List[str] = Field(default_factory=list)
    upgrade_plans: List[str] = Field(default_factory=list)
    summary: str

# -------------------------
# Agent Output (generic wrapper)
# -------------------------
class AgentOutput(BaseModel):
    summary: Optional[str] = Field(None)
    followup_questions: List[str] = Field(default_factory=list)
    recommendations: List[Recommendation] = Field(default_factory=list)

    def json(self) -> str:
        return self.model_dump_json(indent=2)