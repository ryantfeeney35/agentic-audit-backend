"""
Energy Usage Agent

Analyzes utility usage data in context with audit observations
to produce evidence-based findings and recommendations.

Operates in STRICT EVIDENCE MODE: All recommendations must be
grounded in actual utility data or observed equipment conditions.
"""

import logging
import json
from typing import Optional, List, Dict, Any, TYPE_CHECKING
from langchain_core.output_parsers import PydanticOutputParser
from langchain_openai import ChatOpenAI

if TYPE_CHECKING:
    from .schemas import SolarSizingOutput
    from .roi.solar import SolarROIOutput

from .schemas import (
    EnergyUsageAnalysisInput,
    EnergyUsageAgentOutput,
    EnergyUsageFinding,
    EnergyUsageRecommendation,
    EnergyUsageFindingCategory,
    RecommendationType,
    StepType,
    SolarSizingInput,
)

logger = logging.getLogger(__name__)

# Use same LLM config as other agents
llm = ChatOpenAI(model="gpt-4.1", temperature=0.3)

# Solar recommendation constants
MIN_ANNUAL_KWH_FOR_SOLAR = 3000
SOLAR_SERVICE_ID = "electrical-solar-installation"


def _check_solar_trigger_conditions(
    context: EnergyUsageAnalysisInput,
    interval_count: int,
    property_zip: Optional[str] = None,
) -> bool:
    """
    Check if solar recommendation should be generated.
    
    Conditions (all must be true):
    1. Interval data exists (interval_count > 0)
    2. No existing solar (solar_info.has_solar is False or None)
    3. Property is in California (zip starts with "9")
    4. Annual consumption > 3,000 kWh
    
    Args:
        context: Energy usage analysis input
        interval_count: Number of interval data records available
        property_zip: Property zip code (optional)
        
    Returns:
        True if solar recommendation should be generated
    """
    # Condition 1: Must have interval data
    if interval_count <= 0:
        logger.debug("Solar trigger: No interval data")
        return False
    
    # Condition 2: Must not have existing solar
    if context.solar_info and context.solar_info.has_solar:
        logger.debug("Solar trigger: Existing solar detected")
        return False
    
    # Condition 3: Must be in California (NEM 3.0 specific)
    if property_zip:
        if not property_zip.startswith("9"):
            logger.debug("Solar trigger: Non-California zip code %s", property_zip)
            return False
    
    # Condition 4: Must have sufficient consumption
    annual_kwh = context.utility_summary.annual_usage_kwh or 0
    if annual_kwh < MIN_ANNUAL_KWH_FOR_SOLAR:
        logger.debug(
            "Solar trigger: Low consumption %d kWh (threshold %d)",
            annual_kwh, MIN_ANNUAL_KWH_FOR_SOLAR
        )
        return False
    
    logger.info("Solar trigger: All conditions met, will generate recommendation")
    return True


def _generate_solar_recommendation(
    sizing_output: "SolarSizingOutput",
    roi_output: "SolarROIOutput",
) -> EnergyUsageRecommendation:
    """
    Generate a solar installation recommendation with disclaimers.
    
    Args:
        sizing_output: Solar system sizing results
        roi_output: ROI calculation results
        
    Returns:
        EnergyUsageRecommendation for solar installation
    """
    from .roi.solar import DEFAULT_PPA_RATE
    
    # Build summary with appropriate disclaimers
    summary = (
        f"Install a {sizing_output.system_size_kw:.1f} kW solar panel system with "
        f"{sizing_output.battery_capacity_kwh:.0f} kWh battery storage. "
        f"Estimate based on your actual usage patterns. "
        f"Actual savings depend on roof orientation and shading. "
        f"Professional site assessment recommended."
    )
    
    # Build rationale with data evidence
    rationale = (
        f"Based on {sizing_output.annual_consumption_kwh:,.0f} kWh annual consumption, "
        f"a {sizing_output.system_size_kw:.1f} kW system would produce approximately "
        f"{sizing_output.annual_production_kwh:,.0f} kWh/year ({sizing_output.offset_percentage*100:.0f}% offset). "
        f"With a ${DEFAULT_PPA_RATE:.2f}/kWh PPA, estimated annual savings of ${roi_output.annual_savings_usd:,.0f} "
        f"compared to current ${roi_output.current_annual_cost:,.0f}/year utility cost. "
        f"Battery sized for {sizing_output.peak_period_avg_kwh:.1f} kWh/hr peak (4-7 PM) coverage."
    )
    
    # Qualitative impact estimate
    impact = (
        f"Potential ${roi_output.annual_savings_usd:,.0f}/year savings "
        f"({(roi_output.annual_savings_usd/roi_output.current_annual_cost*100):.0f}% reduction)"
    )
    
    return EnergyUsageRecommendation(
        step_type=StepType.ENERGY_USAGE,
        recommendation_type=RecommendationType.UPGRADE,
        summary=summary,
        rationale=rationale,
        estimated_impact=impact,
        priority=2,  # High priority after critical safety items
    )


