"""Tests for solar sizing and ROI calculations."""

import pytest
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

# Import directly to avoid agents/__init__.py which initializes OpenAI client
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.schemas import SolarSizingInput, SolarSizingOutput, SolarROIOutput
from agents.roi.rates import TOU_DR1, EV_TOU_5, TOUPeriod, get_tou_rate, calculate_interval_cost
from agents.roi.solar import (
    extract_interval_metrics,
    calculate_system_size,
    calculate_battery_size,
    size_solar_system,
    calculate_current_annual_cost,
    calculate_post_solar_cost,
    calculate_solar_roi,
)


class TestTOURates:
    """Test TOU rate schedule functionality."""

    def test_tou_dr1_peak_weekday(self):
        """Peak rate on weekday at 5pm."""
        rate = get_tou_rate(17, is_weekend=False, schedule=TOU_DR1)
        assert rate == TOU_DR1.rates[TOUPeriod.ON_PEAK]

    def test_tou_dr1_offpeak_weekday_morning(self):
        """Off-peak rate on weekday at 6am."""
        rate = get_tou_rate(6, is_weekend=False, schedule=TOU_DR1)
        assert rate == TOU_DR1.rates[TOUPeriod.OFF_PEAK]

    def test_tou_dr1_peak_weekend_peak_hour(self):
        """Weekends have same schedule as weekdays."""
        rate = get_tou_rate(17, is_weekend=True, schedule=TOU_DR1)
        assert rate == TOU_DR1.rates[TOUPeriod.ON_PEAK]

    def test_ev_tou_5_super_offpeak(self):
        """Super off-peak at midnight."""
        rate = get_tou_rate(0, is_weekend=False, schedule=EV_TOU_5)
        assert rate == EV_TOU_5.rates[TOUPeriod.SUPER_OFF_PEAK]

    def test_ev_tou_5_peak_weekday(self):
        """Peak at 4pm on weekday."""
        rate = get_tou_rate(16, is_weekend=False, schedule=EV_TOU_5)
        assert rate == EV_TOU_5.rates[TOUPeriod.ON_PEAK]

    def test_calculate_interval_cost(self):
        """Basic interval cost calculation."""
        # 1 kWh at peak rate
        cost = calculate_interval_cost(1.0, 17, False, TOU_DR1)
        assert cost == TOU_DR1.rates[TOUPeriod.ON_PEAK]


class TestSolarSizing:
    """Test solar system sizing calculations."""

    def test_calculate_system_size_basic(self):
        """Basic system size calculation."""
        # 10,000 kWh annual consumption, 85% offset
        # 10000 * 0.85 / 1600 = 5.3125 -> rounds to 5.5 kW
        size = calculate_system_size(10000)
        assert size == 5.5

    def test_calculate_system_size_small(self):
        """Minimum system size returns 0 for below threshold."""
        # 2000 kWh annual -> below MIN_ANNUAL_KWH (3000)
        size = calculate_system_size(2000)
        assert size == 0.0

    def test_calculate_system_size_at_threshold(self):
        """At threshold still produces system."""
        # 3000 kWh annual -> 3000 * 0.85 / 1600 = 1.59 kW -> 2.0 (min system size)
        size = calculate_system_size(3000)
        assert size == 2.0

    def test_calculate_battery_size_basic(self):
        """Basic battery size calculation."""
        # 2 kWh/hr peak average, 3 peak hours, 70% coverage
        # 2 * 3 * 0.7 = 4.2 kWh -> minimum 5.0
        size = calculate_battery_size(2.0)
        assert size == 5.0  # Below min, so returns 5.0

    def test_calculate_battery_size_medium(self):
        """Medium battery size calculation."""
        # 3 kWh/hr peak average, 3 peak hours, 70% coverage
        # 3 * 3 * 0.7 = 6.3 kWh
        size = calculate_battery_size(3.0)
        assert 6.0 <= size <= 6.5

    def test_calculate_battery_size_maximum(self):
        """Maximum battery size cap."""
        # Very high peak usage -> capped at 10 kWh
        size = calculate_battery_size(10.0)
        assert size == 10.0

    def test_size_solar_system(self):
        """Full sizing function."""
        sizing_input = SolarSizingInput(
            annual_consumption_kwh=12000,
            peak_period_avg_kwh=2.5,
            data_coverage_months=8,
        )
        result = size_solar_system(sizing_input)
        
        assert isinstance(result, SolarSizingOutput)
        assert result.system_size_kw >= 2.0  # Minimum system size
        assert result.battery_capacity_kwh >= 5.0
        assert result.battery_capacity_kwh <= 10.0
        assert result.annual_production_kwh > 0
        assert isinstance(result.confidence, float)
        assert 0.0 < result.confidence <= 1.0


