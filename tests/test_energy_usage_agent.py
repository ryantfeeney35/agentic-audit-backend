"""
Tests for Energy Usage Agent

Tests the Energy Usage Agent's analysis capabilities with
monkeypatched LLM responses to avoid network calls.
"""

import pytest
import json
from unittest.mock import patch, MagicMock
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Set dummy API key before importing modules that need it
os.environ.setdefault('OPENAI_API_KEY', 'test-key-not-used')

from agents.schemas import (
    EnergyUsageAnalysisInput,
    EnergyUsageAgentOutput,
    EnergyUsageFinding,
    EnergyUsageRecommendation,
    EnergyUsageFindingCategory,
    RecommendationType,
    StepType,
    UtilityUsageSummarySchema,
    OccupancyInfo,
    ApplianceItem,
    SolarInfo,
)
from agents.energy_usage import (
    analyze_energy_usage,
    convert_to_standard_recommendations,
    EnergyUsageAgent,
)


# Sample LLM response for mocking
MOCK_LLM_RESPONSE_HIGH_USAGE = """
{
  "findings": [
    {
      "category": "baseline",
      "description": "Annual usage of 12,000 kWh is approximately 30% above average for a 2,000 sqft home in this climate zone.",
      "evidence": "Annual usage: 12,000 kWh, home size: 2,000 sqft, typical usage ~9,000 kWh/year",
      "confidence": 0.85
    },
    {
      "category": "seasonal",
      "description": "Strong cooling-dominant pattern with summer months 2x winter usage.",
      "evidence": "July avg: 1,400 kWh, January avg: 650 kWh, indicating heavy AC reliance",
      "confidence": 0.9
    },
    {
      "category": "tou",
      "description": "62% of usage occurs during on-peak hours, suggesting load-shifting opportunity.",
      "evidence": "TOU data shows on_peak_pct: 62%, off_peak_pct: 38%",
      "confidence": 0.8
    }
  ],
  "recommendations": [
    {
      "step_type": "Energy Usage",
      "recommendation_type": "behavior",
      "summary": "Shift pool pump operation to off-peak hours (after 9 PM)",
      "rationale": "Pool pump identified in appliance inventory; shifting 3-4 hours of daily runtime to off-peak could reduce on-peak percentage significantly",
      "estimated_impact": "Could reduce on-peak usage by 10-15%",
      "priority": 2
    },
    {
      "step_type": "HVAC",
      "recommendation_type": "behavior",
      "summary": "Pre-cool home before peak hours during summer",
      "rationale": "High summer cooling load (1,400 kWh in July) combined with TOU rate structure suggests pre-cooling strategy",
      "estimated_impact": "Could reduce summer peak demand by 15-20%",
      "priority": 1
    }
  ],
  "assumptions": [
    "Pool pump runs approximately 4 hours daily based on typical operation",
    "Home has sufficient thermal mass to benefit from pre-cooling"
  ],
  "followup_questions": [
    "What time does your pool pump currently run?",
    "Do you have a programmable thermostat?"
  ],
  "overall_confidence": 0.85,
  "summary": "Energy usage analysis reveals a cooling-dominant home consuming 30% above typical. Two primary opportunities identified: shifting pool pump to off-peak hours and implementing a pre-cooling strategy during summer months. Both recommendations are behavior changes that don't require equipment upgrades."
}
"""

MOCK_LLM_RESPONSE_SOLAR_HOME = """
{
  "findings": [
    {
      "category": "baseline",
      "description": "Net annual consumption of 4,000 kWh indicates good solar offset.",
      "evidence": "Annual usage: 8,000 kWh, solar production: ~4,000 kWh estimated from 4kW system",
      "confidence": 0.75
    },
    {
      "category": "correlation",
      "description": "Usage spikes align with evening hours when solar production ends.",
      "evidence": "TOU data shows 70% of grid draw during evening peak hours",
      "confidence": 0.8
    }
  ],
  "recommendations": [
    {
      "step_type": "Energy Usage",
      "recommendation_type": "behavior",
      "summary": "Shift high-consumption activities to midday solar production hours",
      "rationale": "With 4kW solar system, midday surplus often exists; running dishwasher, laundry during 10AM-3PM maximizes self-consumption",
      "estimated_impact": "Could increase solar self-consumption by 20-25%",
      "priority": 1
    }
  ],
  "assumptions": [
    "Solar production peaks midday based on typical panel orientation"
  ],
  "followup_questions": [],
  "overall_confidence": 0.78,
  "summary": "Home with solar installation shows good offset but evening grid dependence. Shifting loads to midday hours would maximize solar self-consumption and reduce grid draw during expensive evening peak periods."
}
"""