def _generate_solar_recommendations_from_optimization(
    opt_result,
    annual_consumption_kwh: float,
) -> List[EnergyUsageRecommendation]:
    """
    Generate solar recommendations from optimization results.
    
    Creates both Cash and PPA path recommendations when available.
    
    Args:
        opt_result: OptimizationResult from simulator
        annual_consumption_kwh: Annual consumption for context
        
    Returns:
        List of EnergyUsageRecommendation objects
    """
    recommendations = []
    
    logger.info(
        "⚡ Solar optimization baseline: $%.2f/yr, consumption: %.0f kWh/yr",
        opt_result.baseline_annual_cost_usd,
        annual_consumption_kwh,
    )
    
    # Generate Cash path recommendation
    if opt_result.cash_optimal:
        cash = opt_result.cash_optimal
        
        logger.info(
            "⚡ Cash path: %.1f kW + %.0f kWh battery, production=%.0f kWh/yr, "
            "cost=$%.0f (net $%.0f), savings=$%.0f/yr, IRR=%.1f%%, payback=%.1f yrs",
            cash["pv_kw"], cash["battery_kwh"], cash["annual_production_kwh"],
            cash["system_cost_usd"], cash["net_cost_usd"],
            cash["year1_savings_usd"], cash["irr_percent"], cash["payback_years"],
        )
        
        battery_text = ""
        if cash["battery_kwh"] > 0:
            battery_text = f" with {cash['battery_kwh']:.0f} kWh battery storage"
        
        summary = (
            f"CASH PURCHASE: Install a {cash['pv_kw']:.1f} kW solar panel system{battery_text}. "
            f"Estimate based on your actual usage patterns. "
            f"Actual savings depend on roof orientation and shading. "
            f"Professional site assessment recommended."
        )
        
        rationale = (
            f"Based on interval-level simulation of {annual_consumption_kwh:,.0f} kWh annual consumption, "
            f"a {cash['pv_kw']:.1f} kW system produces approximately {cash['annual_production_kwh']:,.0f} kWh/year. "
            f"Self-consumption: {cash['self_consumption_percent']:.0f}%, Export: {cash['export_percent']:.0f}%. "
            f"System cost ${cash['system_cost_usd']:,.0f} - 30% ITC = ${cash['net_cost_usd']:,.0f} net. "
            f"Estimated {cash['irr_percent']:.1f}% IRR with {cash['payback_years']:.1f} year payback."
        )
        
        impact = (
            f"Potential ${cash['year1_savings_usd']:,.0f}/year savings "
            f"({cash['year1_savings_usd']/opt_result.baseline_annual_cost_usd*100:.0f}% reduction)"
        )
        
        recommendations.append(EnergyUsageRecommendation(
            step_type=StepType.ENERGY_USAGE,
            recommendation_type=RecommendationType.UPGRADE,
            summary=summary,
            rationale=rationale,
            estimated_impact=impact,
            priority=2,
            annual_savings_usd=cash['year1_savings_usd'],
            upgrade_cost_usd=cash['net_cost_usd'],
            payback_years=cash['payback_years'],
        ))
    
    # Generate PPA path recommendation
    if opt_result.ppa_optimal:
        ppa = opt_result.ppa_optimal
        
        logger.info(
            "⚡ PPA path: %.1f kW + %.0f kWh battery, production=%.0f kWh/yr, "
            "PPA cost=$%.0f/yr, utility cost=$%.0f/yr, savings=$%.0f/yr",
            ppa["pv_kw"], ppa["battery_kwh"], ppa["annual_production_kwh"],
            ppa["annual_ppa_cost_usd"], ppa["annual_utility_cost_usd"],
            ppa["year1_savings_usd"],
        )
        
        battery_text = ""
        if ppa["battery_kwh"] > 0:
            battery_text = f" with {ppa['battery_kwh']:.0f} kWh battery"
        
        summary = (
            f"PPA (NO UPFRONT COST): Install a {ppa['pv_kw']:.1f} kW solar panel system{battery_text}. "
            f"Pay ${ppa['monthly_cost_usd']:.0f}/month total (PPA + utility). "
            f"Estimate based on your actual usage patterns. "
            f"Professional site assessment recommended."
        )
        
        rationale = (
            f"Based on interval-level simulation, a {ppa['pv_kw']:.1f} kW system at $0.18/kWh PPA rate "
            f"produces {ppa['annual_production_kwh']:,.0f} kWh/year at ${ppa['annual_ppa_cost_usd']:,.0f}/year PPA cost. "
            f"Remaining utility cost: ${ppa['annual_utility_cost_usd']:,.0f}/year. "
            f"Self-consumption: {ppa['self_consumption_percent']:.0f}%, Export: {ppa['export_percent']:.0f}%."
        )
        
        impact = (
            f"Potential ${ppa['year1_savings_usd']:,.0f}/year savings vs current ${opt_result.baseline_annual_cost_usd:,.0f}/year"
        )
        
        # PPA has no upfront cost, savings is difference from baseline
        recommendations.append(EnergyUsageRecommendation(
            step_type=StepType.ENERGY_USAGE,
            recommendation_type=RecommendationType.UPGRADE,
            summary=summary,
            rationale=rationale,
            estimated_impact=impact,
            priority=2,
            annual_savings_usd=ppa['year1_savings_usd'],
            upgrade_cost_usd=0.0,  # PPA has no upfront cost
            payback_years=0.0,  # Immediate savings (no payback period)
        ))
    
    return recommendations


