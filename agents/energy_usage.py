"""
Energy Usage Agent

Analyzes utility usage data in context with audit observations
to produce evidence-based findings and recommendations.

Operates in STRICT EVIDENCE MODE: All recommendations must be
grounded in actual utility data or observed equipment conditions.
"""

import logging
import json
from typing import Optional, List, Dict, Any
from langchain_core.output_parsers import PydanticOutputParser
from langchain_openai import ChatOpenAI

from .schemas import (
    EnergyUsageAnalysisInput,
    EnergyUsageAgentOutput,
    EnergyUsageFinding,
    EnergyUsageRecommendation,
    EnergyUsageFindingCategory,
    RecommendationType,
    StepType,
)

logger = logging.getLogger(__name__)

# Use same LLM config as other agents
llm = ChatOpenAI(model="gpt-4.1", temperature=0.3)


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

3. TIME-OF-USE OPPORTUNITIES (if TOU data available)
   - Calculate on-peak vs off-peak distribution
   - Identify load-shifting potential
   - Note appliances that could shift (pool pump, EV charging, laundry)

4. ANOMALY DETECTION
   - Flag months with usage >30% above/below trend
   - Look for baseload creep over time
   - Identify potential equipment issues

5. EQUIPMENT CORRELATION
   - Match high usage periods to known HVAC characteristics
   - Consider appliance inventory impact
   - Account for solar production if present

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
) -> EnergyUsageAgentOutput:
    """
    Analyze utility usage data and produce evidence-based findings/recommendations.
    
    Args:
        context: Structured input containing utility data and audit context
        include_service_taxonomy: Whether to include service catalog constraints
        
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
            # Note: annual_savings_usd, upgrade_cost_usd, payback_years
            # will be populated by ROI enrichment system
        }
        
        # Add priority as order_of_completion if provided
        if rec.priority:
            standard_rec["order_of_completion"] = rec.priority
            
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
    ) -> EnergyUsageAgentOutput:
        """
        Run energy usage analysis.
        
        Args:
            context: Structured analysis input
            
        Returns:
            EnergyUsageAgentOutput with findings and recommendations
        """
        return analyze_energy_usage(context)
    
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