MOCK_LLM_RESPONSE_MINIMAL_DATA = """
{
  "findings": [
    {
      "category": "baseline",
      "description": "Limited data prevents detailed analysis, but annual usage appears moderate.",
      "evidence": "Only annual total provided: 8,500 kWh, no monthly breakdown",
      "confidence": 0.4
    }
  ],
  "recommendations": [],
  "assumptions": [
    "Unable to identify specific patterns without monthly data"
  ],
  "followup_questions": [
    "Can you provide monthly usage history for the past 12 months?",
    "What is your average summer vs winter electric bill?",
    "Do you have time-of-use rate billing?"
  ],
  "overall_confidence": 0.35,
  "summary": "Insufficient data for detailed energy usage analysis. Annual usage of 8,500 kWh is moderate, but without monthly breakdown, seasonal patterns and optimization opportunities cannot be identified. Recommend importing full utility history."
}
"""


class MockLLMResponse:
    """Mock LLM response object."""
    def __init__(self, content):
        self.content = content


@pytest.fixture
def mock_llm():
    """Patch the LLM to return mock responses."""
    with patch('agents.energy_usage.llm') as mock:
        yield mock


@pytest.fixture
def basic_utility_summary():
    """Basic utility summary for testing."""
    return UtilityUsageSummarySchema(
        fuel_type="electric",
        start_date="2024-01-01",
        end_date="2024-12-31",
        annual_usage_kwh=12000,
        annual_cost_usd=2400,
        monthly_breakdown=[
            {"month": "2024-01", "usage_kwh": 650, "cost_usd": 130},
            {"month": "2024-02", "usage_kwh": 600, "cost_usd": 120},
            {"month": "2024-03", "usage_kwh": 700, "cost_usd": 140},
            {"month": "2024-04", "usage_kwh": 800, "cost_usd": 160},
            {"month": "2024-05", "usage_kwh": 1000, "cost_usd": 200},
            {"month": "2024-06", "usage_kwh": 1300, "cost_usd": 260},
            {"month": "2024-07", "usage_kwh": 1400, "cost_usd": 280},
            {"month": "2024-08", "usage_kwh": 1350, "cost_usd": 270},
            {"month": "2024-09", "usage_kwh": 1100, "cost_usd": 220},
            {"month": "2024-10", "usage_kwh": 850, "cost_usd": 170},
            {"month": "2024-11", "usage_kwh": 650, "cost_usd": 130},
            {"month": "2024-12", "usage_kwh": 600, "cost_usd": 120},
        ],
        seasonal_pattern={"summer_avg": 1250, "winter_avg": 625, "shoulder_avg": 850},
        tou_data={"on_peak_pct": 62, "off_peak_pct": 38},
    )


@pytest.fixture
def analysis_input_with_appliances(basic_utility_summary):
    """Analysis input with appliances."""
    return EnergyUsageAnalysisInput(
        utility_summary=basic_utility_summary,
        appliance_inventory=[
            ApplianceItem(name="Pool Pump", location="backyard", usage_pattern="4 hours daily"),
            ApplianceItem(name="Electric Water Heater", location="garage", estimated_age_years=8),
        ],
        hvac_summary={"system_type": "Central AC", "condition": "Fair", "efficiency_rating": "SEER 14"},
        occupancy_info=OccupancyInfo(occupant_count=4, daytime_occupied=False, work_from_home=False),
        home_sqft=2000,
        climate_zone="3B",
    )


class TestEnergyUsageSchemas:
    """Tests for Energy Usage Agent schemas."""
    
    def test_step_type_includes_energy_usage(self):
        """StepType enum should include ENERGY_USAGE."""
        assert hasattr(StepType, 'ENERGY_USAGE')
        assert StepType.ENERGY_USAGE.value == "Energy Usage"
    
    def test_finding_category_enum(self):
        """EnergyUsageFindingCategory should have expected values."""
        assert EnergyUsageFindingCategory.BASELINE.value == "baseline"
        assert EnergyUsageFindingCategory.SEASONAL.value == "seasonal"
        assert EnergyUsageFindingCategory.TOU.value == "tou"
        assert EnergyUsageFindingCategory.ANOMALY.value == "anomaly"
        assert EnergyUsageFindingCategory.CORRELATION.value == "correlation"
    
    def test_recommendation_type_enum(self):
        """RecommendationType should have upgrade and behavior."""
        assert RecommendationType.UPGRADE.value == "upgrade"
        assert RecommendationType.BEHAVIOR.value == "behavior"
    
    def test_utility_summary_schema_creation(self, basic_utility_summary):
        """Should create valid UtilityUsageSummarySchema."""
        assert basic_utility_summary.fuel_type == "electric"
        assert basic_utility_summary.annual_usage_kwh == 12000
        assert len(basic_utility_summary.monthly_breakdown) == 12
    
    def test_analysis_input_creation(self, analysis_input_with_appliances):
        """Should create valid EnergyUsageAnalysisInput."""
        assert analysis_input_with_appliances.utility_summary.annual_usage_kwh == 12000
        assert len(analysis_input_with_appliances.appliance_inventory) == 2
        assert analysis_input_with_appliances.home_sqft == 2000