ENERGY_USAGE_SYSTEM_PROMPT = """
You are the Energy Usage Agent operating in STRICT EVIDENCE MODE.

Your task is to analyze utility usage data alongside audit observations and produce 
findings and recommendations that are:

1. GROUNDED in the provided utility data (specific months, patterns, anomalies)
2. CORRELATED with observed equipment (HVAC age/type, appliances from inventory)
3. AWARE of solar production if present (avoid contradictory advice)
4. PRIORITIZED by estimated impact and feasibility

═══════════════════════════════════════════════════════════════════════════════
STRICT EVIDENCE MODE RULES
═══════════════════════════════════════════════════════════════════════════════

DO:
✓ Reference specific data points: "July usage was 1,200 kWh, 40% above the annual average"
✓ Correlate patterns with known equipment: "High summer usage aligns with the 15-year-old AC unit"
✓ Note data quality issues: "Analysis limited by 3 missing months of data"
✓ Provide confidence scores based on data completeness
✓ Suggest behavior changes ONLY when usage patterns clearly indicate opportunity

DO NOT:
✗ Recommend generic upgrades without evidence from the data
✗ Assume equipment conditions not described in the input
✗ Duplicate recommendations that other domain agents would make (HVAC, Insulation)
✗ Invent specific cost/savings numbers (ROI system handles this)
✗ Assume solar makes sense without evidence of high usage or good exposure
✗ Make recommendations when utility data is missing or poor quality

═══════════════════════════════════════════════════════════════════════════════
ANALYSIS FRAMEWORK
═══════════════════════════════════════════════════════════════════════════════

1. BASELINE ASSESSMENT
   - Compare annual usage to typical homes of similar size (if sqft provided)
   - Calculate $/kWh to assess rate structure impact
   - Note if usage seems high/low/typical for the climate zone

2. SEASONAL PATTERN ANALYSIS  
   - Identify summer vs winter usage differential
   - Look for heating vs cooling dominant patterns
   - Flag unusual shoulder season spikes

3. TIME-OF-USE & INTERVAL ANALYSIS (PRIORITY when interval_summary available)
   When interval_summary and daily_profiles are provided, USE THEM for:
   - Identify peak hours from hourly_averages_kwh - these are the best TOU targets
   - Compare weekday vs weekend profiles to understand occupancy patterns
   - Calculate baseload_kw - this is always-on consumption (refrigerator, standby, etc.)
   - Look for load-shifting opportunities based on actual usage times
   - Identify specific hours with high consumption for targeted recommendations
   
   Example interval-based insights:
   - "Peak usage occurs at 4-7 PM (0.8 kWh avg) - shifting laundry to 10 PM could save $X"
   - "Baseload of 0.5 kW suggests significant standby power draw"
   - "Weekend morning usage 40% higher than weekday - likely pool pump schedule"

4. ANOMALY DETECTION
   - Flag months with usage >30% above/below trend
   - Look for baseload creep over time
   - Identify potential equipment issues
   - With interval data: identify specific hours/days with unusual spikes

5. EQUIPMENT CORRELATION
   - Match high usage periods to known HVAC characteristics
   - Consider appliance inventory impact
   - Account for solar production if present
   - With interval data: correlate peak hours with expected HVAC runtime

═══════════════════════════════════════════════════════════════════════════════
RECOMMENDATION CATEGORIES
═══════════════════════════════════════════════════════════════════════════════

For step_type assignment:
- HVAC: Thermostat schedules, HVAC operation changes
- Insulation: If usage suggests envelope issues (high heating/cooling)
- Energy Usage: General behavior changes, load shifting, monitoring

For recommendation_type:
- "upgrade": Equipment changes (handled by other agents, rarely from this agent)
- "behavior": Usage pattern/scheduling changes (primary focus of this agent)

═══════════════════════════════════════════════════════════════════════════════
OUTPUT REQUIREMENTS
═══════════════════════════════════════════════════════════════════════════════

Return valid JSON matching EnergyUsageAgentOutput schema:
{
  "findings": [
    {
      "category": "baseline|seasonal|tou|anomaly|correlation",
      "description": "...",
      "evidence": "specific data points",
      "confidence": 0.0-1.0
    }
  ],
  "recommendations": [
    {
      "step_type": "HVAC|Insulation|Energy Usage",
      "recommendation_type": "upgrade|behavior",
      "summary": "...",
      "rationale": "evidence-based justification",
      "estimated_impact": "qualitative impact",
      "priority": 1-5
    }
  ],
  "assumptions": ["any inferences made"],
  "followup_questions": ["questions to improve analysis"],
  "overall_confidence": 0.0-1.0,
  "summary": "executive summary"
}

If utility data is missing or severely incomplete, return:
- Empty findings and recommendations
- overall_confidence near 0
- summary explaining data limitations
- followup_questions asking for utility data
"""


