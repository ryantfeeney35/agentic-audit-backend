from __future__ import annotations

import re
from typing import List

from ..schemas import StepType, Recommendation
from .types import AtticInsulationROIInput
from .insulation import calculate_attic_insulation_roi


def enrich_recommendations_with_roi(domain: "StepType", recs: list["Recommendation"], context: dict) -> list["Recommendation"]:
    """Return a new list of recommendations enriched with deterministic ROI values where applicable.

    Only applies to Insulation domain right now. Copies input items; no in-place mutation.
    """
    if not isinstance(recs, list) or not recs:
        return recs or []

    if domain == StepType.INSULATION:
        return _enrich_insulation(recs, context or {})

    # other domains: passthrough
    return [r for r in recs]


def _enrich_insulation(recs: List[Recommendation], context: dict) -> List[Recommendation]:
    out: List[Recommendation] = []

    # Determine area: prefer explicit attic_area_sqft, else derive from property_sqft using 35% heuristic
    area_sqft = None
    try:
        val = context.get("attic_area_sqft")
        if val and float(val) > 0:
            area_sqft = float(val)
    except Exception:
        area_sqft = None

    if area_sqft is None:
        try:
            psqft = context.get("property_sqft")
            if psqft and float(psqft) > 0:
                # Default fraction documented: 35% of total property sqft attributed to attic area
                area_sqft = round(float(psqft) * 0.35, 2)
        except Exception:
            area_sqft = None

    # Determine R-values
    current_r_value = context.get("attic_current_r", 13)
    target_r_value = context.get("attic_target_r", 38)

    # Determine billing rate and cost
    energy_rate = context.get("energy_rate_usd_per_kwh", 0.20)
    net_cost = context.get("net_upgrade_cost_usd")
    climate = context.get("climate", "mild")
    horizon = context.get("analysis_horizon_years", 25)

    # Precompile a simple "net cost $<number>" parser for fallback
    cost_re = re.compile(r"net cost\s*\$\s*([0-9,]+(?:\.[0-9]{1,2})?)", re.IGNORECASE)

    for r in recs:
        # copy-through by default
        new_r = r.model_copy(deep=True)

        # Apply only for insulation step type mentioning attic in summary or source
        summary_lc = (r.summary or "").lower()
        source_lc = (r.source or "").lower()
        is_attic = ("attic" in summary_lc) or ("attic" in source_lc)
        if r.step_type != StepType.INSULATION or not is_attic:
            out.append(new_r)
            continue

        # Guard: require area and current R; target defaults to 38 if not provided
        local_area = area_sqft
        if local_area is None:
            out.append(new_r)  # ROI skipped: missing area.
            continue

        if current_r_value is None:
            out.append(new_r)  # ROI skipped: missing current R.
            continue

        local_cost = net_cost
        if local_cost is None:
            # attempt parse from summary
            m = cost_re.search(r.summary or "")
            if m:
                try:
                    local_cost = float(m.group(1).replace(",", ""))
                except Exception:
                    local_cost = None
        if local_cost is None:
            out.append(new_r)  # ROI skipped: missing net cost.
            continue

        # Build input and compute
        roi_input = AtticInsulationROIInput(
            area_sqft=float(local_area),
            current_r_value=float(current_r_value),
            target_r_value=float(target_r_value),
            energy_rate_usd_per_kwh=float(energy_rate),
            net_upgrade_cost_usd=float(local_cost),
            analysis_horizon_years=int(horizon),
            climate=str(climate),
        )

        res = calculate_attic_insulation_roi(roi_input)

        # Update fields on the copy; preserve any existing non-null values from the agent if present
        new_r = new_r.model_copy(
            update={
                "annual_savings_usd": res.annual_savings_usd,
                "upgrade_cost_usd": float(local_cost),
                "payback_years": res.payback_years,
                "source": (new_r.source or "roi:insulation.attic@v1"),
            }
        )

        out.append(new_r)

    return out
