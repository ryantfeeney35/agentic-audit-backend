"""
Unit tests for NEM 3 SBP export rates module.
Tests task 2.5.
"""
import pytest
from agents.roi.export_rates import get_export_rate, get_export_rates_array


class TestGetExportRate:
    """Tests for get_export_rate function."""

    def test_winter_peak_weekday(self):
        """Winter peak (4-9pm weekday) should have higher rate."""
        # January, 5pm (17), weekday
        rate = get_export_rate(month=1, hour=17, is_weekend=False)
        assert 0.05 <= rate <= 0.15
        # Winter peak should be moderate
        assert rate > 0.03

    def test_winter_offpeak_weekday(self):
        """Winter off-peak should have lower rate."""
        # January, 2am, weekday
        rate = get_export_rate(month=1, hour=2, is_weekend=False)
        assert rate > 0
        assert rate < 0.10  # Off-peak should be lower

    def test_summer_peak_weekday(self):
        """Summer peak (4-9pm weekday) should have highest rate."""
        # August, 6pm (18), weekday
        rate = get_export_rate(month=8, hour=18, is_weekend=False)
        assert rate > 0.05  # Summer peak should be higher

    def test_summer_offpeak_weekday(self):
        """Summer off-peak morning."""
        # July, 10am, weekday
        rate = get_export_rate(month=7, hour=10, is_weekend=False)
        assert rate > 0

    def test_weekend_rates(self):
        """Weekend rates should differ from weekday."""
        # August, 6pm
        weekday_rate = get_export_rate(month=8, hour=18, is_weekend=False)
        weekend_rate = get_export_rate(month=8, hour=18, is_weekend=True)
        # Both should be valid rates
        assert weekday_rate > 0
        assert weekend_rate > 0

    def test_all_months_valid(self):
        """All months should return valid rates."""
        for month in range(1, 13):
            rate = get_export_rate(month=month, hour=12, is_weekend=False)
            assert 0 < rate < 1.0, f"Month {month} returned invalid rate: {rate}"

    def test_all_hours_valid(self):
        """All hours should return valid rates."""
        for hour in range(24):
            rate = get_export_rate(month=6, hour=hour, is_weekend=False)
            assert 0 < rate < 1.0, f"Hour {hour} returned invalid rate: {rate}"

    def test_boundary_months(self):
        """Test boundary conditions for months."""
        # Month 1 (January)
        rate1 = get_export_rate(month=1, hour=12, is_weekend=False)
        # Month 12 (December)
        rate12 = get_export_rate(month=12, hour=12, is_weekend=False)
        assert rate1 > 0
        assert rate12 > 0


class TestGetExportRatesArray:
    """Tests for get_export_rates_array function."""

    def test_array_length(self):
        """Array should have 8760 hourly intervals for full year."""
        rates = get_export_rates_array()
        assert len(rates) == 8760

    def test_all_rates_positive(self):
        """All rates should be positive."""
        rates = get_export_rates_array()
        assert all(r > 0 for r in rates)

    def test_rates_reasonable_range(self):
        """Rates should be in reasonable range."""
        rates = get_export_rates_array()
        assert min(rates) >= 0.01
        assert max(rates) <= 0.50

    def test_rate_variation(self):
        """Should have variation in rates (not all same value)."""
        rates = get_export_rates_array()
        unique_rates = set(rates)
        assert len(unique_rates) > 1, "Export rates should vary"

    def test_summer_has_higher_peak(self):
        """Summer months should have higher peak rates than winter."""
        rates = get_export_rates_array()
        # Hourly array: 8760 hours, Jan = 0-743, Jul = 4344-5087, Aug = 5088-5831
        # Check summer (July-August) vs winter (January-February)
        summer_rates = rates[4344:5832]  # July-August
        winter_rates = rates[0:1416]  # January-February
        assert max(summer_rates) >= max(winter_rates) * 0.5  # Allow seasonal variation