def analyze_energy_usage(
    context: EnergyUsageAnalysisInput,
    include_service_taxonomy: bool = True,
    interval_data: Optional[List] = None,
    property_zip: Optional[str] = None,
) -> EnergyUsageAgentOutput:
    """
    Analyze utility usage data and produce evidence-based findings/recommendations.
    
    Args:
        context: Structured input containing utility data and audit context
        include_service_taxonomy: Whether to include service catalog constraints
        interval_data: Optional list of UtilityIntervalData records for solar sizing
        property_zip: Property zip code for California check
        
    Returns:
        EnergyUsageAgentOutput with findings, recommendations, and confidence
    """
    parser = PydanticOutputParser(pydantic_object=EnergyUsageAgentOutput)
    
    # Check for minimal data requirements
    utility_data = context.utility_summary
    if not utility_data or (not utility_data.annual_usage_kwh and not utility_data.monthly_breakdown):
        logger.warning("Energy Usage Agent: Insufficient utility data provided")
        return EnergyUsageAgentOutput(
            findings=[],
            recommendations=[],
            assumptions=["No utility usage data was provided"],
            followup_questions=[
                "Can you connect your utility account to import usage history?",
                "Do you have 12 months of utility bills we can review?",
                "What is your typical monthly electric bill amount?",
            ],
            overall_confidence=0.0,
            summary="Unable to perform energy usage analysis: No utility data available. "
                    "Please connect your utility account or provide monthly usage history.",
        )
    
    # Build the context message
    context_json = context.model_dump_json(indent=2)
    
    # Optional: Add service taxonomy guidance
    taxonomy_guidance = ""
    if include_service_taxonomy:
        try:
            from .services.prompt_helpers import get_service_taxonomy_prompt
            taxonomy_guidance = f"\n\nSERVICE CATALOG GUIDANCE:\n{get_service_taxonomy_prompt()}"
        except ImportError:
            logger.debug("Service taxonomy not available")
    
    messages = [
        {
            "role": "system",
            "content": ENERGY_USAGE_SYSTEM_PROMPT + taxonomy_guidance + f"\n\n{parser.get_format_instructions()}",
        },
        {
            "role": "user",
            "content": f"Analyze the following energy usage context and provide evidence-based findings and recommendations:\n\n{context_json}",
        },
    ]
    
    try:
        logger.debug("Energy Usage Agent: Invoking LLM")
        response = llm.invoke(messages)
        logger.debug("Energy Usage Agent: LLM response received")
        
        # Parse response
        result = parser.parse(response.content)
        
        # Validate recommendations have proper types
        for rec in result.recommendations:
            if rec.recommendation_type == RecommendationType.BEHAVIOR:
                # Behavior changes should use Energy Usage step type unless clearly HVAC-related
                if rec.step_type not in [StepType.ENERGY_USAGE, StepType.HVAC]:
                    rec.step_type = StepType.ENERGY_USAGE
        
        # Check for solar recommendation opportunity
        interval_count = len(interval_data) if interval_data else 0
        data_days = interval_count // 96  # 96 intervals per day
        
        if _check_solar_trigger_conditions(context, interval_count, property_zip):
            try:
                import numpy as np
                from .roi.simulator import optimize_solar_system
                
                # Convert interval data to numpy array for optimizer
                consumption_intervals = np.array([
                    interval.usage_kwh for interval in interval_data
                ])
                
                # Check minimum data coverage (30 days)
                if data_days < 30:
                    logger.info(
                        "Solar trigger: Insufficient data coverage (%d days, minimum 30)",
                        data_days
                    )
                else:
                    # Run interval-level optimization
                    opt_result = optimize_solar_system(
                        consumption_intervals=consumption_intervals,
                        zip_code=property_zip or "92101",  # Default to SD
                    )
                    
                    if opt_result.error_message:
                        logger.warning("Solar optimization failed: %s", opt_result.error_message)
                    else:
                        # Generate recommendations for valid paths
                        solar_recs = _generate_solar_recommendations_from_optimization(
                            opt_result, context.utility_summary.annual_usage_kwh or 0
                        )
                        result.recommendations.extend(solar_recs)
                        
                        if opt_result.cash_optimal:
                            logger.info(
                                "Solar Cash recommendation: %.1f kW, %.1f%% IRR, $%.0f/yr savings",
                                opt_result.cash_optimal["pv_kw"],
                                opt_result.cash_optimal["irr_percent"],
                                opt_result.cash_optimal["year1_savings_usd"]
                            )
                        if opt_result.ppa_optimal:
                            logger.info(
                                "Solar PPA recommendation: %.1f kW, $%.0f/yr savings",
                                opt_result.ppa_optimal["pv_kw"],
                                opt_result.ppa_optimal["year1_savings_usd"]
                            )
                            
            except Exception as e:
                logger.warning("Failed to generate solar recommendation: %s", e, exc_info=True)
        
        return result
        
    except Exception as e:
        logger.error(f"Energy Usage Agent error: {e}", exc_info=True)
        return EnergyUsageAgentOutput(
            findings=[],
            recommendations=[],
            assumptions=[],
            followup_questions=[],
            overall_confidence=0.0,
            summary=f"Analysis failed due to an error: {str(e)}",
        )


