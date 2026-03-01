"""
Unit tests for financial calculations module.
Tests tasks 5.6 and 6.4.
"""
import pytest
from agents.roi.financial import (
    calculate_system_cost,
    calculate_net_cost_after_itc,
    calculate_irr,
    calculate_npv,
    calculate_simple_payback,
    calculate_cash_path,
    calculate_ppa_path,
    SOLAR_COST_PER_WATT,
    BATTERY_COST_PER_KWH,
    ITC_RATE,
    PPA_RATE_PER_KWH,
    CASH_ANALYSIS_YEARS,
)


class TestSystemCost:
    """Tests for system cost calculations."""

    def test_solar_only_cost(self):
        """PV-only system cost calculation."""
        result = calculate_system_cost(pv_kw=6.0, battery_kwh=0)
        expected = 6.0 * 1000 * SOLAR_COST_PER_WATT  # 6000W * $2.50/W = $15,000
        assert result["total_cost_usd"] == expected
        assert result["solar_cost_usd"] == expected
        assert result["battery_cost_usd"] == 0

    def test_solar_plus_battery_cost(self):
        """PV + battery system cost."""
        result = calculate_system_cost(pv_kw=8.0, battery_kwh=10.0)
        pv_cost = 8.0 * 1000 * SOLAR_COST_PER_WATT  # $20,000
        batt_cost = 10.0 * BATTERY_COST_PER_KWH      # $5,000
        assert result["total_cost_usd"] == pv_cost + batt_cost

    def test_zero_system(self):
        """Zero system should cost zero."""
        result = calculate_system_cost(pv_kw=0, battery_kwh=0)
        assert result["total_cost_usd"] == 0


class TestITCCalculation:
    """Tests for Investment Tax Credit application."""

    def test_itc_reduction(self):
        """ITC should reduce cost by 30%."""
        gross_cost = 15000
        net_cost = calculate_net_cost_after_itc(gross_cost)
        expected = gross_cost * (1 - ITC_RATE)  # $15,000 * 0.7 = $10,500
        assert net_cost == expected

    def test_itc_zero_cost(self):
        """ITC on zero cost should be zero."""
        net_cost = calculate_net_cost_after_itc(0)
        assert net_cost == 0


class TestIRRCalculation:
    """Tests for Internal Rate of Return calculation."""

    def test_positive_irr(self):
        """Should calculate positive IRR for profitable investment."""
        # $10,000 investment with $1,500/year savings for 25 years
        irr = calculate_irr(
            net_cost=10000,
            annual_savings=1500,
            years=25
        )
        assert irr is not None
        assert 10 < irr < 20  # Should be around 14%

    def test_negative_irr(self):
        """Should return low/negative IRR when savings < cost recovery."""
        # $20,000 investment with only $200/year savings
        irr = calculate_irr(
            net_cost=20000,
            annual_savings=200,
            years=25
        )
        # IRR should be very low or negative
        assert irr is not None
        assert irr < 5

    def test_zero_investment(self):
        """Zero investment should return 100% (infinite return)."""
        irr = calculate_irr(
            net_cost=0,
            annual_savings=1000,
            years=25
        )
        assert irr == 100.0


class TestNPVCalculation:
    """Tests for Net Present Value calculation."""

    def test_positive_npv(self):
        """Should calculate positive NPV for good investment."""
        npv = calculate_npv(
            net_cost=10000,
            annual_savings=1500,
            years=25,
            discount_rate=0.06
        )
        assert npv > 0

    def test_negative_npv(self):
        """Should calculate negative NPV for poor investment."""
        npv = calculate_npv(
            net_cost=50000,
            annual_savings=500,
            years=25,
            discount_rate=0.06
        )
        assert npv < 0


class TestSimplePayback:
    """Tests for simple payback calculation."""

    def test_normal_payback(self):
        """Should calculate payback in years."""
        payback = calculate_simple_payback(
            net_cost=10000,
            annual_savings=2000
        )
        assert payback == 5.0

    def test_decimal_payback(self):
        """Payback should support decimal years."""
        payback = calculate_simple_payback(
            net_cost=10000,
            annual_savings=3000
        )
        assert abs(payback - 3.33) < 0.1

    def test_zero_savings_payback(self):
        """Zero savings should return 999 (placeholder for infinity)."""
        payback = calculate_simple_payback(
            net_cost=10000,
            annual_savings=0
        )
        assert payback == 999.0


class TestCashPath:
    """Tests for full Cash path calculation."""

    def test_cash_path_structure(self):
        """Cash path should return CashPathResult with all required fields."""
        result = calculate_cash_path(
            pv_kw=6.0,
            battery_kwh=10.0,
            baseline_annual_cost=4000,
            post_solar_annual_cost=1500
        )
        
        assert hasattr(result, 'system_cost_usd')
        assert hasattr(result, 'net_cost_usd')
        assert hasattr(result, 'year1_savings_usd')
        assert hasattr(result, 'irr_percent')
        assert hasattr(result, 'simple_payback_years')
        assert hasattr(result, 'npv_25yr_usd')

    def test_cash_path_values(self):
        """Cash path values should be reasonable."""
        result = calculate_cash_path(
            pv_kw=6.0,
            battery_kwh=10.0,
            baseline_annual_cost=4000,
            post_solar_annual_cost=1500
        )
        
        # System cost should be positive
        assert result.system_cost_usd > 0
        # Net cost should be less than system (after ITC)
        assert result.net_cost_usd < result.system_cost_usd
        # Year 1 savings should be baseline - post_solar
        assert result.year1_savings_usd == 4000 - 1500


class TestPPAPath:
    """Tests for full PPA path calculation."""

    def test_ppa_path_structure(self):
        """PPA path should return PPAPathResult with all required fields."""
        result = calculate_ppa_path(
            pv_kw=6.0,
            battery_kwh=10.0,
            annual_production_kwh=9000,  # 6kW * 1500 hrs = 9000 kWh/year
            post_solar_utility_cost=1500,
            baseline_annual_cost=4000
        )
        
        assert hasattr(result, 'annual_ppa_cost_usd')
        assert hasattr(result, 'annual_utility_cost_usd')
        assert hasattr(result, 'total_annual_cost_usd')
        assert hasattr(result, 'year1_savings_usd')

    def test_ppa_payment_calculation(self):
        """PPA payment should be generation * rate."""
        generation = 9000
        result = calculate_ppa_path(
            pv_kw=6.0,
            battery_kwh=10.0,
            annual_production_kwh=generation,
            post_solar_utility_cost=1500,
            baseline_annual_cost=4000
        )
        
        expected_ppa = generation * PPA_RATE_PER_KWH
        assert abs(result.annual_ppa_cost_usd - expected_ppa) < 1

    def test_ppa_savings_calculation(self):
        """Year 1 savings should be baseline - total cost."""
        result = calculate_ppa_path(
            pv_kw=6.0,
            battery_kwh=10.0,
            annual_production_kwh=9000,
            post_solar_utility_cost=1500,
            baseline_annual_cost=4000
        )
        
        expected_total = result.annual_ppa_cost_usd + result.annual_utility_cost_usd
        expected_savings = 4000 - expected_total
        assert abs(result.year1_savings_usd - expected_savings) < 1
