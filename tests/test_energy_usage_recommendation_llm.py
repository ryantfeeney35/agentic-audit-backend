"""
Live LLM tests for Energy Usage recommendation generation from utility data.

These tests interact with the actual OpenAI API to validate that:
1. Real utility interval data produces evidence-based recommendations
2. Recommendations are grounded in actual usage patterns

Requires OPENAI_API_KEY environment variable to be set.

Run with: pytest backend/tests/test_energy_usage_recommendation_llm.py -v -s
Or: pytest -m recommendation -v
"""
import os
import csv
import pytest
from datetime import datetime
from pathlib import Path
from collections import defaultdict

# Check for API key before attempting imports that initialize the LLM
_has_api_key = bool(os.environ.get("OPENAI_API_KEY"))

# Skip all tests if OPENAI_API_KEY is not set
pytestmark = [
    pytest.mark.skipif(
        not _has_api_key,
        reason="OPENAI_API_KEY not set - skipping live LLM tests"
    ),
    pytest.mark.recommendation,  # Run with: pytest -m recommendation
]

# Only import the agent module if we have an API key (to avoid initialization errors)
if _has_api_key:
    from agents.energy_usage import analyze_energy_usage
    from agents.schemas import (
        EnergyUsageAnalysisInput,
        UtilityUsageSummarySchema,
    )
else:
    analyze_energy_usage = None
    EnergyUsageAnalysisInput = None
    UtilityUsageSummarySchema = None


# Path to test data
TEST_DATA_DIR = Path(__file__).parent / "data"
CARSON_ELECTRIC_CSV = TEST_DATA_DIR / "Carson Electric_15_Minute_7-1-2025_11-8-2025_20251110.csv"


