"""
SDG&E Time-of-Use Rate Schedules for Solar ROI Calculations.

Contains rate structures for:
- TOU-DR1: Standard residential TOU rate (pre-solar)
- EV-TOU-5: Electric vehicle / solar-friendly rate (post-solar)

Rate data sourced from SDG&E rate schedules effective 2025.
https://www.sdge.com/residential/pricing-plans/about-our-pricing-plans/
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict


class TOUPeriod(str, Enum):
    """Time-of-use period classifications."""
    SUPER_OFF_PEAK = "super_off_peak"
    OFF_PEAK = "off_peak"
    ON_PEAK = "on_peak"


@dataclass
class RateSchedule:
    """A TOU rate schedule with period-based rates."""
    name: str
    description: str
    # Rates in $/kWh for each TOU period
    rates: Dict[TOUPeriod, float]
    # Hour ranges for each period (tuple of start, end hours, 0-23)
    # Structure: {period: [(start, end, is_weekend_only), ...]}
    periods_weekday: Dict[TOUPeriod, list]
    periods_weekend: Dict[TOUPeriod, list]


# SDG&E TOU-DR1: Standard Residential TOU Rate
# Effective rates (approximate averages including baseline credits)
TOU_DR1 = RateSchedule(
    name="TOU-DR1",
    description="SDG&E Standard Residential Time-of-Use rate",
    rates={
        TOUPeriod.SUPER_OFF_PEAK: 0.28,  # 12 AM - 6 AM
        TOUPeriod.OFF_PEAK: 0.42,         # 6 AM - 4 PM, 9 PM - 12 AM
        TOUPeriod.ON_PEAK: 0.58,          # 4 PM - 9 PM
    },
    periods_weekday={
        TOUPeriod.SUPER_OFF_PEAK: [(0, 6)],     # 12 AM - 6 AM
        TOUPeriod.OFF_PEAK: [(6, 16), (21, 24)], # 6 AM - 4 PM, 9 PM - 12 AM
        TOUPeriod.ON_PEAK: [(16, 21)],           # 4 PM - 9 PM
    },
    periods_weekend={
        TOUPeriod.SUPER_OFF_PEAK: [(0, 6)],     # 12 AM - 6 AM
        TOUPeriod.OFF_PEAK: [(6, 16), (21, 24)], # 6 AM - 4 PM, 9 PM - 12 AM
        TOUPeriod.ON_PEAK: [(16, 21)],           # 4 PM - 9 PM (same as weekday)
    },
)


# SDG&E EV-TOU-5: Electric Vehicle TOU Rate (also good for solar)
# Lower super-off-peak rate, good for shifting loads
EV_TOU_5 = RateSchedule(
    name="EV-TOU-5",
    description="SDG&E Electric Vehicle Time-of-Use rate (recommended for solar + battery)",
    rates={
        TOUPeriod.SUPER_OFF_PEAK: 0.12,  # 12 AM - 6 AM (much cheaper)
        TOUPeriod.OFF_PEAK: 0.38,        # 6 AM - 4 PM, 9 PM - 12 AM
        TOUPeriod.ON_PEAK: 0.62,         # 4 PM - 9 PM (slightly higher)
    },
    periods_weekday={
        TOUPeriod.SUPER_OFF_PEAK: [(0, 6)],     # 12 AM - 6 AM
        TOUPeriod.OFF_PEAK: [(6, 16), (21, 24)], # 6 AM - 4 PM, 9 PM - 12 AM
        TOUPeriod.ON_PEAK: [(16, 21)],           # 4 PM - 9 PM
    },
    periods_weekend={
        TOUPeriod.SUPER_OFF_PEAK: [(0, 6)],     # 12 AM - 6 AM
        TOUPeriod.OFF_PEAK: [(6, 16), (21, 24)], # 6 AM - 4 PM, 9 PM - 12 AM
        TOUPeriod.ON_PEAK: [(16, 21)],           # 4 PM - 9 PM
    },
)


# NEM 3.0 export compensation rates (much lower than retail)
NEM_3_EXPORT_RATE = 0.05  # $/kWh average export compensation


def get_tou_period(hour: int, is_weekend: bool = False, schedule: RateSchedule = TOU_DR1) -> TOUPeriod:
    """
    Get the TOU period for a given hour.
    
    Args:
        hour: Hour of the day (0-23)
        is_weekend: Whether this is a weekend day
        schedule: Rate schedule to use
        
    Returns:
        TOUPeriod enum value
    """
    periods = schedule.periods_weekend if is_weekend else schedule.periods_weekday
    
    for period, ranges in periods.items():
        for start, end in ranges:
            if start <= hour < end:
                return period
    
    # Fallback (shouldn't happen with complete schedules)
    return TOUPeriod.OFF_PEAK


def get_tou_rate(hour: int, is_weekend: bool = False, schedule: RateSchedule = TOU_DR1) -> float:
    """
    Get the electricity rate for a given hour.
    
    Args:
        hour: Hour of the day (0-23)
        is_weekend: Whether this is a weekend day
        schedule: Rate schedule to use (default TOU-DR1)
        
    Returns:
        Rate in $/kWh
        
    Example:
        >>> get_tou_rate(14, is_weekend=False)  # 2 PM weekday
        0.42  # Off-peak rate
        >>> get_tou_rate(17, is_weekend=False)  # 5 PM weekday
        0.58  # On-peak rate
    """
    period = get_tou_period(hour, is_weekend, schedule)
    return schedule.rates[period]


def calculate_interval_cost(
    usage_kwh: float,
    hour: int,
    is_weekend: bool = False,
    schedule: RateSchedule = TOU_DR1,
) -> float:
    """
    Calculate cost for a single interval's usage.
    
    Args:
        usage_kwh: Energy consumption in kWh
        hour: Hour of the day (0-23)
        is_weekend: Whether this is a weekend day
        schedule: Rate schedule to use
        
    Returns:
        Cost in USD
    """
    rate = get_tou_rate(hour, is_weekend, schedule)
    return usage_kwh * rate
