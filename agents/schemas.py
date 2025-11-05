# agents/schemas.py
from enum import Enum
from pydantic import BaseModel, Field
from typing import List, Optional

class StepType(str, Enum):
    EXTERIOR = "Exterior"
    INTERIOR = "Interior"
    HVAC = "HVAC"
    INSULATION = "Insulation"

# -------------------------
# Shared structures
# -------------------------
class Recommendation(BaseModel):
    step_type: StepType
    summary: str
    annual_savings_usd: Optional[float]
    upgrade_cost_usd: Optional[float]
    payback_years: Optional[float]
    source: Optional[str] = None

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

# -------------------------
# Exterior ventilation assessment (composite media schema)
# -------------------------
class VentType(str, Enum):
    SOFFIT = "soffit"
    GABLE = "gable"
    RIDGE = "ridge"
    CRAWL_SPACE = "crawl_space"
    POWERED = "powered"


class VentFunction(str, Enum):
    INTAKE = "intake"
    EXHAUST = "exhaust"
    UNKNOWN = "unknown"


class VentCondition(str, Enum):
    GOOD = "good"
    BLOCKED = "blocked"
    PAINTED_OVER = "painted_over"
    DAMAGED = "damaged"
    MISSING = "missing"
    UNKNOWN = "unknown"


class ExteriorVent(BaseModel):
    type: VentType = Field(..., description="Vent type classification")
    function: VentFunction = Field(..., description="Intake, exhaust, or unknown")
    location: str = Field(..., description="Approx site on exterior: eave/soffit, gable, ridge, crawl space, roof")
    condition: VentCondition = Field(..., description="Observed condition classification")
    notes: Optional[str] = Field(default=None, description="Short factual note if helpful")
    is_obstructed: bool = Field(..., description="True if visibly obstructed/covered/blocked")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Detection confidence 0..1")


class ExteriorVentAssessment(BaseModel):
    detected_vents: List[ExteriorVent] = Field(default_factory=list)
    balance: str = Field(..., description="Assessment of intake/exhaust balance in plain language")
    moisture_signs: List[str] = Field(default_factory=list, description="Observed stains/mold near vents")
    issues: List[str] = Field(default_factory=list, description="Key issues such as blocked/missing/damaged vents")
    confidence: float = Field(..., ge=0.0, le=1.0)
    followup_questions: List[str] = Field(default_factory=list)
    recommendation: str = Field(..., description="CREIA-aligned recommendation text with short rationale")


class ExteriorMediaSchema(BaseModel):
    # Keep original exterior fields for backward compatibility
    orientation: str
    siding_type: str
    shading: str
    glass_wall_ratio: str
    ea_analysis: str
    summary: str

    # New ventilation assessment block
    vent_assessment: ExteriorVentAssessment

class RoofMediaSchema(BaseModel):
    finish_type: str
    color: str
    visible_vents: List[ExteriorVent] = Field(default_factory=list, description="List of visible roof ventilation elements")
    shading: str
    condition_issues: List[str] = Field(default_factory=list)
    summary: str
    followup_questions: List[str] = Field(default_factory=list)
    recommendation: str
    confidence: float = Field(..., ge=0.0, le=1.0)

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
    # Summary is optional in the generic shape to allow recs-only calls,
    # but bootstrap flows should use BootstrapOutput (which requires summary).
    summary: Optional[str] = Field(None)
    followup_questions: List[str] = Field(default_factory=list)
    recommendations: List[Recommendation] = Field(default_factory=list)

    def json(self) -> str:
        return self.model_dump_json(indent=2)

# -------------------------
# Mode-specific stricter outputs
# -------------------------
class BootstrapOutput(BaseModel):
    # In bootstrap, we require a summary to be present.
    summary: str = Field(
        ...,
        description="Concise narrative summary of current domain findings based on context provided",
    )
    # Ask at most 10, but schema allows a list; prompt will enforce limits.
    followup_questions: List[str] = Field(default_factory=list)
    # Keep recommendations field for shape compatibility, but it's not required in bootstrap.
    recommendations: List[Recommendation] = Field(default_factory=list)