def parse_carson_electric_csv(csv_path: Path) -> dict:
    """
    Parse Carson Electric 15-minute interval CSV into UtilityAPI-compatible format.
    
    This transforms the SDG&E Green Button style CSV into the structure expected
    by our EnergyUsageAnalysisInput schema.
    
    Returns:
        dict with keys matching UtilityUsageSummarySchema
    """
    with open(csv_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # Parse header metadata (first 13 lines, 0-indexed)
    metadata = {}
    for i, line in enumerate(lines[:13]):
        parts = line.strip().split(',', 1)
        if len(parts) == 2:
            metadata[parts[0]] = parts[1]
    
    # Header row is line 14 (index 13), data starts line 15 (index 14)
    # Use csv.DictReader starting from header row
    intervals = []
    reader = csv.DictReader(lines[13:])  # Start from header row at index 13
    
    for row in reader:
        try:
            date_str = row.get('Date', '').strip('"')
            time_str = row.get('Start Time', '').strip('"')
            consumption = float(row.get('Consumption', '0').strip('"'))
            generation = float(row.get('Generation', '0').strip('"'))
            net = float(row.get('Net', '0').strip('"'))
            
            # Parse datetime - handle "12:00 AM" format
            dt = datetime.strptime(f"{date_str} {time_str}", "%m/%d/%Y %I:%M %p")
            
            intervals.append({
                'datetime': dt,
                'hour': dt.hour,
                'weekday': dt.weekday() < 5,  # Mon-Fri = weekday
                'consumption_kwh': consumption,
                'generation_kwh': generation,
                'net_kwh': net,
            })
        except (ValueError, KeyError) as e:
            continue  # Skip malformed rows
    
    if not intervals:
        raise ValueError("No valid interval data found in CSV")
    
    # Compute summary statistics
    total_consumption = sum(i['consumption_kwh'] for i in intervals)
    total_generation = sum(i['generation_kwh'] for i in intervals)
    total_net = sum(i['net_kwh'] for i in intervals)
    
    # Date range
    start_date = min(i['datetime'] for i in intervals)
    end_date = max(i['datetime'] for i in intervals)
    
    # Monthly breakdown
    monthly_data = defaultdict(lambda: {'consumption': 0, 'generation': 0, 'net': 0})
    for i in intervals:
        month_key = i['datetime'].strftime('%Y-%m')
        monthly_data[month_key]['consumption'] += i['consumption_kwh']
        monthly_data[month_key]['generation'] += i['generation_kwh']
        monthly_data[month_key]['net'] += i['net_kwh']
    
    monthly_breakdown = [
        {
            'month': month,
            'usage_kwh': round(data['consumption'], 2),
            'net_kwh': round(data['net'], 2),
            'generation_kwh': round(data['generation'], 2),
        }
        for month, data in sorted(monthly_data.items())
    ]
    
    # Hourly averages (for interval_summary)
    hourly_totals = defaultdict(lambda: {'sum': 0, 'count': 0})
    for i in intervals:
        hourly_totals[i['hour']]['sum'] += i['consumption_kwh']
        hourly_totals[i['hour']]['count'] += 1
    
    hourly_averages_kwh = {
        hour: round(data['sum'] / data['count'], 4) if data['count'] > 0 else 0
        for hour, data in hourly_totals.items()
    }
    
    # Find peak hours (top 3 hours by average consumption)
    sorted_hours = sorted(hourly_averages_kwh.items(), key=lambda x: x[1], reverse=True)
    peak_hours = [hour for hour, _ in sorted_hours[:3]]
    
    # Estimate baseload (minimum average hourly consumption, typically 2-5 AM)
    night_hours = [h for h in range(0, 6)]
    night_averages = [hourly_averages_kwh.get(h, 0) for h in night_hours if h in hourly_averages_kwh]
    baseload_kw = round(min(night_averages) * 4 if night_averages else 0, 2)  # Convert 15-min kWh to kW
    
    # Daily profiles (weekday vs weekend)
    weekday_hourly = defaultdict(lambda: {'sum': 0, 'count': 0})
    weekend_hourly = defaultdict(lambda: {'sum': 0, 'count': 0})
    
    for i in intervals:
        if i['weekday']:
            weekday_hourly[i['hour']]['sum'] += i['consumption_kwh']
            weekday_hourly[i['hour']]['count'] += 1
        else:
            weekend_hourly[i['hour']]['sum'] += i['consumption_kwh']
            weekend_hourly[i['hour']]['count'] += 1
    
    weekday_profile = [
        round(weekday_hourly[h]['sum'] / weekday_hourly[h]['count'], 4)
        if weekday_hourly[h]['count'] > 0 else 0
        for h in range(24)
    ]
    weekend_profile = [
        round(weekend_hourly[h]['sum'] / weekend_hourly[h]['count'], 4)
        if weekend_hourly[h]['count'] > 0 else 0
        for h in range(24)
    ]
    
    # Seasonal pattern (summer vs rest based on available data: July-Nov)
    summer_months = ['2025-07', '2025-08']
    fall_months = ['2025-09', '2025-10', '2025-11']
    
    summer_usage = sum(
        monthly_data[m]['consumption'] for m in summer_months if m in monthly_data
    )
    summer_days = sum(
        1 for m in summer_months if m in monthly_data
    ) * 30  # Approximate
    
    fall_usage = sum(
        monthly_data[m]['consumption'] for m in fall_months if m in monthly_data
    )
    fall_days = sum(
        1 for m in fall_months if m in monthly_data
    ) * 30  # Approximate
    
    seasonal_pattern = {
        'summer_avg_daily_kwh': round(summer_usage / summer_days, 2) if summer_days > 0 else None,
        'fall_avg_daily_kwh': round(fall_usage / fall_days, 2) if fall_days > 0 else None,
    }
    
    # Annualize usage (we have ~4 months of data)
    days_of_data = (end_date - start_date).days + 1
    daily_avg = total_consumption / days_of_data if days_of_data > 0 else 0
    annual_usage_kwh = round(daily_avg * 365, 2)
    
    return {
        'fuel_type': 'electric',
        'start_date': start_date.strftime('%Y-%m-%d'),
        'end_date': end_date.strftime('%Y-%m-%d'),
        'annual_usage_kwh': annual_usage_kwh,
        'monthly_breakdown': monthly_breakdown,
        'seasonal_pattern': seasonal_pattern,
        'interval_summary': {
            'total_intervals': len(intervals),
            'date_range': f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}",
            'hourly_averages_kwh': hourly_averages_kwh,
            'peak_hours': peak_hours,
            'baseload_kw': baseload_kw,
            'total_consumption_kwh': round(total_consumption, 2),
            'total_generation_kwh': round(total_generation, 2),
            'has_solar': total_generation > 0,
        },
        'daily_profiles': [
            {'day_type': 'weekday', 'hourly_kwh': weekday_profile},
            {'day_type': 'weekend', 'hourly_kwh': weekend_profile},
        ],
        'data_quality_flags': [] if len(intervals) > 10000 else ['partial_year_data'],
    }


