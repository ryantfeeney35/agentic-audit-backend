import math
import pytest
from pydantic import ValidationError
from agents.roi import (
    calculate_attic_insulation_roi,
    AtticInsulationROIInput,
)


def test_roi_insulation_example():
    # Example parity per spec
    inp = AtticInsulationROIInput(
        area_sqft=1200,
        current_r_value=13,
        target_r_value=38,
        energy_rate_usd_per_kwh=0.20,
        net_upgrade_cost_usd=2500,
        analysis_horizon_years=25,
        climate="mild",
    )

    res = calculate_attic_insulation_roi(inp)

    assert abs(res.annual_kwh_saved - 540.00) <= 0.01
    assert abs(res.annual_savings_usd - 108.00) <= 0.01
    assert abs(res.lifetime_savings_usd - 2700.00) <= 0.01
    assert res.roi_percent is not None and abs(res.roi_percent - 8.00) <= 0.01
    assert res.payback_years is not None and abs(res.payback_years - 23.15) <= 0.05


def test_roi_insulation_edge_cases():
    # AREA_ZERO - Pydantic validates area_sqft > 0
    with pytest.raises(ValidationError) as exc_info:
        AtticInsulationROIInput(
            area_sqft=0,
            current_r_value=13,
            target_r_value=38,
            energy_rate_usd_per_kwh=0.20,
            net_upgrade_cost_usd=2500,
        )
    assert "area_sqft" in str(exc_info.value)

    # R_NO_IMPROVEMENT - valid input but no R improvement yields zero savings with error
    res = calculate_attic_insulation_roi(
        AtticInsulationROIInput(
            area_sqft=1200,
            current_r_value=38,
            target_r_value=38,
            energy_rate_usd_per_kwh=0.20,
            net_upgrade_cost_usd=2500,
        )
    )
    assert res.error is not None
    assert res.annual_savings_usd == 0
    assert res.annual_kwh_saved == 0

    # COST_ZERO - Pydantic validates net_upgrade_cost_usd > 0
    with pytest.raises(ValidationError) as exc_info:
        AtticInsulationROIInput(
            area_sqft=1200,
            current_r_value=13,
            target_r_value=38,
            energy_rate_usd_per_kwh=0.20,
            net_upgrade_cost_usd=0,
        )
    assert "net_upgrade_cost_usd" in str(exc_info.value)

    # RATE_ZERO - Pydantic validates energy_rate_usd_per_kwh > 0
    with pytest.raises(ValidationError) as exc_info:
        AtticInsulationROIInput(
            area_sqft=1200,
            current_r_value=13,
            target_r_value=38,
            energy_rate_usd_per_kwh=0.0,
            net_upgrade_cost_usd=2500,
        )
    assert "energy_rate_usd_per_kwh" in str(exc_info.value)
