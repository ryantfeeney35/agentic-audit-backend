from __future__ import annotations

from .constants import (
    CLIMATE_MULTIPLIER,
    KWH_SAVED_PER_SQFT_PER_R_DELTA_BASE,
)
from .types import AtticInsulationROIInput, ROIResult


def _zero_with_error(reason: str, notes: list[str]) -> ROIResult:
    return ROIResult(
        annual_kwh_saved=0.0,
        annual_savings_usd=0.0,
        lifetime_savings_usd=0.0,
        payback_years=None,
        roi_percent=None,
        notes=notes,
        error=reason,
    )


def calculate_attic_insulation_roi(inp: AtticInsulationROIInput) -> ROIResult:
    """Compute deterministic ROI for an attic insulation R-value upgrade.

    Exact rules per spec:
    - r_delta = max(target - current, 0)
    - Zero/invalid inputs yield zero savings and an error, no exceptions.
    - annual_kwh_saved = round(area * (BASE * r_delta) * climate_multiplier, 2)
    - annual_savings_usd = round(annual_kwh_saved * energy_rate, 2)
    - lifetime_savings_usd = round(annual_savings_usd * horizon_years, 2)
    - payback_years = round(net_cost / annual_savings_usd, 2) if annual_savings_usd > 0 else None
    - roi_percent = round(((lifetime - net_cost) / net_cost) * 100, 2) if net_cost > 0 else None
    """

    # Compute delta R, clamp at 0
    r_delta = max(inp.target_r_value - inp.current_r_value, 0)

    # Validate inputs and produce zeroed result when any critical value is non-positive or no improvement.
    invalid_reasons: list[str] = []
    if inp.area_sqft <= 0:
        invalid_reasons.append("area_sqft must be > 0")
    if inp.energy_rate_usd_per_kwh <= 0:
        invalid_reasons.append("energy_rate_usd_per_kwh must be > 0")
    if inp.net_upgrade_cost_usd <= 0:
        invalid_reasons.append("net_upgrade_cost_usd must be > 0")
    if r_delta == 0:
        invalid_reasons.append("no R-value improvement (target <= current)")

    base_notes: list[str] = [
        f"Inputs: area_sqft={inp.area_sqft}, current_r={inp.current_r_value}, target_r={inp.target_r_value}",
        f"Derived: r_delta={r_delta}",
        f"Multipliers: base_per_sqft_per_R={KWH_SAVED_PER_SQFT_PER_R_DELTA_BASE}, climate={inp.climate}",
        f"Rates/Costs: energy_rate=${inp.energy_rate_usd_per_kwh}/kWh, net_cost=${inp.net_upgrade_cost_usd}",
        f"Horizon: {inp.analysis_horizon_years} years",
    ]

    if invalid_reasons:
        reason = "; ".join(invalid_reasons)
        base_notes.append("Computation skipped due to invalid inputs.")
        return _zero_with_error(reason=reason, notes=base_notes)

    # Energy savings per sqft
    kwh_per_sqft = KWH_SAVED_PER_SQFT_PER_R_DELTA_BASE * r_delta
    climate_multiplier = CLIMATE_MULTIPLIER.get(inp.climate, 1.0)

    annual_kwh_saved = round(inp.area_sqft * kwh_per_sqft * climate_multiplier, 2)
    annual_savings_usd = round(annual_kwh_saved * inp.energy_rate_usd_per_kwh, 2)
    lifetime_savings_usd = round(annual_savings_usd * inp.analysis_horizon_years, 2)

    payback_years = round(inp.net_upgrade_cost_usd / annual_savings_usd, 2) if annual_savings_usd > 0 else None
    roi_percent = (
        round(((lifetime_savings_usd - inp.net_upgrade_cost_usd) / inp.net_upgrade_cost_usd) * 100, 2)
        if inp.net_upgrade_cost_usd > 0
        else None
    )

    notes = base_notes + [
        f"Formula: kwh_per_sqft = BASE * r_delta = {KWH_SAVED_PER_SQFT_PER_R_DELTA_BASE} * {r_delta}",
        f"Annual kWh = area * kwh_per_sqft * climate = {inp.area_sqft} * {kwh_per_sqft:.3f} * {climate_multiplier}",
        f"Annual $ = annual_kWh * rate = {annual_kwh_saved} * ${inp.energy_rate_usd_per_kwh}",
        f"Lifetime $ = annual$ * horizon = ${annual_savings_usd} * {inp.analysis_horizon_years}",
        "Payback = net_cost / annual$ (if annual$ > 0)",
        "ROI% = ((lifetime$ - net_cost) / net_cost) * 100 (if net_cost > 0)",
    ]

    return ROIResult(
        annual_kwh_saved=annual_kwh_saved,
        annual_savings_usd=annual_savings_usd,
        lifetime_savings_usd=lifetime_savings_usd,
        payback_years=payback_years,
        roi_percent=roi_percent,
        notes=notes,
        error=None,
    )
