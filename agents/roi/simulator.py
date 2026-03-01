"""
Solar + Battery System Simulation at 15-Minute Intervals.

Provides interval-level simulation of PV generation, battery dispatch,
and energy flows for accurate NEM 3.0 ROI calculations.

Key Components:
- BatteryDispatch: Tracks battery state and enforces physical limits
- simulate_year: Full-year simulation with energy flow tracking
- optimize_solar_system: Grid search optimization for PV + battery sizing
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

import numpy as np

from .export_rates import get_export_rate
from .rates import EV_TOU_5, TOU_DR1, TOUPeriod, get_tou_period, get_tou_rate

if TYPE_CHECKING:
    from models import UtilityIntervalData

logger = logging.getLogger(__name__)

# Battery physical parameters
DEFAULT_BATTERY_EFFICIENCY = 0.95  # One-way efficiency (90% round-trip)
DEFAULT_MAX_POWER_KW = 5.0  # Max charge/discharge rate
INTERVAL_HOURS = 0.25  # 15-minute intervals = 0.25 hours

# Simulation parameters
HOURS_PER_YEAR = 8760
INTERVALS_PER_YEAR = 35040  # 8760 * 4

# Optimization grid
PV_SIZES_KW = [i * 0.5 for i in range(2, 21)]  # 1.0 to 10.0 kW in 0.5 steps
BATTERY_SIZES_KWH = [0, 5, 10, 15, 20, 25, 30]  # 7 options

# Minimum data coverage for optimization
MIN_DATA_DAYS = 30


@dataclass
class BatteryState:
    """Tracks battery state of charge and cumulative metrics."""
    
    capacity_kwh: float
    soc_kwh: float = 0.0  # Current state of charge
    efficiency: float = DEFAULT_BATTERY_EFFICIENCY
    max_power_kw: float = DEFAULT_MAX_POWER_KW
    
    # Cumulative metrics
    total_charged_kwh: float = 0.0
    total_discharged_kwh: float = 0.0
    total_losses_kwh: float = 0.0
    
    def __post_init__(self):
        """Initialize SOC to 50% of capacity."""
        self.soc_kwh = self.capacity_kwh * 0.5
    
    def charge(self, energy_kwh: float) -> float:
        """
        Charge the battery with available energy.
        
        Args:
            energy_kwh: Energy available to charge (pre-efficiency)
            
        Returns:
            Energy actually stored (post-efficiency)
        """
        if self.capacity_kwh <= 0:
            return 0.0
        
        # Apply power limit
        max_charge_this_interval = self.max_power_kw * INTERVAL_HOURS
        charge_attempt = min(energy_kwh, max_charge_this_interval)
        
        # Apply efficiency loss on charging
        energy_stored = charge_attempt * self.efficiency
        
        # Apply capacity limit
        available_capacity = self.capacity_kwh - self.soc_kwh
        actual_stored = min(energy_stored, available_capacity)
        
        # Update state
        self.soc_kwh += actual_stored
        self.total_charged_kwh += actual_stored
        
        # Track losses
        energy_used = actual_stored / self.efficiency if self.efficiency > 0 else 0
        self.total_losses_kwh += (charge_attempt - actual_stored / self.efficiency) if self.efficiency > 0 else 0
        
        # Return energy consumed from surplus (pre-efficiency)
        return actual_stored / self.efficiency if self.efficiency > 0 else 0.0
    
    def discharge(self, energy_needed_kwh: float) -> float:
        """
        Discharge the battery to meet load.
        
        Args:
            energy_needed_kwh: Energy needed (post-efficiency)
            
        Returns:
            Energy delivered to load (post-efficiency)
        """
        if self.capacity_kwh <= 0 or self.soc_kwh <= 0:
            return 0.0
        
        # Apply power limit
        max_discharge_this_interval = self.max_power_kw * INTERVAL_HOURS
        
        # We need to pull more from battery due to efficiency loss
        energy_to_pull = energy_needed_kwh / self.efficiency
        discharge_attempt = min(energy_to_pull, max_discharge_this_interval, self.soc_kwh)
        
        # Update state
        self.soc_kwh -= discharge_attempt
        self.total_discharged_kwh += discharge_attempt
        
        # Energy delivered after efficiency loss
        delivered = discharge_attempt * self.efficiency
        self.total_losses_kwh += (discharge_attempt - delivered)
        
        return delivered
    
    @property
    def soc_percent(self) -> float:
        """Current SOC as percentage."""
        return (self.soc_kwh / self.capacity_kwh * 100) if self.capacity_kwh > 0 else 0.0
    
    @property
    def full_cycles(self) -> float:
        """Equivalent full cycles (total discharged / capacity)."""
        return self.total_discharged_kwh / self.capacity_kwh if self.capacity_kwh > 0 else 0.0


@dataclass
class SimulationResult:
    """Results from a full-year simulation."""
    
    # System configuration
    pv_kw: float
    battery_kwh: float
    
    # Energy totals
    total_solar_generated_kwh: float = 0.0
    total_consumed_kwh: float = 0.0
    total_grid_import_kwh: float = 0.0
    total_grid_export_kwh: float = 0.0
    total_self_consumed_kwh: float = 0.0
    total_battery_losses_kwh: float = 0.0
    
    # Cost totals
    total_import_cost_usd: float = 0.0
    total_export_credit_usd: float = 0.0
    net_utility_cost_usd: float = 0.0  # imports - exports + fixed
    
    # TOU breakdown
    import_by_period: Dict[TOUPeriod, float] = field(default_factory=dict)
    export_by_period: Dict[TOUPeriod, float] = field(default_factory=dict)
    
    # Monthly breakdown (for detailed reporting)
    monthly_import_kwh: List[float] = field(default_factory=list)
    monthly_export_kwh: List[float] = field(default_factory=list)
    monthly_import_cost: List[float] = field(default_factory=list)
    monthly_export_credit: List[float] = field(default_factory=list)
    
    # Battery metrics
    battery_cycles: float = 0.0
    
    # Data quality
    data_coverage_days: int = 0
    data_confidence: float = 0.0
    
    @property
    def self_consumption_percent(self) -> float:
        """Percentage of solar used on-site (not exported)."""
        if self.total_solar_generated_kwh <= 0:
            return 0.0
        return self.total_self_consumed_kwh / self.total_solar_generated_kwh * 100
    
    @property
    def export_percent(self) -> float:
        """Percentage of solar exported to grid."""
        if self.total_solar_generated_kwh <= 0:
            return 0.0
        return self.total_grid_export_kwh / self.total_solar_generated_kwh * 100
    
    @property
    def offset_percent(self) -> float:
        """Percentage of consumption offset by solar."""
        if self.total_consumed_kwh <= 0:
            return 0.0
        offset = self.total_solar_generated_kwh - self.total_grid_export_kwh
        return offset / self.total_consumed_kwh * 100


def is_peak_period(hour: int, is_weekend: bool = False) -> bool:
    """Check if hour is in TOU peak period (4 PM - 9 PM weekdays)."""
    if is_weekend:
        return False
    return 16 <= hour < 21


def simulate_year(
    consumption_intervals: np.ndarray,
    pv_production_intervals: np.ndarray,
    battery_kwh: float = 0.0,
    import_schedule=EV_TOU_5,
    utility: str = "SDGE",
    start_date: datetime = None,
) -> SimulationResult:
    """
    Simulate a full year of PV + battery operation.
    
    Args:
        consumption_intervals: 15-min consumption values (kWh), array of up to 35040
        pv_production_intervals: 15-min PV production values (kWh), array of 35040
        battery_kwh: Battery capacity (0 for PV-only)
        import_schedule: TOU rate schedule for imports
        utility: Utility for export rates
        start_date: Start date for TOU calculations (defaults to Jan 1)
        
    Returns:
        SimulationResult with all energy and cost metrics
    """
    if start_date is None:
        start_date = datetime(2024, 1, 1)
    
    # Initialize result
    pv_kw = 0.0  # Will be estimated from production
    if len(pv_production_intervals) > 0:
        # Estimate system size from annual production (~1600 kWh/kW in SD)
        annual_production = np.sum(pv_production_intervals)
        pv_kw = annual_production / 1600 if annual_production > 0 else 0
    
    result = SimulationResult(pv_kw=pv_kw, battery_kwh=battery_kwh)
    
    # Initialize battery
    battery = BatteryState(capacity_kwh=battery_kwh)
    
    # Extend consumption to full year if needed (by repeating pattern)
    num_intervals = len(consumption_intervals)
    if num_intervals < INTERVALS_PER_YEAR:
        # Calculate coverage
        result.data_coverage_days = num_intervals // 96  # 96 intervals per day
        
        # Tile to fill year
        repeats = (INTERVALS_PER_YEAR + num_intervals - 1) // num_intervals
        consumption_full = np.tile(consumption_intervals, repeats)[:INTERVALS_PER_YEAR]
    else:
        consumption_full = consumption_intervals[:INTERVALS_PER_YEAR]
        result.data_coverage_days = 365
    
    # Calculate confidence based on coverage
    if result.data_coverage_days >= 365:
        result.data_confidence = 0.95
    elif result.data_coverage_days >= 180:
        result.data_confidence = 0.85
    elif result.data_coverage_days >= 90:
        result.data_confidence = 0.75
    elif result.data_coverage_days >= 30:
        result.data_confidence = 0.60
    else:
        result.data_confidence = 0.40
    
    # Initialize TOU period tracking
    result.import_by_period = {p: 0.0 for p in TOUPeriod}
    result.export_by_period = {p: 0.0 for p in TOUPeriod}
    
    # Monthly accumulators
    monthly_imports = [0.0] * 12
    monthly_exports = [0.0] * 12
    monthly_import_costs = [0.0] * 12
    monthly_export_credits = [0.0] * 12
    
    # Main simulation loop
    current_time = start_date
    
    for i in range(min(len(pv_production_intervals), INTERVALS_PER_YEAR)):
        consumption = consumption_full[i]
        production = pv_production_intervals[i]
        
        hour = current_time.hour
        month_idx = current_time.month - 1
        is_weekend = current_time.weekday() >= 5
        
        # Accumulate totals
        result.total_consumed_kwh += consumption
        result.total_solar_generated_kwh += production
        
        # Net energy this interval
        net = production - consumption
        
        if net >= 0:
            # Surplus: charge battery, then export
            surplus = net
            
            # Try to charge battery
            if battery_kwh > 0:
                charged = battery.charge(surplus)
                surplus -= charged
                result.total_self_consumed_kwh += charged
            
            # Remaining surplus goes to export
            if surplus > 0:
                result.total_grid_export_kwh += surplus
                
                # Calculate export credit
                export_rate = get_export_rate(current_time.month, hour, is_weekend, utility)
                credit = surplus * export_rate
                result.total_export_credit_usd += credit
                monthly_export_credits[month_idx] += credit
                monthly_exports[month_idx] += surplus
                
                # Track by TOU period
                period = get_tou_period(hour, is_weekend, import_schedule)
                result.export_by_period[period] += surplus
            
            # Solar used directly
            result.total_self_consumed_kwh += min(production, consumption)
            
        else:
            # Deficit: use battery during peak, import otherwise
            deficit = -net
            
            # All solar is self-consumed
            result.total_self_consumed_kwh += production
            
            # Try battery during peak hours
            if battery_kwh > 0 and is_peak_period(hour, is_weekend):
                delivered = battery.discharge(deficit)
                deficit -= delivered
            
            # Remaining deficit from grid
            if deficit > 0:
                result.total_grid_import_kwh += deficit
                
                # Calculate import cost
                import_rate = get_tou_rate(hour, is_weekend, import_schedule)
                cost = deficit * import_rate
                result.total_import_cost_usd += cost
                monthly_import_costs[month_idx] += cost
                monthly_imports[month_idx] += deficit
                
                # Track by TOU period
                period = get_tou_period(hour, is_weekend, import_schedule)
                result.import_by_period[period] += deficit
        
        # Advance time
        current_time += timedelta(minutes=15)
    
    # Finalize results
    result.total_battery_losses_kwh = battery.total_losses_kwh
    result.battery_cycles = battery.full_cycles
    
    result.monthly_import_kwh = monthly_imports
    result.monthly_export_kwh = monthly_exports
    result.monthly_import_cost = monthly_import_costs
    result.monthly_export_credit = monthly_export_credits
    
    # Net utility cost (imports - exports, assuming monthly netting)
    result.net_utility_cost_usd = result.total_import_cost_usd - result.total_export_credit_usd
    
    return result


def calculate_baseline_cost(
    consumption_intervals: np.ndarray,
    rate_schedule=TOU_DR1,
    start_date: datetime = None,
    fixed_monthly_charge: float = 10.0,
) -> float:
    """
    Calculate annual utility cost without solar.
    
    Args:
        consumption_intervals: 15-min consumption values
        rate_schedule: TOU rate schedule
        start_date: Start date for TOU calculations
        fixed_monthly_charge: Monthly fixed utility charge
        
    Returns:
        Total annual utility cost in USD
    """
    if start_date is None:
        start_date = datetime(2024, 1, 1)
    
    # Extend to full year if needed
    num_intervals = len(consumption_intervals)
    if num_intervals < INTERVALS_PER_YEAR:
        repeats = (INTERVALS_PER_YEAR + num_intervals - 1) // num_intervals
        consumption_full = np.tile(consumption_intervals, repeats)[:INTERVALS_PER_YEAR]
    else:
        consumption_full = consumption_intervals[:INTERVALS_PER_YEAR]
    
    total_cost = 0.0
    current_time = start_date
    
    for i in range(INTERVALS_PER_YEAR):
        consumption = consumption_full[i]
        hour = current_time.hour
        is_weekend = current_time.weekday() >= 5
        
        rate = get_tou_rate(hour, is_weekend, rate_schedule)
        total_cost += consumption * rate
        
        current_time += timedelta(minutes=15)
    
    # Add fixed charges
    total_cost += fixed_monthly_charge * 12
    
    return total_cost


def validate_energy_balance(result: SimulationResult, tolerance: float = 0.01) -> bool:
    """
    Validate that energy flows balance correctly.
    
    Solar = Self-consumed + Exported (± battery losses)
    """
    expected_solar = result.total_self_consumed_kwh + result.total_grid_export_kwh
    actual_solar = result.total_solar_generated_kwh
    
    # Account for battery losses
    expected_solar += result.total_battery_losses_kwh
    
    relative_error = abs(expected_solar - actual_solar) / actual_solar if actual_solar > 0 else 0
    
    if relative_error > tolerance:
        logger.warning(
            f"Energy balance error: expected {expected_solar:.1f}, "
            f"actual {actual_solar:.1f} kWh ({relative_error*100:.1f}% error)"
        )
        return False
    
    return True


@dataclass
class OptimizationResult:
    """Results from grid search optimization."""
    
    # Optimal configurations
    cash_optimal: Optional[dict] = None
    ppa_optimal: Optional[dict] = None
    
    # Baseline
    baseline_annual_cost_usd: float = 0.0
    
    # Optimization metadata
    configurations_evaluated: int = 0
    runtime_seconds: float = 0.0
    paths_aligned: bool = False
    
    # Data quality
    data_coverage_days: int = 0
    data_confidence: float = 0.0
    
    # Errors
    error_message: Optional[str] = None


def optimize_solar_system(
    consumption_intervals: np.ndarray,
    zip_code: str,
    baseline_rate_schedule=TOU_DR1,
    post_solar_rate_schedule=EV_TOU_5,
    utility: str = "SDGE",
    db_session=None,
    pv_sizes: List[float] = None,
    battery_sizes: List[float] = None,
) -> OptimizationResult:
    """
    Find optimal PV + battery configuration using grid search.
    
    Evaluates all combinations of PV and battery sizes, running full-year
    simulations for each, and selects:
    - Cash optimal: Configuration with highest IRR
    - PPA optimal: Configuration with lowest total Year-1 cost
    
    Args:
        consumption_intervals: 15-min consumption values (kWh)
        zip_code: Property zip code for PVWatts lookup
        baseline_rate_schedule: TOU schedule for baseline cost
        post_solar_rate_schedule: TOU schedule with solar
        utility: Utility for export rates
        db_session: Optional DB session for caching
        pv_sizes: Custom PV sizes to evaluate (default: 1-10 kW)
        battery_sizes: Custom battery sizes (default: 0-30 kWh)
        
    Returns:
        OptimizationResult with optimal configurations and metadata
    """
    start_time = time.time()
    result = OptimizationResult()
    
    # Use defaults if not specified
    pv_sizes = pv_sizes or PV_SIZES_KW
    battery_sizes = battery_sizes or BATTERY_SIZES_KWH
    
    # Validate input data
    num_intervals = len(consumption_intervals)
    data_days = num_intervals // 96
    
    if data_days < MIN_DATA_DAYS:
        result.error_message = f"Insufficient data: {data_days} days (minimum {MIN_DATA_DAYS})"
        return result
    
    result.data_coverage_days = data_days
    
    # Calculate baseline cost
    result.baseline_annual_cost_usd = calculate_baseline_cost(
        consumption_intervals, baseline_rate_schedule
    )
    
    # Fetch PVWatts profile
    from .pvwatts import get_hourly_profile, interpolate_to_15min, scale_profile_to_system_size
    
    hourly_profile, pvwatts_error = get_hourly_profile(zip_code, db_session=db_session)
    
    if hourly_profile is None:
        # Try fallback to fixed solar curve
        logger.warning(f"PVWatts failed: {pvwatts_error}, using fallback profile")
        # Generate approximate San Diego profile (1600 kWh/kW/year)
        hourly_profile = _generate_fallback_profile()
    
    # Interpolate to 15-minute intervals
    pv_profile_15min = interpolate_to_15min(hourly_profile)
    
    # Track best configurations
    best_cash_irr = -float('inf')
    best_cash_config = None
    best_ppa_cost = float('inf')
    best_ppa_config = None
    
    configs_evaluated = 0
    
    # Import financial functions
    from .financial import calculate_cash_path, calculate_ppa_path
    
    # Grid search
    for pv_kw in pv_sizes:
        # Scale PV profile to this size
        pv_production = scale_profile_to_system_size(pv_profile_15min, pv_kw)
        
        for battery_kwh in battery_sizes:
            configs_evaluated += 1
            
            # Run simulation
            sim_result = simulate_year(
                consumption_intervals,
                pv_production,
                battery_kwh=battery_kwh,
                import_schedule=post_solar_rate_schedule,
                utility=utility,
            )
            
            # Calculate Cash path metrics
            cash_result = calculate_cash_path(
                pv_kw,
                battery_kwh,
                result.baseline_annual_cost_usd,
                sim_result.net_utility_cost_usd,
            )
            
            # Calculate PPA path metrics
            ppa_result = calculate_ppa_path(
                pv_kw,
                battery_kwh,
                sim_result.total_solar_generated_kwh,
                sim_result.net_utility_cost_usd,
                result.baseline_annual_cost_usd,
            )
            
            # Track best Cash (highest IRR)
            if cash_result.irr_percent > best_cash_irr:
                best_cash_irr = cash_result.irr_percent
                best_cash_config = {
                    "pv_kw": pv_kw,
                    "battery_kwh": battery_kwh,
                    "irr_percent": cash_result.irr_percent,
                    "payback_years": cash_result.simple_payback_years,
                    "net_cost_usd": cash_result.net_cost_usd,
                    "year1_savings_usd": cash_result.year1_savings_usd,
                    "system_cost_usd": cash_result.system_cost_usd,
                    "post_solar_annual_cost_usd": cash_result.post_solar_annual_cost_usd,
                    "self_consumption_percent": sim_result.self_consumption_percent,
                    "export_percent": sim_result.export_percent,
                    "annual_production_kwh": sim_result.total_solar_generated_kwh,
                }
            
            # Track best PPA (lowest total cost)
            if ppa_result.total_annual_cost_usd < best_ppa_cost:
                best_ppa_cost = ppa_result.total_annual_cost_usd
                best_ppa_config = {
                    "pv_kw": pv_kw,
                    "battery_kwh": battery_kwh,
                    "monthly_cost_usd": ppa_result.monthly_total_cost_usd,
                    "year1_total_cost_usd": ppa_result.total_annual_cost_usd,
                    "year1_savings_usd": ppa_result.year1_savings_usd,
                    "annual_ppa_cost_usd": ppa_result.annual_ppa_cost_usd,
                    "annual_utility_cost_usd": ppa_result.annual_utility_cost_usd,
                    "self_consumption_percent": sim_result.self_consumption_percent,
                    "export_percent": sim_result.export_percent,
                    "annual_production_kwh": sim_result.total_solar_generated_kwh,
                }
    
    # Finalize result
    result.configurations_evaluated = configs_evaluated
    result.runtime_seconds = time.time() - start_time
    
    # Only include valid results
    if best_cash_config and best_cash_config["irr_percent"] > 0:
        result.cash_optimal = best_cash_config
    
    if best_ppa_config and best_ppa_config["year1_savings_usd"] > 0:
        result.ppa_optimal = best_ppa_config
    
    # Check if paths align
    if result.cash_optimal and result.ppa_optimal:
        result.paths_aligned = (
            result.cash_optimal["pv_kw"] == result.ppa_optimal["pv_kw"] and
            result.cash_optimal["battery_kwh"] == result.ppa_optimal["battery_kwh"]
        )
    
    # Set data confidence
    if result.data_coverage_days >= 365:
        result.data_confidence = 0.95
    elif result.data_coverage_days >= 180:
        result.data_confidence = 0.85
    elif result.data_coverage_days >= 90:
        result.data_confidence = 0.75
    else:
        result.data_confidence = 0.60
    
    logger.info(
        f"Optimization complete: {configs_evaluated} configs in "
        f"{result.runtime_seconds:.2f}s. Cash IRR: {best_cash_irr:.1f}%, "
        f"PPA cost: ${best_ppa_cost:.2f}/yr"
    )
    
    return result


def _generate_fallback_profile() -> np.ndarray:
    """
    Generate approximate San Diego solar profile.
    
    Used when PVWatts API is unavailable. Based on typical
    San Diego insolation pattern: ~1600 kWh/kW/year.
    """
    # Typical hourly pattern (fraction of daily peak)
    hourly_pattern = np.array([
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0,  # 0-5 AM
        0.05, 0.15, 0.35, 0.55, 0.75, 0.90,  # 6-11 AM
        0.95, 0.90, 0.80, 0.65, 0.45, 0.25,  # 12-5 PM
        0.08, 0.0, 0.0, 0.0, 0.0, 0.0,  # 6-11 PM
    ])
    
    # Seasonal multipliers (winter lower, summer higher)
    monthly_multiplier = np.array([
        0.70, 0.75, 0.85, 0.95, 1.05, 1.10,  # Jan-Jun
        1.10, 1.05, 0.95, 0.85, 0.75, 0.70,  # Jul-Dec
    ])
    
    # Generate 8760 hourly values
    profile = []
    days_per_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    
    for month_idx, days in enumerate(days_per_month):
        monthly_mult = monthly_multiplier[month_idx]
        for day in range(days):
            for hour in range(24):
                # Target ~1600 kWh/kW/year = ~4.38 kWh/day average
                # Peak hour produces ~0.8 kWh
                value = hourly_pattern[hour] * monthly_mult * 0.8 / 4.38 * 4.38
                profile.append(value)
    
    return np.array(profile)