class TestAnalyzeEnergyUsage:
    """Tests for analyze_energy_usage function."""
    
    def test_returns_insufficient_data_response_when_no_utility_data(self):
        """Should return low confidence response when utility data missing."""
        input_data = EnergyUsageAnalysisInput(
            utility_summary=UtilityUsageSummarySchema(fuel_type="electric"),
        )
        
        result = analyze_energy_usage(input_data)
        
        assert result.overall_confidence == 0.0
        assert len(result.findings) == 0
        assert len(result.recommendations) == 0
        assert "No utility usage data" in result.assumptions[0]
        assert len(result.followup_questions) > 0
    
    def test_calls_llm_with_valid_data(self, mock_llm, analysis_input_with_appliances):
        """Should invoke LLM with properly formatted context."""
        mock_llm.invoke.return_value = MockLLMResponse(MOCK_LLM_RESPONSE_HIGH_USAGE)
        
        result = analyze_energy_usage(analysis_input_with_appliances, include_service_taxonomy=False)
        
        # Verify LLM was called
        assert mock_llm.invoke.called
        call_args = mock_llm.invoke.call_args[0][0]
        
        # Should have system and user messages
        assert len(call_args) == 2
        assert call_args[0]["role"] == "system"
        assert call_args[1]["role"] == "user"
        
        # System message should contain strict evidence mode
        assert "STRICT EVIDENCE MODE" in call_args[0]["content"]
    
    def test_parses_findings_correctly(self, mock_llm, analysis_input_with_appliances):
        """Should parse LLM response into EnergyUsageAgentOutput."""
        mock_llm.invoke.return_value = MockLLMResponse(MOCK_LLM_RESPONSE_HIGH_USAGE)
        
        result = analyze_energy_usage(analysis_input_with_appliances, include_service_taxonomy=False)
        
        assert isinstance(result, EnergyUsageAgentOutput)
        assert len(result.findings) == 3
        assert result.findings[0].category == EnergyUsageFindingCategory.BASELINE
        assert result.findings[1].category == EnergyUsageFindingCategory.SEASONAL
        assert result.findings[2].category == EnergyUsageFindingCategory.TOU
    
    def test_parses_recommendations_correctly(self, mock_llm, analysis_input_with_appliances):
        """Should parse recommendations with correct types."""
        mock_llm.invoke.return_value = MockLLMResponse(MOCK_LLM_RESPONSE_HIGH_USAGE)
        
        result = analyze_energy_usage(analysis_input_with_appliances, include_service_taxonomy=False)
        
        assert len(result.recommendations) == 2
        
        # First recommendation: pool pump
        pool_rec = result.recommendations[0]
        assert pool_rec.step_type == StepType.ENERGY_USAGE
        assert pool_rec.recommendation_type == RecommendationType.BEHAVIOR
        assert "pool pump" in pool_rec.summary.lower()
        
        # Second recommendation: pre-cooling
        hvac_rec = result.recommendations[1]
        assert hvac_rec.step_type == StepType.HVAC
        assert hvac_rec.recommendation_type == RecommendationType.BEHAVIOR
    
    def test_handles_solar_home_context(self, mock_llm, basic_utility_summary):
        """Should properly handle solar home analysis."""
        mock_llm.invoke.return_value = MockLLMResponse(MOCK_LLM_RESPONSE_SOLAR_HOME)
        
        input_data = EnergyUsageAnalysisInput(
            utility_summary=basic_utility_summary,
            solar_info=SolarInfo(has_solar=True, system_size_kw=4.0),
        )
        
        result = analyze_energy_usage(input_data, include_service_taxonomy=False)
        
        assert result.overall_confidence > 0.7
        assert "solar" in result.summary.lower()
        # Should have solar-aware recommendation
        assert any("solar" in r.summary.lower() or "midday" in r.summary.lower() 
                   for r in result.recommendations)
    
    def test_returns_error_output_on_exception(self, mock_llm, analysis_input_with_appliances):
        """Should return error output when LLM call fails."""
        mock_llm.invoke.side_effect = Exception("API Error")
        
        result = analyze_energy_usage(analysis_input_with_appliances, include_service_taxonomy=False)
        
        assert result.overall_confidence == 0.0
        assert "error" in result.summary.lower()
        assert len(result.recommendations) == 0


