"""
Financial Models for Solar + Battery ROI Calculations.

Provides two recommendation paths:
1. Cash Purchase: Maximize IRR over 25-year analysis period
2. PPA (Power Purchase Agreement): Minimize Year-1 total cost

Financial Assumptions (California 2024):
- Solar installed cost: $2.50/W
- Battery installed cost: $500/kWh
- Federal ITC: 30% tax credit
- PPA rate: $0.18/kWh for all generation
- Analysis period: 25 years (Cash), 1 year (PPA)
- No escalation factors (conservative)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Installation cost assumptions (California 2024)
SOLAR_COST_PER_WATT = 2.50  # $/W installed
BATTERY_COST_PER_KWH = 500  # $/kWh installed

# Federal Investment Tax Credit
ITC_RATE = 0.30  # 30% credit

# PPA assumptions
PPA_RATE_PER_KWH = 0.18  # $/kWh for all solar generation

# Analysis period
CASH_ANALYSIS_YEARS = 25

# Monthly fixed utility charges (approximate)
MONTHLY_FIXED_CHARGE = 10.0  # $/month


@dataclass
class CashPathResult:
    """Financial results for Cash purchase path."""
    
    # System configuration
    pv_kw: float
    battery_kwh: float
    
    # Costs
    system_cost_usd: float  # Total before incentives
    net_cost_usd: float  # After ITC
    solar_cost_usd: float
    battery_cost_usd: float
    
    # Annual metrics
    year1_savings_usd: float
    baseline_annual_cost_usd: float
    post_solar_annual_cost_usd: float
    
    # ROI metrics
    irr_percent: float
    simple_payback_years: float
    npv_25yr_usd: float
    
    # Lifetime metrics
    lifetime_savings_usd: float
    
    # Validity
    is_valid: bool = True
    error_message: Optional[str] = None


@dataclass
class PPAPathResult:
    """Financial results for PPA path."""
    
    # System configuration
    pv_kw: float
    battery_kwh: float
    
    # Annual costs
    annual_ppa_cost_usd: float
    annual_utility_cost_usd: float
    total_annual_cost_usd: float
    
    # Monthly for display
    monthly_ppa_cost_usd: float
    monthly_utility_cost_usd: float
    monthly_total_cost_usd: float
    
    # Comparison
    baseline_annual_cost_usd: float
    year1_savings_usd: float
    
    # Validity
    is_valid: bool = True
    error_message: Optional[str] = None


def calculate_system_cost(pv_kw: float, battery_kwh: float) -> dict:
    """
    Calculate total system installation cost.
    
    Args:
        pv_kw: Solar system size in kW
        battery_kwh: Battery capacity in kWh
        
    Returns:
        Dict with cost breakdown
    """
    solar_cost = pv_kw * 1000 * SOLAR_COST_PER_WATT  # kW to W
    battery_cost = battery_kwh * BATTERY_COST_PER_KWH
    total_cost = solar_cost + battery_cost
    
    return {
        "solar_cost_usd": solar_cost,
        "battery_cost_usd": battery_cost,
        "total_cost_usd": total_cost,
    }


def calculate_net_cost_after_itc(system_cost: float) -> float:
    """
    Calculate net cost after applying federal ITC.
    
    Args:
        system_cost: Total system cost before incentives
        
    Returns:
        Net cost after 30% ITC
    """
    return system_cost * (1 - ITC_RATE)


def calculate_irr(
    net_cost: float,
    annual_savings: float,
    years: int = CASH_ANALYSIS_YEARS,
) -> float:
    """
    Calculate Internal Rate of Return.
    
    Uses numpy's IRR calculation with initial outflow (cost) and
    annual inflows (savings).
    
    Args:
        net_cost: Net upfront cost (positive value)
        annual_savings: Annual savings (positive value)
        years: Analysis period
        
    Returns:
        IRR as percentage (e.g., 12.5 for 12.5%)
        Returns -100 if IRR cannot be calculated (negative savings)
    """
    if annual_savings <= 0:
        return -100.0
    
    if net_cost <= 0:
        return 100.0  # No cost = infinite return
    
    # Cash flows: initial outflow, then annual inflows
    cash_flows = [-net_cost] + [annual_savings] * years
    
    try:
        # Try numpy financial IRR
        try:
            import numpy_financial as npf
            irr = npf.irr(cash_flows)
        except ImportError:
            # Fallback to manual calculation
            irr = _manual_irr(cash_flows)
        
        if irr is None or np.isnan(irr):
            return -100.0
        
        return float(irr * 100)  # Convert to percentage
        
    except Exception as e:
        logger.warning(f"IRR calculation failed: {e}")
        return -100.0


def _manual_irr(cash_flows: list, tolerance: float = 0.0001, max_iterations: int = 100) -> Optional[float]:
    """
    Manual IRR calculation using Newton-Raphson method.
    
    Fallback when numpy_financial is not available.
    """
    # Initial guess based on simple payback
    if cash_flows[0] >= 0 or sum(cash_flows[1:]) <= 0:
        return None
    
    rate = 0.1  # Start with 10% guess
    
    for _ in range(max_iterations):
        # Calculate NPV and derivative
        npv = sum(cf / (1 + rate) ** i for i, cf in enumerate(cash_flows))
        npv_prime = sum(-i * cf / (1 + rate) ** (i + 1) for i, cf in enumerate(cash_flows))
        
        if abs(npv_prime) < 1e-10:
            break
        
        new_rate = rate - npv / npv_prime
        
        if abs(new_rate - rate) < tolerance:
            return new_rate
        
        rate = new_rate
        
        # Bound check
        if rate < -0.99 or rate > 10:
            return None
    
    return rate


def calculate_npv(
    net_cost: float,
    annual_savings: float,
    discount_rate: float = 0.05,
    years: int = CASH_ANALYSIS_YEARS,
) -> float:
    """
    Calculate Net Present Value.
    
    Args:
        net_cost: Net upfront cost
        annual_savings: Annual savings
        discount_rate: Discount rate (default 5%)
        years: Analysis period
        
    Returns:
        NPV in USD
    """
    pv_savings = sum(annual_savings / (1 + discount_rate) ** i for i in range(1, years + 1))
    return pv_savings - net_cost


def calculate_simple_payback(net_cost: float, annual_savings: float) -> float:
    """
    Calculate simple payback period.
    
    Args:
        net_cost: Net upfront cost
        annual_savings: Annual savings
        
    Returns:
        Payback period in years (999 if savings <= 0)
    """
    if annual_savings <= 0:
        return 999.0
    return net_cost / annual_savings


def calculate_cash_path(
    pv_kw: float,
    battery_kwh: float,
    baseline_annual_cost: float,
    post_solar_annual_cost: float,
) -> CashPathResult:
    """
    Calculate Cash purchase path financial metrics.
    
    Args:
        pv_kw: Solar system size
        battery_kwh: Battery capacity
        baseline_annual_cost: Annual utility cost without solar
        post_solar_annual_cost: Annual utility cost with solar
        
    Returns:
        CashPathResult with all financial metrics
    """
    # Calculate costs
    costs = calculate_system_cost(pv_kw, battery_kwh)
    system_cost = costs["total_cost_usd"]
    net_cost = calculate_net_cost_after_itc(system_cost)
    
    # Calculate savings
    annual_savings = baseline_annual_cost - post_solar_annual_cost
    
    # Calculate ROI metrics
    irr = calculate_irr(net_cost, annual_savings)
    payback = calculate_simple_payback(net_cost, annual_savings)
    npv = calculate_npv(net_cost, annual_savings)
    lifetime_savings = annual_savings * CASH_ANALYSIS_YEARS - net_cost
    
    return CashPathResult(
        pv_kw=pv_kw,
        battery_kwh=battery_kwh,
        system_cost_usd=system_cost,
        net_cost_usd=net_cost,
        solar_cost_usd=costs["solar_cost_usd"],
        battery_cost_usd=costs["battery_cost_usd"],
        year1_savings_usd=annual_savings,
        baseline_annual_cost_usd=baseline_annual_cost,
        post_solar_annual_cost_usd=post_solar_annual_cost,
        irr_percent=irr,
        simple_payback_years=payback,
        npv_25yr_usd=npv,
        lifetime_savings_usd=lifetime_savings,
        is_valid=irr > 0,
        error_message=None if irr > 0 else "Negative IRR - solar not cost-effective",
    )


def calculate_ppa_path(
    pv_kw: float,
    battery_kwh: float,
    annual_production_kwh: float,
    post_solar_utility_cost: float,
    baseline_annual_cost: float,
    ppa_rate: float = PPA_RATE_PER_KWH,
) -> PPAPathResult:
    """
    Calculate PPA path financial metrics.
    
    Args:
        pv_kw: Solar system size
        battery_kwh: Battery capacity  
        annual_production_kwh: Total solar production
        post_solar_utility_cost: Utility cost after solar (grid only)
        baseline_annual_cost: Annual cost without solar
        ppa_rate: PPA rate per kWh
        
    Returns:
        PPAPathResult with all financial metrics
    """
    # Calculate PPA cost (pay for all generation)
    annual_ppa_cost = annual_production_kwh * ppa_rate
    
    # Total annual cost = PPA + utility
    total_annual_cost = annual_ppa_cost + post_solar_utility_cost
    
    # Monthly breakdown
    monthly_ppa = annual_ppa_cost / 12
    monthly_utility = post_solar_utility_cost / 12
    monthly_total = total_annual_cost / 12
    
    # Savings vs baseline
    year1_savings = baseline_annual_cost - total_annual_cost
    
    return PPAPathResult(
        pv_kw=pv_kw,
        battery_kwh=battery_kwh,
        annual_ppa_cost_usd=annual_ppa_cost,
        annual_utility_cost_usd=post_solar_utility_cost,
        total_annual_cost_usd=total_annual_cost,
        monthly_ppa_cost_usd=monthly_ppa,
        monthly_utility_cost_usd=monthly_utility,
        monthly_total_cost_usd=monthly_total,
        baseline_annual_cost_usd=baseline_annual_cost,
        year1_savings_usd=year1_savings,
        is_valid=year1_savings > 0,
        error_message=None if year1_savings > 0 else "PPA cost exceeds savings",
    )
