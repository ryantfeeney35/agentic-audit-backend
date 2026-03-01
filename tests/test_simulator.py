"""
Unit tests for battery dispatch simulation module.
Tests tasks 3.7, 4.7, and 7.8.
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime
from dataclasses import dataclass
from agents.roi.simulator import (
    BatteryState,
    SimulationResult,
    OptimizationResult,
    simulate_year,
    calculate_baseline_cost,
    optimize_solar_system,
    validate_energy_balance,
)


class TestBatteryState:
    """Tests for BatteryState class."""

    def test_initial_state(self):
        """Battery should start at 50% SOC."""
        battery = BatteryState(capacity_kwh=10.0)
        assert battery.soc_kwh == 5.0  # 50% of 10kWh
        assert battery.capacity_kwh == 10.0

    def test_charge_within_capacity(self):
        """Charging should increase SOC."""
        battery = BatteryState(capacity_kwh=10.0)
        initial_soc = battery.soc_kwh
        charged = battery.charge(2.0)  # Try to charge 2 kWh
        assert battery.soc_kwh > initial_soc
        assert charged > 0

    def test_charge_at_max_capacity(self):
        """Charging at full capacity should not increase SOC."""
        battery = BatteryState(capacity_kwh=10.0)
        battery.soc_kwh = 10.0  # Set to full
        charged = battery.charge(5.0)
        assert battery.soc_kwh == 10.0

    def test_discharge_within_capacity(self):
        """Discharging should decrease SOC."""
        battery = BatteryState(capacity_kwh=10.0)
        battery.soc_kwh = 8.0  # 80% full
        discharged = battery.discharge(2.0)
        assert battery.soc_kwh < 8.0
        assert discharged > 0

    def test_discharge_at_empty(self):
        """Discharging at empty should return 0."""
        battery = BatteryState(capacity_kwh=10.0)
        battery.soc_kwh = 0.0  # Empty
        discharged = battery.discharge(5.0)
        assert discharged == 0.0

    def test_round_trip_efficiency(self):
        """Energy out should be less than energy in due to losses."""
        battery = BatteryState(capacity_kwh=10.0)
        battery.soc_kwh = 5.0
        
        # Charge and discharge
        battery.charge(2.0)
        battery.discharge(2.0)
        
        # Should have some losses
        assert battery.total_losses_kwh > 0

    def test_soc_percent(self):
        """SOC percentage should be calculated correctly."""
        battery = BatteryState(capacity_kwh=10.0)
        battery.soc_kwh = 5.0
        assert battery.soc_percent == 50.0


class TestSimulateYear:
    """Tests for year simulation."""

    @pytest.fixture
    def mock_consumption_data(self):
        """Create mock 15-min consumption data for a year."""
        import numpy as np
        # Typical daily pattern: higher in evening, lower at night
        data = []
        for interval in range(35040):
            hour = (interval % 96) // 4
            if 17 <= hour <= 21:  # Evening peak
                consumption = 1.2
            elif 6 <= hour <= 16:  # Daytime
                consumption = 0.5
            else:  # Night
                consumption = 0.3
            data.append(consumption)
        return np.array(data)

    @pytest.fixture  
    def mock_pv_profile(self):
        """Create mock PV generation profile (35040 15-min intervals)."""
        import numpy as np
        profile = []
        for interval in range(35040):
            hour = (interval % 96) // 4
            if 8 <= hour <= 16:  # Daylight hours
                # Peak at noon
                solar = max(0, 1.0 - abs(hour - 12) * 0.15)
            else:
                solar = 0
            profile.append(solar)  # Per kW
        return np.array(profile)

    def test_simulation_returns_result(self, mock_consumption_data, mock_pv_profile):
        """Simulation should return SimulationResult."""
        result = simulate_year(
            consumption_intervals=mock_consumption_data,
            pv_production_intervals=mock_pv_profile * 6.0,  # 6kW system
            battery_kwh=10.0,
        )

        assert isinstance(result, SimulationResult)
        assert result.total_solar_generated_kwh > 0
        assert result.total_consumed_kwh > 0
        assert result.net_utility_cost_usd >= 0

    def test_pv_only_simulation(self, mock_consumption_data, mock_pv_profile):
        """PV-only (no battery) simulation should work."""
        result = simulate_year(
            consumption_intervals=mock_consumption_data,
            pv_production_intervals=mock_pv_profile * 6.0,
            battery_kwh=0,  # No battery
        )

        assert result.total_solar_generated_kwh > 0
        assert result.battery_cycles == 0


class TestBaselineCost:
    """Tests for baseline cost calculation."""

    def test_baseline_cost_calculation(self):
        """Should calculate cost based on consumption and TOU rates."""
        import numpy as np
        # Simple consumption data
        intervals = np.ones(35040) * 0.3  # 0.3 kWh per 15-min interval
        
        cost = calculate_baseline_cost(intervals)
        
        assert cost > 0
        # Annual consumption ~35040 * 0.3 = 10512 kWh
        # ~$0.30/kWh avg = ~$3153 + $120 fixed = ~$3273
        assert 2000 < cost < 6000

    def test_short_data_extrapolates(self):
        """Short data should be extrapolated to full year."""
        import numpy as np
        # Only 30 days of data
        intervals = np.ones(30 * 96) * 0.3
        
        cost = calculate_baseline_cost(intervals)
        
        # Should still calculate a full year cost
        assert cost > 0
        assert 2000 < cost < 6000  # Full year cost


class TestOptimization:
    """Tests for grid search optimization."""

    @patch('agents.roi.pvwatts.get_hourly_profile')
    def test_optimization_returns_result_structure(self, mock_pv):
        """Should return OptimizationResult with expected fields."""
        import numpy as np
        
        # Mock PVWatts to avoid API call
        mock_pv.return_value = (np.ones(8760) * 0.18, None)  # ~1600 kWh/kW/year
        
        # Create simple consumption data (60 days worth = 60 * 96 intervals)
        intervals = np.ones(60 * 96) * 0.3
        
        result = optimize_solar_system(
            consumption_intervals=intervals,
            zip_code='92101',
            pv_sizes=[3.0, 5.0],  # Limited grid for fast test
            battery_sizes=[0, 10.0],
        )

        assert isinstance(result, OptimizationResult)
        assert hasattr(result, 'cash_optimal')
        assert hasattr(result, 'ppa_optimal')
        assert hasattr(result, 'configurations_evaluated')
        assert hasattr(result, 'baseline_annual_cost_usd')

    def test_optimization_insufficient_data(self):
        """Should return error for insufficient data."""
        import numpy as np
        
        # Only 5 days of data (minimum is 30)
        intervals = np.ones(5 * 96) * 0.3
        
        result = optimize_solar_system(
            consumption_intervals=intervals,
            zip_code='92101',
        )
        
        assert result.error_message is not None
        assert 'Insufficient' in result.error_message


class TestEnergyBalanceValidation:
    """Tests for energy balance validation."""

    def test_valid_balance(self):
        """Valid energy balance should pass."""
        import numpy as np
        
        # Create a valid SimulationResult
        result = SimulationResult(pv_kw=6.0, battery_kwh=10.0)
        result.total_solar_generated_kwh = 8000
        result.total_self_consumed_kwh = 7000
        result.total_grid_export_kwh = 1000
        
        is_valid = validate_energy_balance(result)
        assert is_valid

    def test_invalid_balance(self):
        """Invalid energy balance should fail validation."""
        import numpy as np
        
        # Create invalid result (exports + self-consumed > generated)
        result = SimulationResult(pv_kw=6.0, battery_kwh=10.0)
        result.total_solar_generated_kwh = 8000
        result.total_self_consumed_kwh = 6000
        result.total_grid_export_kwh = 3000  # 6000 + 3000 > 8000
        
        is_valid = validate_energy_balance(result)
        assert not is_valid
