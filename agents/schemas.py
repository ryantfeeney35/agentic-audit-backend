# agents/schemas.py
from enum import Enum
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Any

class StepType(str, Enum):
    EXTERIOR = "Exterior"
    INTERIOR = "Interior"
    HVAC = "HVAC"
    INSULATION = "Insulation"
    ENERGY_USAGE = "Energy Usage"

# -------------------------
# Shared structures
# -------------------------
class Recommendation(BaseModel):
    step_type: StepType
    summary: str
    annual_savings_usd: Optional[float] = None
    upgrade_cost_usd: Optional[float] = None
    payback_years: Optional[float] = None
    source: Optional[str] = None
    # Service catalog alignment fields (populated by post-processing filter)
    service_id: Optional[str] = Field(
        default=None,
        description="ID of the matched service from the catalog (e.g., 'hvac-ducting-repair')",
    )
    order_of_completion: Optional[int] = Field(
        default=None,
        ge=1,
        le=10,
        description="Priority order for work sequencing (lower = do first)",
    )
    rebate_eligible: Optional[bool] = Field(
        default=None,
        description="Whether this service qualifies for rebates",
    )

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
    knee_wall_present: Optional[bool] = Field(
        default=None,
        description="Whether the room has knee walls (short vertical walls at the base of sloped ceilings, common in finished attics).",
    )
    wall_to_glass_ratio: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Estimated ratio of window glass area to total wall area (0..1).",
    )
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
    thickness_inches: Optional[float] = Field(
        default=None,
        description="Estimated insulation thickness in inches, or null if not determinable",
    )
    condition: str
    issues: List[str] = Field(default_factory=list)
    recommended_upgrades: List[str] = Field(default_factory=list)
    summary: str

    @field_validator("thickness_inches", mode="before")
    @classmethod
    def convert_unknown_to_none(cls, v: Any) -> Optional[float]:
        """Convert 'unknown' or other non-numeric strings to None."""
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            # Handle "unknown", "n/a", "not visible", etc.
            v_lower = v.lower().strip()
            if v_lower in ("unknown", "n/a", "na", "none", "not visible", "not determinable", ""):
                return None
            # Try to parse as float
            try:
                return float(v)
            except ValueError:
                return None
        return None

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


# -------------------------
# Energy Usage Agent Schemas
# -------------------------
class EnergyUsageFindingCategory(str, Enum):
    BASELINE = "baseline"          # Overall usage vs typical homes
    SEASONAL = "seasonal"          # Seasonal patterns
    TOU = "tou"                    # Time-of-use opportunities
    ANOMALY = "anomaly"            # Unusual spikes or patterns
    CORRELATION = "correlation"    # Usage correlated with equipment/behavior


class EnergyUsageFinding(BaseModel):
    """A specific finding from energy usage analysis."""
    category: EnergyUsageFindingCategory = Field(
        ...,
        description="Category of finding (baseline, seasonal, tou, anomaly, correlation)",
    )
    description: str = Field(
        ...,
        description="Plain language description of the finding",
    )
    evidence: str = Field(
        ...,
        description="Specific data points or observations supporting this finding",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence in this finding based on data quality/completeness",
    )


class RecommendationType(str, Enum):
    UPGRADE = "upgrade"            # Equipment/envelope upgrades
    BEHAVIOR = "behavior"          # Usage pattern changes


class EnergyUsageRecommendation(BaseModel):
    """A recommendation from energy usage analysis."""
    step_type: StepType = Field(
        ...,
        description="Category for the recommendation (HVAC, Insulation, Energy Usage for behavior changes)",
    )
    recommendation_type: RecommendationType = Field(
        default=RecommendationType.UPGRADE,
        description="Whether this is an upgrade or behavior change recommendation",
    )
    summary: str = Field(
        ...,
        description="Concise recommendation summary",
    )
    rationale: str = Field(
        ...,
        description="Evidence-based justification referencing specific usage data",
    )
    estimated_impact: Optional[str] = Field(
        default=None,
        description="Qualitative impact estimate (e.g., 'Could reduce summer peak by 15-20%')",
    )
    priority: Optional[int] = Field(
        default=None,
        ge=1,
        le=5,
        description="Priority level 1-5 (1=highest)",
    )
    # Note: annual_savings_usd, upgrade_cost_usd, payback_years handled by ROI system


class OccupancyInfo(BaseModel):
    """Information about home occupancy patterns."""
    occupant_count: Optional[int] = Field(default=None, description="Number of occupants")
    daytime_occupied: Optional[bool] = Field(default=None, description="Is someone home during weekdays?")
    work_from_home: Optional[bool] = Field(default=None, description="Do occupants work from home?")
    typical_schedule: Optional[str] = Field(default=None, description="Brief schedule description")


class ApplianceItem(BaseModel):
    """An appliance observed or reported in the home."""
    name: str = Field(..., description="Appliance name (e.g., 'Pool Pump', 'Electric Water Heater')")
    location: Optional[str] = Field(default=None, description="Location in home")
    estimated_age_years: Optional[int] = Field(default=None, description="Estimated age in years")
    condition: Optional[str] = Field(default=None, description="Observed condition")
    usage_pattern: Optional[str] = Field(default=None, description="How frequently used")