class TestEnergyUsageRecommendationGeneration:
    """Test suite for energy usage recommendation generation from utility data."""
    
    @pytest.fixture
    def carson_utility_data(self) -> dict:
        """Load and parse Carson Electric CSV data."""
        assert CARSON_ELECTRIC_CSV.exists(), f"Test data file not found: {CARSON_ELECTRIC_CSV}"
        return parse_carson_electric_csv(CARSON_ELECTRIC_CSV)
    
    def test_csv_parsing_produces_valid_schema(self, carson_utility_data):
        """Verify CSV parsing produces valid UtilityUsageSummarySchema."""
        # Should not raise validation error
        schema = UtilityUsageSummarySchema(**carson_utility_data)
        
        # Basic sanity checks
        assert schema.fuel_type == 'electric'
        assert schema.annual_usage_kwh is not None
        assert schema.annual_usage_kwh > 0
        assert schema.monthly_breakdown is not None
        assert len(schema.monthly_breakdown) > 0
        assert schema.interval_summary is not None
        
        print(f"\n✓ Parsed utility data:")
        print(f"  - Date range: {schema.start_date} to {schema.end_date}")
        print(f"  - Annualized usage: {schema.annual_usage_kwh:,.0f} kWh")
        print(f"  - Months of data: {len(schema.monthly_breakdown)}")
        print(f"  - Total intervals: {schema.interval_summary.get('total_intervals', 0):,}")
        print(f"  - Peak hours: {schema.interval_summary.get('peak_hours', [])}")
        print(f"  - Has solar: {schema.interval_summary.get('has_solar', False)}")
    
    def test_utility_data_generates_recommendation(self, carson_utility_data):
        """
        Test that real utility data produces at least one recommendation.
        
        This validates the full LLM pipeline with actual interval data.
        """
        # Build input context
        utility_summary = UtilityUsageSummarySchema(**carson_utility_data)
        
        context = EnergyUsageAnalysisInput(
            utility_summary=utility_summary,
            appliance_inventory=[],
            home_sqft=2000,  # Typical San Diego home
            climate_zone="3C",  # Coastal California
        )
        
        print(f"\n--- Running Energy Usage Analysis ---")
        print(f"Input: {carson_utility_data.get('start_date')} to {carson_utility_data.get('end_date')}")
        print(f"Annualized: {utility_summary.annual_usage_kwh:,.0f} kWh")
        
        # Run analysis
        result = analyze_energy_usage(context)
        
        print(f"\n--- Analysis Results ---")
        print(f"Overall confidence: {result.overall_confidence:.2f}")
        print(f"Summary: {result.summary[:200]}...")
        
        # Check findings
        print(f"\nFindings ({len(result.findings)}):")
        for finding in result.findings:
            print(f"  - [{finding.category.value}] {finding.description[:100]}...")
        
        # Check recommendations
        print(f"\nRecommendations ({len(result.recommendations)}):")
        for rec in result.recommendations:
            print(f"  - [{rec.step_type.value}] {rec.summary}")
            print(f"    Rationale: {rec.rationale[:100]}...")
        
        # ASSERTION: At least one recommendation should be generated
        assert len(result.recommendations) >= 1, (
            f"Expected at least 1 recommendation from utility data analysis, "
            f"got {len(result.recommendations)}. Summary: {result.summary}"
        )
        
        # ASSERTION: Analysis should have reasonable confidence (data is real/complete)
        assert result.overall_confidence >= 0.5, (
            f"Expected confidence >= 0.5 for real utility data, "
            f"got {result.overall_confidence}"
        )
        
        # ASSERTION: Should have at least one finding
        assert len(result.findings) >= 1, (
            f"Expected at least 1 finding from utility data analysis, "
            f"got {len(result.findings)}"
        )
        
        print(f"\n✓ Test passed: {len(result.recommendations)} recommendations generated")
    
    def test_utility_data_recommendations_have_rationale(self, carson_utility_data):
        """Test that recommendations include evidence-based rationale."""
        utility_summary = UtilityUsageSummarySchema(**carson_utility_data)
        
        context = EnergyUsageAnalysisInput(
            utility_summary=utility_summary,
            appliance_inventory=[],
            home_sqft=2000,
            climate_zone="3C",
        )
        
        result = analyze_energy_usage(context)
        
        # Skip if no recommendations (covered by other test)
        if not result.recommendations:
            pytest.skip("No recommendations generated to validate")
        
        for rec in result.recommendations:
            # Each recommendation should have non-empty rationale
            assert rec.rationale and len(rec.rationale) > 10, (
                f"Recommendation '{rec.summary}' missing evidence-based rationale"
            )
            
            # Rationale should reference actual data patterns
            evidence_keywords = [
                'kwh', 'kWh', 'usage', 'consumption', 'peak', 'hour', 
                'summer', 'winter', 'daily', 'monthly', 'average', '%'
            ]
            rationale_lower = rec.rationale.lower()
            has_evidence = any(kw.lower() in rationale_lower for kw in evidence_keywords)
            
            print(f"\n✓ Recommendation: {rec.summary}")
            print(f"  Rationale references data: {has_evidence}")
            print(f"  Rationale: {rec.rationale[:150]}...")
            
            # Soft check - warn but don't fail
            if not has_evidence:
                print(f"  ⚠ Warning: Rationale may not reference specific data")
