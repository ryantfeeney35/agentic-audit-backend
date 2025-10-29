# agents/schemas.py
from enum import Enum
from pydantic import BaseModel, Field
from typing import List, Optional

class StepType(str, Enum):
    EXTERIOR = "exterior"
    INTERIOR = "interior"
    HVAC = "hvac"
    INSULATION = "insulation"

# -------------------------
# Shared structures
# -------------------------
class Recommendation(BaseModel):
    step_type: StepType
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

class InteriorRoomSchema(BaseModel):
    room_type: str
    ceiling_height: str
    ceiling_material: str
    ea_analysis: str
    summary: str

class HVACSchema(BaseModel):
    # 🔹 Core system details
    system_type: str = Field(
        ...,
        description="Type of HVAC system (e.g. Split Heat Pump, Central AC, Furnace, Mini-Split, Package Unit)",
    )
    fuel_type: Optional[str] = Field(
        default=None,
        description="Primary heating/cooling energy source (Electric, Natural Gas, Propane, etc.)",
    )
    brand: Optional[str] = Field(
        default=None,
        description="Manufacturer or brand name (e.g. Carrier, Trane, Lennox)",
    )
    model: Optional[str] = Field(
        default=None,
        description="Model number or identifier if visible on label",
    )

    # 🔹 Efficiency & condition
    efficiency_rating: Optional[str] = Field(
        default=None,
        description="Efficiency rating or SEER/HSPF/AFUE label (e.g. SEER 16, AFUE 92%)",
    )
    condition: str = Field(
        ...,
        description="Overall observed condition of the HVAC system (Good, Fair, Poor, Inoperative)",
    )

    # 🔹 Ducting & airflow
    ducting_type: Optional[str] = Field(
        default=None,
        description="Type of duct material (Flex, Metal, Ductboard, Unknown)",
    )
    ducting_condition: Optional[str] = Field(
        default=None,
        description="Observed duct condition (Good, Leaking, Poorly Insulated, Damaged, Asbestos Tape)",
    )

    # 🔹 Safety & issues
    safety_issues: List[str] = Field(
        default_factory=list,
        description="List of observed safety or operational concerns (e.g. 'Gas leak', 'Missing disconnect', 'Blocked return')",
    )

    # 🔹 Output
    summary: str = Field(
        ...,
        description="Concise narrative summary of HVAC findings and recommendations",
    )
    recommended_upgrades: List[str] = Field(default_factory=list)

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