class SolarInfo(BaseModel):
    """Information about existing solar installation."""
    has_solar: bool = Field(default=False, description="Whether solar panels are present")
    system_size_kw: Optional[float] = Field(default=None, description="System size in kW")
    annual_production_kwh: Optional[float] = Field(default=None, description="Estimated annual production")
    has_battery: Optional[bool] = Field(default=None, description="Whether battery storage is present")


class UtilityUsageSummarySchema(BaseModel):
    """Schema for utility usage data passed to Energy Usage Agent."""
    fuel_type: str = Field(..., description="electric, gas, or both")
    start_date: Optional[str] = Field(default=None, description="Start of data range (YYYY-MM-DD)")
    end_date: Optional[str] = Field(default=None, description="End of data range (YYYY-MM-DD)")
    annual_usage_kwh: Optional[float] = Field(default=None, description="Total annual usage in kWh")
    annual_cost_usd: Optional[float] = Field(default=None, description="Total annual cost in USD")
    monthly_breakdown: Optional[List[dict]] = Field(
        default=None,
        description="Monthly usage data: [{month, usage_kwh, cost_usd}, ...]",
    )
    seasonal_pattern: Optional[dict] = Field(
        default=None,
        description="Seasonal averages: {summer_avg, winter_avg, shoulder_avg}",
    )
    tou_data: Optional[dict] = Field(
        default=None,
        description="Time-of-use breakdown: {on_peak_pct, off_peak_pct, super_off_peak_pct}",
    )
    data_quality_flags: Optional[List[str]] = Field(
        default=None,
        description="Data quality issues: missing months, estimated reads, etc.",
    )
    # Interval data for granular TOU/load analysis
    interval_summary: Optional[dict] = Field(
        default=None,
        description="Summary of 15-minute interval data: {total_intervals, date_range, hourly_averages, peak_hours, baseload_kw}",
    )
    daily_profiles: Optional[List[dict]] = Field(
        default=None,
        description="Representative daily load profiles: [{day_type: 'weekday'|'weekend', hourly_kwh: [0-23 array]}]",
    )


class EnergyUsageAnalysisInput(BaseModel):
    """Input context for Energy Usage Agent analysis."""
    utility_summary: UtilityUsageSummarySchema = Field(
        ...,
        description="Normalized utility usage data",
    )
    appliance_inventory: List[ApplianceItem] = Field(
        default_factory=list,
        description="Known appliances in the home",
    )
    hvac_summary: Optional[dict] = Field(
        default=None,
        description="HVAC system summary from HVAC agent",
    )
    solar_info: Optional[SolarInfo] = Field(
        default=None,
        description="Existing solar installation info",
    )
    occupancy_info: Optional[OccupancyInfo] = Field(
        default=None,
        description="Occupancy patterns",
    )
    comfort_issues: List[str] = Field(
        default_factory=list,
        description="Reported comfort issues from interview",
    )
    climate_zone: Optional[str] = Field(
        default=None,
        description="Climate zone if known",
    )
    home_sqft: Optional[int] = Field(
        default=None,
        description="Home square footage if known",
    )


class EnergyUsageAgentOutput(BaseModel):
    """Output from Energy Usage Agent analysis."""
    findings: List[EnergyUsageFinding] = Field(
        default_factory=list,
        description="Evidence-based findings about usage patterns",
    )
    recommendations: List[EnergyUsageRecommendation] = Field(
        default_factory=list,
        description="Recommendations grounded in usage data evidence",
    )
    assumptions: List[str] = Field(
        default_factory=list,
        description="Any assumptions made and their basis",
    )
    followup_questions: List[str] = Field(
        default_factory=list,
        description="Questions that would improve analysis if answered",
    )
    overall_confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Overall confidence in analysis based on data completeness",
    )
    summary: str = Field(
        ...,
        description="Executive summary of energy usage analysis",
    )


# -------------------------
# Solar Sizing Schemas (NEM 3.0)
# -------------------------
class SolarSizingInput(BaseModel):
    """Input metrics for solar system sizing calculation.
    
    Derived from 15-minute interval data for accurate NEM 3.0 sizing.
    """
    annual_consumption_kwh: float = Field(
        ...,
        description="Total annual consumption from interval data",
    )
    peak_period_avg_kwh: float = Field(
        ...,
        description="Average consumption during 4-7 PM peak period (kWh/hour)",
    )
    monthly_consumption: Optional[List[float]] = Field(
        default=None,
        description="Monthly consumption totals (12 values)",
    )
    weekday_hourly_avg: Optional[List[float]] = Field(
        default=None,
        description="Hourly averages for weekdays (24 values)",
    )
    weekend_hourly_avg: Optional[List[float]] = Field(
        default=None,
        description="Hourly averages for weekends (24 values)",
    )
    data_coverage_months: float = Field(
        ...,
        ge=0,
        le=12,
        description="Number of months of interval data available",
    )
    property_zip: Optional[str] = Field(
        default=None,
        description="Property zip code for location validation",
    )