class TestConvertToStandardRecommendations:
    """Tests for recommendation conversion to standard format."""
    
    def test_converts_recommendations_to_standard_format(self, mock_llm, analysis_input_with_appliances):
        """Should convert EnergyUsageRecommendation to standard dict format."""
        mock_llm.invoke.return_value = MockLLMResponse(MOCK_LLM_RESPONSE_HIGH_USAGE)
        
        output = analyze_energy_usage(analysis_input_with_appliances, include_service_taxonomy=False)
        standard_recs = convert_to_standard_recommendations(output)
        
        assert len(standard_recs) == 2
        
        for rec in standard_recs:
            assert "step_type" in rec
            assert "summary" in rec
            assert "recommendation_type" in rec
            assert rec["source"] == "energy_usage_agent"
    
    def test_preserves_priority_as_order_of_completion(self, mock_llm, analysis_input_with_appliances):
        """Should map priority to order_of_completion."""
        mock_llm.invoke.return_value = MockLLMResponse(MOCK_LLM_RESPONSE_HIGH_USAGE)
        
        output = analyze_energy_usage(analysis_input_with_appliances, include_service_taxonomy=False)
        standard_recs = convert_to_standard_recommendations(output)
        
        # Find rec with priority 1 (pre-cooling, should be first)
        priority_1 = next((r for r in standard_recs if r.get("order_of_completion") == 1), None)
        assert priority_1 is not None
        assert "pre-cool" in priority_1["summary"].lower()


class TestEnergyUsageAgentClass:
    """Tests for EnergyUsageAgent class interface."""
    
    def test_agent_has_domain_attribute(self):
        """Agent should have domain = 'energy_usage'."""
        agent = EnergyUsageAgent()
        assert agent.domain == "energy_usage"
    
    def test_analyze_method(self, mock_llm, analysis_input_with_appliances):
        """Agent.analyze should call analyze_energy_usage."""
        mock_llm.invoke.return_value = MockLLMResponse(MOCK_LLM_RESPONSE_HIGH_USAGE)
        
        agent = EnergyUsageAgent()
        result = agent.analyze(analysis_input_with_appliances)
        
        assert isinstance(result, EnergyUsageAgentOutput)
        assert len(result.findings) > 0
    
    def test_get_standard_recommendations_method(self, mock_llm, analysis_input_with_appliances):
        """Agent.get_standard_recommendations should return list of dicts."""
        mock_llm.invoke.return_value = MockLLMResponse(MOCK_LLM_RESPONSE_HIGH_USAGE)
        
        agent = EnergyUsageAgent()
        recs = agent.get_standard_recommendations(analysis_input_with_appliances)
        
        assert isinstance(recs, list)
        assert all(isinstance(r, dict) for r in recs)


class TestStrictEvidenceMode:
    """Tests ensuring strict evidence mode compliance."""
    
    def test_low_confidence_with_minimal_data(self, mock_llm):
        """Should have low confidence when data is minimal."""
        mock_llm.invoke.return_value = MockLLMResponse(MOCK_LLM_RESPONSE_MINIMAL_DATA)
        
        input_data = EnergyUsageAnalysisInput(
            utility_summary=UtilityUsageSummarySchema(
                fuel_type="electric",
                annual_usage_kwh=8500,
                # No monthly breakdown
            ),
        )
        
        result = analyze_energy_usage(input_data, include_service_taxonomy=False)
        
        assert result.overall_confidence < 0.5
        assert len(result.recommendations) == 0  # No recommendations without evidence
        assert len(result.followup_questions) > 0
    
    def test_no_recommendations_without_data(self):
        """Should not generate recommendations when no utility data."""
        input_data = EnergyUsageAnalysisInput(
            utility_summary=UtilityUsageSummarySchema(fuel_type="electric"),
        )
        
        result = analyze_energy_usage(input_data)
        
        # Should short-circuit before calling LLM
        assert len(result.recommendations) == 0
        assert result.overall_confidence == 0.0
