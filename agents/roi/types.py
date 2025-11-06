from __future__ import annotations

from typing import List, Optional, Literal
from pydantic import BaseModel, Field
from .constants import LIFESPAN_YEARS_DEFAULT


class ROIBaseInput(BaseModel):
    analysis_horizon_years: int = Field(
        default=LIFESPAN_YEARS_DEFAULT,
        description="Years to project annual savings (simple cumulative).",
    )
    climate: Literal["mild", "moderate", "cold", "very_cold", "hot"] = Field(
        default="mild",
        description="Regional climate adjustment for savings.",
    )


class AtticInsulationROIInput(ROIBaseInput):
    area_sqft: float = Field(..., gt=0, description="Attic insulated area in square feet.")
    current_r_value: float = Field(..., ge=0, description="Existing average attic R-value.")
    target_r_value: float = Field(..., ge=0, description="Target R-value after upgrade.")
    energy_rate_usd_per_kwh: float = Field(..., gt=0, description="Local energy rate.")
    net_upgrade_cost_usd: float = Field(..., gt=0, description="Net cost after incentives.")


class ROIResult(BaseModel):
    annual_kwh_saved: float = 0.0
    annual_savings_usd: float = 0.0
    lifetime_savings_usd: float = 0.0
    payback_years: Optional[float] = None
    roi_percent: Optional[float] = None
    notes: List[str] = Field(default_factory=list)
    error: Optional[str] = None
