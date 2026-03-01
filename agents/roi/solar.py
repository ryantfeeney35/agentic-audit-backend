"""
Solar System Sizing and ROI Calculations for NEM 3.0.

Provides deterministic calculations for:
- Optimal solar array size based on interval usage data
- Battery storage sizing for TOU arbitrage
- PPA-based ROI comparing pre/post solar costs

Assumptions (California NEM 3.0):
- Solar insolation: 1,600 kWh/kW/year (San Diego average)
- Target offset: 85% of annual consumption
- Battery sizing: 70% of 4-7 PM peak load coverage
- PPA rate: $0.32/kWh for all generation
- Export compensation: ~$0.05/kWh (minimal under NEM 3.0)
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from ..schemas import SolarSizingInput, SolarSizingOutput, SolarROIOutput
from .rates import (
    TOU_DR1,
    EV_TOU_5,
    NEM_3_EXPORT_RATE,
    get_tou_rate,
    calculate_interval_cost,
)

if TYPE_CHECKING:
    from models import UtilityIntervalData

logger = logging.getLogger(__name__)

# San Diego solar insolation factor
SOLAR_INSOLATION_KWH_PER_KW = 1600  # kWh produced per kW installed annually

# Target offset percentage (avoid oversizing under NEM 3.0)
TARGET_OFFSET_PERCENTAGE = 0.85

# Battery sizing parameters
BATTERY_PEAK_COVERAGE = 0.70  # Cover 70% of peak load
BATTERY_MIN_KWH = 5.0  # Minimum recommended for TOU arbitrage
BATTERY_MAX_KWH = 10.0  # Cap for PPA-based systems

# Minimum annual consumption for solar recommendation
MIN_ANNUAL_KWH = 3000

# PPA rate
DEFAULT_PPA_RATE = 0.32  # $/kWh


def extract_interval_metrics(
    intervals: List["UtilityIntervalData"],
) -> Optional[SolarSizingInput]:
    """
    Extract solar sizing metrics from 15-minute interval data.
    
    Analyzes interval data to produce:
    - Annual consumption estimate
    - Peak period (4-7 PM) average consumption
    - Weekday/weekend hourly profiles
    - Data coverage assessment
    
    Args:
        intervals: List of UtilityIntervalData records
        
    Returns:
        SolarSizingInput with extracted metrics, or None if insufficient data
    """
    if not intervals:
        logger.warning("No interval data provided for solar sizing")
        return None
    
    # Calculate totals and profiles
    total_kwh = 0.0
    hourly_totals_weekday = defaultdict(float)
    hourly_counts_weekday = defaultdict(int)
    hourly_totals_weekend = defaultdict(float)
    hourly_counts_weekend = defaultdict(int)
    monthly_totals = defaultdict(float)
    
    peak_period_total = 0.0
    peak_period_count = 0
    
    dates_seen = set()
    
    for interval in intervals:
        usage = interval.usage_kwh
        total_kwh += usage
        
        start_dt = interval.interval_start
        hour = start_dt.hour
        month = start_dt.month
        day = start_dt.date()
        is_weekend = start_dt.weekday() >= 5
        
        dates_seen.add(day)
        monthly_totals[month] += usage
        
        if is_weekend:
            hourly_totals_weekend[hour] += usage
            hourly_counts_weekend[hour] += 1
        else:
            hourly_totals_weekday[hour] += usage
            hourly_counts_weekday[hour] += 1
        
        # Peak period: 4 PM - 7 PM (hours 16, 17, 18)
        if 16 <= hour < 19:
            peak_period_total += usage
            peak_period_count += 1
    
    # Calculate data coverage
    days_of_data = len(dates_seen)
    months_of_data = days_of_data / 30.0
    
    if months_of_data < 1:
        logger.warning("Less than 1 month of interval data: %.1f months", months_of_data)
    
    # Annualize consumption if less than 12 months
    if months_of_data < 12:
        annual_consumption = total_kwh * (12 / months_of_data)
        logger.info(
            "Annualizing consumption: %.1f kWh over %.1f months → %.1f kWh/year",
            total_kwh, months_of_data, annual_consumption
        )
    else:
        annual_consumption = total_kwh
    
    # Calculate peak period average (kWh per hour)
    # Each 15-min interval is 0.25 hours, so multiply count by 0.25 for total hours
    peak_hours = peak_period_count * 0.25
    peak_period_avg_kwh = peak_period_total / peak_hours if peak_hours > 0 else 0.5
    
    # Build hourly averages
    weekday_hourly_avg = []
    weekend_hourly_avg = []
    for hour in range(24):
        if hourly_counts_weekday[hour] > 0:
            # Convert 15-min averages to hourly (multiply by 4)
            weekday_hourly_avg.append(
                (hourly_totals_weekday[hour] / hourly_counts_weekday[hour]) * 4
            )
        else:
            weekday_hourly_avg.append(0.0)
        
        if hourly_counts_weekend[hour] > 0:
            weekend_hourly_avg.append(
                (hourly_totals_weekend[hour] / hourly_counts_weekend[hour]) * 4
            )
        else:
            weekend_hourly_avg.append(0.0)
    
    # Build monthly totals list
    monthly_consumption = [monthly_totals.get(m, 0.0) for m in range(1, 13)]
    
    # Get property zip from first interval if available
    property_zip = None
    if hasattr(intervals[0], 'audit') and intervals[0].audit:
        if hasattr(intervals[0].audit, 'property') and intervals[0].audit.property:
            property_zip = intervals[0].audit.property.zip_code
    
    return SolarSizingInput(
        annual_consumption_kwh=annual_consumption,
        peak_period_avg_kwh=peak_period_avg_kwh,
        monthly_consumption=monthly_consumption if any(monthly_consumption) else None,
        weekday_hourly_avg=weekday_hourly_avg,
        weekend_hourly_avg=weekend_hourly_avg,
        data_coverage_months=min(months_of_data, 12),
        property_zip=property_zip,
    )


def calculate_system_size(annual_kwh: float) -> float:
    """
    Calculate recommended solar array size in kW.
    
    Targets 85% offset under NEM 3.0 (avoid oversizing due to low export value).
    
    Args:
        annual_kwh: Annual consumption in kWh
        
    Returns:
        Recommended system size in kW, rounded to nearest 0.5 kW
    """
    if annual_kwh < MIN_ANNUAL_KWH:
        return 0.0
    
    # Calculate size to offset TARGET_OFFSET_PERCENTAGE of consumption
    raw_size = (annual_kwh * TARGET_OFFSET_PERCENTAGE) / SOLAR_INSOLATION_KWH_PER_KW
    
    # Round to nearest 0.5 kW
    rounded_size = round(raw_size * 2) / 2
    
    logger.debug(
        "System sizing: %.0f kWh × %.0f%% / %d kWh/kW = %.1f kW → %.1f kW",
        annual_kwh, TARGET_OFFSET_PERCENTAGE * 100, SOLAR_INSOLATION_KWH_PER_KW,
        raw_size, rounded_size
    )
    
    return max(rounded_size, 2.0)  # Minimum 2 kW system


def calculate_battery_size(peak_period_avg_kwh: float) -> float:
    """
    Calculate recommended battery storage capacity.
    
    Sizes battery to shift 70% of 4-7 PM peak load, capped at 10 kWh.
    
    Args:
        peak_period_avg_kwh: Average hourly consumption during 4-7 PM (kWh/hour)
        
    Returns:
        Recommended battery capacity in kWh
    """
    # Peak period is 3 hours (4-7 PM)
    peak_hours = 3.0
    
    # Calculate battery to cover 70% of peak load
    raw_capacity = peak_period_avg_kwh * peak_hours * BATTERY_PEAK_COVERAGE
    
    # Apply min/max bounds
    battery_kwh = max(BATTERY_MIN_KWH, min(raw_capacity, BATTERY_MAX_KWH))
    
    logger.debug(
        "Battery sizing: %.2f kWh/hr × %.0f hrs × %.0f%% = %.1f kWh → %.1f kWh",
        peak_period_avg_kwh, peak_hours, BATTERY_PEAK_COVERAGE * 100,
        raw_capacity, battery_kwh
    )
    
    return battery_kwh


def size_solar_system(input: SolarSizingInput) -> Optional[SolarSizingOutput]:
    """
    Calculate complete solar+battery system sizing.
    
    Args:
        input: SolarSizingInput with interval-derived metrics
        
    Returns:
        SolarSizingOutput with sizing recommendations, or None if not viable
    """
    if input.annual_consumption_kwh < MIN_ANNUAL_KWH:
        logger.info(
            "Annual consumption %.0f kWh below threshold %d kWh - skipping solar",
            input.annual_consumption_kwh, MIN_ANNUAL_KWH
        )
        return None
    
    # Calculate system size
    system_size_kw = calculate_system_size(input.annual_consumption_kwh)
    if system_size_kw <= 0:
        return None
    
    # Calculate battery size
    battery_capacity_kwh = calculate_battery_size(input.peak_period_avg_kwh)
    
    # Calculate expected production
    annual_production_kwh = system_size_kw * SOLAR_INSOLATION_KWH_PER_KW
    
    # Calculate offset percentage
    offset_percentage = annual_production_kwh / input.annual_consumption_kwh
    
    # Determine confidence based on data coverage
    if input.data_coverage_months >= 6:
        confidence = 0.90
    elif input.data_coverage_months >= 3:
        confidence = 0.75
    else:
        confidence = 0.60
    
    return SolarSizingOutput(
        system_size_kw=system_size_kw,
        battery_capacity_kwh=battery_capacity_kwh,
        annual_production_kwh=annual_production_kwh,
        annual_consumption_kwh=input.annual_consumption_kwh,
        offset_percentage=offset_percentage,
        peak_period_avg_kwh=input.peak_period_avg_kwh,
        confidence=confidence,
    )


def calculate_current_annual_cost(
    intervals: List["UtilityIntervalData"],
) -> float:
    """
    Calculate current annual utility cost using TOU-DR1 rates.
    
    Args:
        intervals: List of UtilityIntervalData records
        
    Returns:
        Estimated annual cost in USD
    """
    if not intervals:
        return 0.0
    
    total_cost = 0.0
    for interval in intervals:
        hour = interval.interval_start.hour
        is_weekend = interval.interval_start.weekday() >= 5
        cost = calculate_interval_cost(
            interval.usage_kwh, hour, is_weekend, TOU_DR1
        )
        total_cost += cost
    
    # Annualize if less than 12 months
    dates_seen = set(i.interval_start.date() for i in intervals)
    months_of_data = len(dates_seen) / 30.0
    
    if months_of_data < 12:
        total_cost = total_cost * (12 / months_of_data)
    
    return total_cost


def calculate_post_solar_cost(
    intervals: List["UtilityIntervalData"],
    production_kwh: float,
    ppa_rate: float = DEFAULT_PPA_RATE,
) -> tuple[float, float, float]:
    """
    Calculate annual cost after solar installation under NEM 3.0.
    
    Simulates hourly production curve and calculates:
    - PPA cost for all generation
    - Remaining grid cost on EV-TOU-5 rates
    - Export compensation (minimal under NEM 3.0)
    
    Args:
        intervals: List of UtilityIntervalData records
        production_kwh: Annual solar production in kWh
        ppa_rate: Cost per kWh for PPA (default $0.32)
        
    Returns:
        Tuple of (ppa_cost, grid_cost, export_credit)
    """
    if not intervals:
        return 0.0, 0.0, 0.0
    
    # PPA cost is simple: all production × rate
    ppa_cost = production_kwh * ppa_rate
    
    # For grid cost, we need to model solar production curve
    # Solar produces during daylight hours, peak ~noon
    # Simple approximation: distribute production across 6 AM - 6 PM
    
    # Build hourly consumption profile
    hourly_consumption = defaultdict(float)
    hourly_counts = defaultdict(int)
    
    for interval in intervals:
        hour = interval.interval_start.hour
        hourly_consumption[hour] += interval.usage_kwh
        hourly_counts[hour] += 1
    
    dates_seen = set(i.interval_start.date() for i in intervals)
    days_of_data = len(dates_seen)
    
    # Annualize hourly consumption
    scale_factor = 365 / max(days_of_data, 1)
    annual_hourly = {h: hourly_consumption[h] * scale_factor for h in range(24)}
    
    # Solar production curve (rough approximation)
    # Peak at 12 PM, taper to zero at 6 AM and 6 PM
    solar_curve = {
        0: 0, 1: 0, 2: 0, 3: 0, 4: 0, 5: 0,
        6: 0.02, 7: 0.05, 8: 0.10, 9: 0.15, 10: 0.18, 11: 0.20,
        12: 0.20, 13: 0.18, 14: 0.15, 15: 0.10, 16: 0.05, 17: 0.02,
        18: 0, 19: 0, 20: 0, 21: 0, 22: 0, 23: 0,
    }
    
    # Normalize curve to sum to 1.0
    curve_sum = sum(solar_curve.values())
    solar_curve = {h: v / curve_sum for h, v in solar_curve.items()}
    
    # Calculate hourly production
    hourly_production = {h: production_kwh * solar_curve[h] for h in range(24)}
    
    # Calculate net grid consumption and exports
    grid_cost = 0.0
    export_credit = 0.0
    
    for hour in range(24):
        consumption = annual_hourly.get(hour, 0)
        production = hourly_production[hour]
        
        if production >= consumption:
            # Net export
            export = production - consumption
            export_credit += export * NEM_3_EXPORT_RATE
        else:
            # Net grid draw
            net_draw = consumption - production
            # Use average of weekday/weekend for simplicity
            rate = (get_tou_rate(hour, False, EV_TOU_5) + 
                    get_tou_rate(hour, True, EV_TOU_5)) / 2
            grid_cost += net_draw * rate
    
    return ppa_cost, grid_cost, export_credit


def calculate_solar_roi(
    sizing: SolarSizingOutput,
    intervals: List["UtilityIntervalData"],
    ppa_rate: float = DEFAULT_PPA_RATE,
) -> SolarROIOutput:
    """
    Calculate complete ROI for solar PPA installation.
    
    Compares current TOU-DR1 costs to post-solar costs (PPA + EV-TOU-5 grid).
    
    Args:
        sizing: SolarSizingOutput with system specifications
        intervals: List of UtilityIntervalData records
        ppa_rate: PPA rate per kWh (default $0.32)
        
    Returns:
        SolarROIOutput with savings breakdown
    """
    # Current annual cost
    current_annual_cost = calculate_current_annual_cost(intervals)
    
    # Post-solar costs
    ppa_cost, grid_cost, export_credit = calculate_post_solar_cost(
        intervals, sizing.annual_production_kwh, ppa_rate
    )
    
    # Total post-solar cost
    post_solar_annual_cost = ppa_cost + grid_cost - export_credit
    
    # Annual savings
    annual_savings = current_annual_cost - post_solar_annual_cost
    
    logger.info(
        "Solar ROI: Current $%.0f → Post-solar $%.0f (PPA $%.0f + Grid $%.0f - Export $%.0f) = Savings $%.0f",
        current_annual_cost, post_solar_annual_cost,
        ppa_cost, grid_cost, export_credit, annual_savings
    )
    
    return SolarROIOutput(
        annual_savings_usd=annual_savings,
        current_annual_cost=current_annual_cost,
        post_solar_annual_cost=post_solar_annual_cost,
        ppa_annual_cost=ppa_cost,
        grid_annual_cost=grid_cost,
        payback_years=0.0,  # No upfront cost with PPA
    )