def convert_to_standard_recommendations(
    energy_output: EnergyUsageAgentOutput,
) -> List[Dict[str, Any]]:
    """
    Convert EnergyUsageAgentOutput recommendations to standard Recommendation format
    for merging with other agent outputs.
    
    Args:
        energy_output: Output from energy usage analysis
        
    Returns:
        List of recommendation dicts compatible with standard agent output
    """
    standard_recs = []
    
    for rec in energy_output.recommendations:
        standard_rec = {
            "step_type": rec.step_type.value,
            "summary": rec.summary,
            "recommendation_type": rec.recommendation_type.value,
            "source": "energy_usage_agent",
        }
        
        # Add priority as order_of_completion if provided
        if rec.priority:
            standard_rec["order_of_completion"] = rec.priority
        
        # Add ROI fields if populated (from solar optimization)
        if rec.annual_savings_usd is not None:
            standard_rec["annual_savings_usd"] = rec.annual_savings_usd
        if rec.upgrade_cost_usd is not None:
            standard_rec["upgrade_cost_usd"] = rec.upgrade_cost_usd
        if rec.payback_years is not None:
            standard_rec["payback_years"] = rec.payback_years
            
        standard_recs.append(standard_rec)
    
    return standard_recs


class EnergyUsageAgent:
    """
    Energy Usage Agent class for integration with orchestrator.
    
    Provides consistent interface with other domain agents.
    """
    
    def __init__(self):
        self.domain = "energy_usage"
    
    def analyze(
        self,
        context: EnergyUsageAnalysisInput,
        interval_data: Optional[List] = None,
        property_zip: Optional[str] = None,
    ) -> EnergyUsageAgentOutput:
        """
        Run energy usage analysis.
        
        Args:
            context: Structured analysis input
            interval_data: Optional list of UtilityIntervalData records for solar sizing
            property_zip: Property zip code for California check
            
        Returns:
            EnergyUsageAgentOutput with findings and recommendations
        """
        return analyze_energy_usage(
            context,
            interval_data=interval_data,
            property_zip=property_zip,
        )
    
    def get_standard_recommendations(
        self,
        context: EnergyUsageAnalysisInput,
    ) -> List[Dict[str, Any]]:
        """
        Analyze and return recommendations in standard format.
        
        Args:
            context: Structured analysis input
            
        Returns:
            List of recommendation dicts for merging
        """
        output = self.analyze(context)
        return convert_to_standard_recommendations(output)