class SolarSizingOutput(BaseModel):
    """Output from solar system sizing calculation.
    
    Contains recommended system size and battery capacity under NEM 3.0.
    """
    system_size_kw: float = Field(
        ...,
        description="Recommended solar array size in kW",
    )
    battery_capacity_kwh: float = Field(
        ...,
        description="Recommended battery storage capacity in kWh",
    )
    annual_production_kwh: float = Field(
        ...,
        description="Estimated annual production based on San Diego insolation",
    )
    annual_consumption_kwh: float = Field(
        ...,
        description="Annual consumption from interval data",
    )
    offset_percentage: float = Field(
        ...,
        ge=0,
        le=1.5,
        description="Production / consumption ratio",
    )
    peak_period_avg_kwh: float = Field(
        ...,
        description="Average 4-7 PM consumption used for battery sizing",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence based on data coverage",
    )


class SolarROIOutput(BaseModel):
    """ROI calculation output for solar PPA model.
    
    Compares current utility cost to post-solar cost under NEM 3.0.
    """
    annual_savings_usd: float = Field(
        ...,
        description="Estimated first-year savings",
    )
    current_annual_cost: float = Field(
        ...,
        description="Current annual utility cost under TOU-DR1",
    )
    post_solar_annual_cost: float = Field(
        ...,
        description="Total annual cost with solar (PPA + remaining grid)",
    )
    ppa_annual_cost: float = Field(
        ...,
        description="Annual PPA payment for solar generation",
    )
    grid_annual_cost: float = Field(
        ...,
        description="Annual grid cost after solar offset",
    )
    payback_years: float = Field(
        default=0.0,
        description="Years to payback (0 for PPA model - no upfront cost)",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Interval-Based Solar Optimization Schemas (NEM 3.0 Simulation-Based)
# ──────────────────────────────────────────────────────────────────────────────

class CashRecommendation(BaseModel):
    """Cash purchase path recommendation for solar + battery."""
    
    pv_kw: float = Field(..., description="Optimal PV system size in kW")
    battery_kwh: float = Field(..., description="Optimal battery capacity in kWh")
    irr_percent: float = Field(..., description="Internal rate of return over 25 years")
    payback_years: float = Field(..., description="Simple payback period in years")
    net_cost_usd: float = Field(..., description="System cost after 30% ITC")
    system_cost_usd: float = Field(..., description="Total system cost before incentives")
    year1_savings_usd: float = Field(..., description="First year utility savings")
    post_solar_annual_cost_usd: float = Field(..., description="Annual utility cost with solar")
    self_consumption_percent: float = Field(..., description="Percentage of solar used on-site")
    export_percent: float = Field(..., description="Percentage of solar exported to grid")
    annual_production_kwh: float = Field(..., description="Annual solar production estimate")


class PPARecommendation(BaseModel):
    """PPA path recommendation for solar + battery."""
    
    pv_kw: float = Field(..., description="Optimal PV system size in kW")
    battery_kwh: float = Field(..., description="Optimal battery capacity in kWh")
    monthly_cost_usd: float = Field(..., description="Monthly total cost (PPA + utility)")
    year1_total_cost_usd: float = Field(..., description="Year 1 total cost")
    year1_savings_usd: float = Field(..., description="Year 1 savings vs baseline")
    annual_ppa_cost_usd: float = Field(..., description="Annual PPA payment")
    annual_utility_cost_usd: float = Field(..., description="Annual utility cost with solar")
    self_consumption_percent: float = Field(..., description="Percentage of solar used on-site")
    export_percent: float = Field(..., description="Percentage of solar exported to grid")
    annual_production_kwh: float = Field(..., description="Annual solar production estimate")


class OptimizationDetails(BaseModel):
    """Diagnostic information from solar optimization."""
    
    configurations_evaluated: int = Field(..., description="Number of PV+battery combinations tested")
    runtime_seconds: float = Field(..., description="Optimization runtime")
    paths_aligned: bool = Field(False, description="True if Cash and PPA recommend same system")
    data_coverage_days: int = Field(..., description="Days of interval data used")
    data_confidence: float = Field(..., description="Confidence score based on data coverage (0-1)")


class SolarOptimizationOutput(BaseModel):
    """Output from interval-based solar + battery optimization.
    
    Contains both Cash and PPA path recommendations with detailed metrics.
    """
    # Dual-path recommendations
    cash_recommendation: Optional[CashRecommendation] = Field(
        None,
        description="Cash purchase recommendation (maximize IRR)",
    )
    ppa_recommendation: Optional[PPARecommendation] = Field(
        None,
        description="PPA recommendation (minimize Year-1 cost)",
    )
    
    # Baseline for comparison
    baseline_annual_cost_usd: float = Field(
        ...,
        description="Annual utility cost without solar",
    )
    
    # Interval data summary
    annual_consumption_kwh: float = Field(
        ...,
        description="Total annual consumption from interval data",
    )
    
    # Optimization metadata
    optimization_details: Optional[OptimizationDetails] = Field(
        None,
        description="Diagnostic information from optimization",
    )
    
    # Error handling
    error_message: Optional[str] = Field(
        None,
        description="Error message if optimization failed",
    )