def _create_mock_intervals(days: int, base_usage: float = 0.5) -> list:
    """Create mock interval data for testing."""
    mock_intervals = []
    base_time = datetime(2024, 1, 1)
    
    for i in range(96 * days):  # 96 15-min intervals per day
        hour = (i // 4) % 24
        # Higher usage during peak hours
        if 16 <= hour < 21:
            usage = base_usage * 3
        else:
            usage = base_usage
        
        interval_start = base_time + timedelta(minutes=15 * i)
        mock = MagicMock()
        mock.usage_kwh = usage
        mock.interval_start = interval_start
        # Ensure property zip lookup returns None (no audit/property chain)
        mock.audit = None
        mock_intervals.append(mock)
    
    return mock_intervals


class TestROICalculation:
    """Test ROI calculation."""

    def test_calculate_current_annual_cost(self):
        """Estimate current annual cost from intervals."""
        mock_intervals = _create_mock_intervals(30)  # 1 month
        
        cost = calculate_current_annual_cost(mock_intervals)
        # Should annualize and be reasonable (with TOU rates, 12-15k kWh annual = $5k-12k)
        assert 1000 < cost < 15000

    def test_calculate_post_solar_cost(self):
        """Post-solar cost calculation."""
        mock_intervals = _create_mock_intervals(30)  # 1 month
        production_kwh = 9600  # Typical for 6 kW system
        
        ppa_cost, grid_cost, export_credit = calculate_post_solar_cost(
            mock_intervals, production_kwh
        )
        
        # PPA cost = 9600 kWh * $0.32 = $3,072
        assert 3000 < ppa_cost < 3200
        # Grid cost for remaining consumption
        assert grid_cost >= 0
        # Export credit (small due to NEM 3.0 rates)
        assert export_credit >= 0

    def test_calculate_solar_roi(self):
        """Full ROI calculation."""
        sizing_output = SolarSizingOutput(
            system_size_kw=6.0,
            battery_capacity_kwh=7.0,
            annual_production_kwh=9600,
            annual_consumption_kwh=12000,
            offset_percentage=0.80,
            peak_period_avg_kwh=2.5,
            confidence=0.90,
        )
        
        mock_intervals = _create_mock_intervals(240)  # ~8 months
        
        roi = calculate_solar_roi(sizing_output, mock_intervals)
        
        assert isinstance(roi, SolarROIOutput)
        assert roi.annual_savings_usd > 0  # Should have positive savings
        assert roi.current_annual_cost > roi.post_solar_annual_cost


class TestExtractIntervalMetrics:
    """Test interval data extraction."""

    def test_extract_metrics_full_year(self):
        """Extract metrics from full year of data."""
        mock_intervals = _create_mock_intervals(365)  # Full year
        
        result = extract_interval_metrics(mock_intervals)
        
        assert isinstance(result, SolarSizingInput)
        assert result.annual_consumption_kwh > 0
        assert result.peak_period_avg_kwh > 0
        assert result.data_coverage_months >= 11  # Almost full year

    def test_extract_metrics_insufficient_data(self):
        """Short data period still extracts but with low coverage."""
        mock_intervals = _create_mock_intervals(10)  # Only 10 days
        
        result = extract_interval_metrics(mock_intervals)
        # Should return a result but with low coverage
        assert result is not None
        assert result.data_coverage_months < 1


class TestIntegration:
    """Integration tests for full solar workflow."""

    def test_full_solar_recommendation_flow(self):
        """Test complete flow from interval data to ROI."""
        # Create 8 months of mock interval data with lower usage
        # to ensure solar provides positive ROI
        mock_intervals = _create_mock_intervals(240, base_usage=0.4)  # ~8 months
        
        # Step 1: Extract metrics
        sizing_input = extract_interval_metrics(mock_intervals)
        assert sizing_input is not None
        
        # Step 2: Size system
        sizing_output = size_solar_system(sizing_input)
        assert sizing_output is not None
        assert sizing_output.system_size_kw >= 2.0  # Minimum
        
        # Step 3: Calculate ROI
        roi = calculate_solar_roi(sizing_output, mock_intervals)
        
        # Verify ROI calculation produces valid numbers
        assert isinstance(roi.annual_savings_usd, float)
        assert isinstance(roi.current_annual_cost, float)
        assert isinstance(roi.post_solar_annual_cost, float)
        assert roi.current_annual_cost > 0  # Should have some current cost
        assert roi.ppa_annual_cost > 0  # Should have PPA cost
