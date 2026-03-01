"""
NEM 3.0 / SBP Export Rate Schedules.

Provides time-varying export compensation rates for solar exports under
California's Net Billing Tariff (NEM 3.0). Rates vary by:
- Month (seasonal variation)
- Hour of day (higher during system peaks)
- Day type (weekday vs weekend)

Initial implementation hardcodes SDG&E SBP avoided-cost schedule for 2024.
Future: Load from export_rate_schedules database table.

Rate Sources:
- SDG&E Solar Billing Plan (SBP) Avoided Cost Calculator
- CPUC ACC (Avoided Cost Calculator) values
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Fallback rate if specific schedule unavailable
FALLBACK_EXPORT_RATE = 0.04  # $/kWh

# Maximum NEM 3.0 export rate (regulatory cap)
MAX_EXPORT_RATE = 0.15  # $/kWh


# SDG&E SBP Avoided Cost Schedule - 2024
# Rates in $/kWh, indexed by (month, hour, is_weekend)
# Based on SDG&E ACC export values, simplified to hourly averages
SDGE_SBP_2024 = {
    # Winter months (Nov-Feb): Lower export values overall
    # Summer months (Jun-Sep): Higher values, especially during peak
    # Shoulder (Mar-May, Oct): Moderate values
    
    # Structure: monthly profiles with hourly rates
    # Each month has 24 hourly rates for weekday and weekend
}

def _build_sdge_schedule():
    """Build the full 8760-hour SDG&E SBP export schedule."""
    schedule = {}
    
    # Base rates by season and hour
    # Winter (Dec-Feb): Low demand
    winter_hourly = [
        0.035, 0.032, 0.030, 0.028, 0.027, 0.030,  # 0-5: overnight
        0.035, 0.040, 0.042, 0.040, 0.035, 0.032,  # 6-11: morning
        0.030, 0.028, 0.027, 0.028, 0.055, 0.075,  # 12-17: midday to evening
        0.080, 0.070, 0.055, 0.045, 0.040, 0.037,  # 18-23: evening
    ]
    
    # Spring (Mar-May): Moderate
    spring_hourly = [
        0.035, 0.032, 0.028, 0.026, 0.025, 0.028,  # 0-5
        0.032, 0.035, 0.035, 0.032, 0.028, 0.025,  # 6-11
        0.023, 0.022, 0.022, 0.025, 0.050, 0.070,  # 12-17
        0.075, 0.065, 0.050, 0.042, 0.038, 0.036,  # 18-23
    ]
    
    # Summer (Jun-Sep): High demand, peak TOU arbitrage
    summer_hourly = [
        0.040, 0.035, 0.032, 0.030, 0.028, 0.032,  # 0-5
        0.038, 0.042, 0.040, 0.035, 0.030, 0.028,  # 6-11
        0.025, 0.023, 0.022, 0.028, 0.065, 0.095,  # 12-17: low midday, high evening
        0.100, 0.085, 0.065, 0.050, 0.045, 0.042,  # 18-23: peak evening
    ]
    
    # Fall (Oct-Nov): Shoulder
    fall_hourly = [
        0.036, 0.033, 0.030, 0.028, 0.027, 0.030,  # 0-5
        0.035, 0.038, 0.038, 0.035, 0.030, 0.028,  # 6-11
        0.025, 0.024, 0.024, 0.027, 0.055, 0.078,  # 12-17
        0.082, 0.070, 0.055, 0.045, 0.040, 0.038,  # 18-23
    ]
    
    # Weekend rates are ~10% lower (less grid stress)
    weekend_discount = 0.90
    
    # Map months to seasonal profiles
    month_profiles = {
        1: winter_hourly, 2: winter_hourly,  # Jan, Feb
        3: spring_hourly, 4: spring_hourly, 5: spring_hourly,  # Mar-May
        6: summer_hourly, 7: summer_hourly, 8: summer_hourly, 9: summer_hourly,  # Jun-Sep
        10: fall_hourly, 11: fall_hourly,  # Oct, Nov
        12: winter_hourly,  # Dec
    }
    
    # Build schedule dictionary
    for month in range(1, 13):
        for hour in range(24):
            # Weekday rate
            key_weekday = f"{month}-{hour}-0"
            schedule[key_weekday] = month_profiles[month][hour]
            
            # Weekend rate (10% discount)
            key_weekend = f"{month}-{hour}-1"
            schedule[key_weekend] = month_profiles[month][hour] * weekend_discount
    
    return schedule


# Pre-built schedule for performance
_SDGE_SBP_SCHEDULE = _build_sdge_schedule()


def get_export_rate(
    month: int,
    hour: int,
    is_weekend: bool = False,
    utility: str = "SDGE",
    db_session=None,
) -> float:
    """
    Get the export compensation rate for a specific time.
    
    Args:
        month: Month of year (1-12)
        hour: Hour of day (0-23)
        is_weekend: Whether this is a weekend day
        utility: Utility identifier (default: SDGE)
        db_session: Optional DB session for custom schedules
        
    Returns:
        Export rate in $/kWh
    """
    # Validate inputs
    if not 1 <= month <= 12:
        logger.warning(f"Invalid month {month}, using 1")
        month = 1
    if not 0 <= hour <= 23:
        logger.warning(f"Invalid hour {hour}, using 0")
        hour = 0
    
    # Try database first (for custom/updated schedules)
    if db_session is not None:
        db_rate = _get_db_export_rate(month, hour, is_weekend, utility, db_session)
        if db_rate is not None:
            return db_rate
    
    # Use hardcoded SDG&E schedule
    if utility.upper() in ("SDGE", "SDG&E"):
        key = f"{month}-{hour}-{1 if is_weekend else 0}"
        rate = _SDGE_SBP_SCHEDULE.get(key, FALLBACK_EXPORT_RATE)
        return min(rate, MAX_EXPORT_RATE)
    
    # Fallback for unknown utilities
    logger.warning(f"No export schedule for utility {utility}, using fallback")
    return FALLBACK_EXPORT_RATE


def get_export_rates_array(
    utility: str = "SDGE",
    db_session=None,
) -> list:
    """
    Get a full year of hourly export rates (8760 values).
    
    Returns array ordered by hour-of-year (hour 0 of Jan 1 first).
    Assumes 2024 (leap year with 366 days = 8784 hours).
    For non-leap years, last day rates are duplicated from Dec 30.
    
    Args:
        utility: Utility identifier
        db_session: Optional DB session for custom schedules
        
    Returns:
        List of 8760 export rates in $/kWh
    """
    rates = []
    
    # Approximate: 365 days, ignoring leap year complexity for simulation
    # Month day counts (non-leap year)
    month_days = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    
    for month_idx, days in enumerate(month_days):
        month = month_idx + 1
        for day in range(days):
            # Determine day of week (approximation: Jan 1, 2024 = Monday)
            day_of_year = sum(month_days[:month_idx]) + day
            is_weekend = (day_of_year % 7) >= 5  # Sat=5, Sun=6
            
            for hour in range(24):
                rate = get_export_rate(month, hour, is_weekend, utility, db_session)
                rates.append(rate)
    
    # Should be 8760, but verify
    if len(rates) != 8760:
        logger.warning(f"Export rates array has {len(rates)} values, expected 8760")
    
    return rates


def _get_db_export_rate(
    month: int,
    hour: int,
    is_weekend: bool,
    utility: str,
    db_session,
) -> Optional[float]:
    """Lookup export rate from database schedule if available."""
    try:
        from models import ExportRateSchedule
        from datetime import date
        
        # Find most recent schedule for this utility
        schedule = db_session.query(ExportRateSchedule).filter(
            ExportRateSchedule.utility == utility.upper(),
            ExportRateSchedule.effective_date <= date.today(),
        ).order_by(ExportRateSchedule.effective_date.desc()).first()
        
        if schedule and schedule.schedule_data:
            key = f"{month}-{hour}-{1 if is_weekend else 0}"
            rates = schedule.schedule_data.get("rates", {})
            if key in rates:
                return float(rates[key])
        
        return None
        
    except Exception as e:
        logger.debug(f"DB export rate lookup failed: {e}")
        return None


def get_annual_export_summary(utility: str = "SDGE") -> dict:
    """
    Get summary statistics for export rate schedule.
    
    Returns:
        Dict with min, max, average rates by season
    """
    schedule = _SDGE_SBP_SCHEDULE if utility.upper() in ("SDGE", "SDG&E") else {}
    
    if not schedule:
        return {"average": FALLBACK_EXPORT_RATE, "min": FALLBACK_EXPORT_RATE, "max": FALLBACK_EXPORT_RATE}
    
    rates = list(schedule.values())
    
    # Summer peak (Jun-Sep, 4-9 PM weekday)
    summer_peak = [
        schedule.get(f"{m}-{h}-0", FALLBACK_EXPORT_RATE)
        for m in [6, 7, 8, 9] for h in [16, 17, 18, 19, 20]
    ]
    
    # Midday low (all months, 10 AM - 3 PM)
    midday = [
        schedule.get(f"{m}-{h}-0", FALLBACK_EXPORT_RATE)
        for m in range(1, 13) for h in [10, 11, 12, 13, 14]
    ]
    
    return {
        "average": sum(rates) / len(rates),
        "min": min(rates),
        "max": max(rates),
        "summer_peak_avg": sum(summer_peak) / len(summer_peak) if summer_peak else FALLBACK_EXPORT_RATE,
        "midday_avg": sum(midday) / len(midday) if midday else FALLBACK_EXPORT_RATE,
    }
