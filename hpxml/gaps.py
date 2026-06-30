"""Gap analysis: which HES-required inputs are still missing or low-confidence.

Drives the frontend completeness checklist ("7 fields needed for a Home Energy
Score").  ``required`` gaps block scoring; ``review`` gaps are values we
inferred/derived that the auditor should confirm.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import List

from .hes_model import HesBuilding


@dataclass
class GapField:
    key: str          # machine key, maps to a field in Audit.hes_inputs
    label: str        # human-facing label
    section: str      # grouping for the UI (About, Enclosure, Systems)
    severity: str     # "required" | "review"
    detail: str = ""  # why it's flagged / current inferred value

    def to_dict(self):
        return asdict(self)


def compute_gaps(model: HesBuilding) -> List[GapField]:
    gaps: List[GapField] = []

    def req(key, label, section, missing, detail=""):
        if missing:
            gaps.append(GapField(key, label, section, "required", detail))

    def review(key, label, section, flagged, detail=""):
        if flagged:
            gaps.append(GapField(key, label, section, "review", detail))

    # -- About --------------------------------------------------------------
    addr = model.address
    req("address", "Property address", "About",
        not (addr.street and addr.city and addr.state and addr.zip_code))
    req("year_built", "Year built", "About", not model.year_built)
    req("conditioned_floor_area", "Conditioned floor area", "About",
        not model.conditioned_floor_area)
    review("conditioned_floor_area", "Confirm conditioned floor area", "About",
           model.conditioned_floor_area_derived,
           f"Estimated {model.conditioned_floor_area} sq ft from gross area — confirm.")
    req("num_floors_above_grade", "Number of stories", "About",
        not model.num_floors_above_grade)
    req("bedrooms", "Number of bedrooms", "About", not model.bedrooms)
    req("front_orientation", "Front orientation", "About",
        model.front_orientation is None)

    # -- Enclosure ----------------------------------------------------------
    air = model.air_infiltration
    req("air_infiltration", "Air leakage (blower door or qualitative)", "Enclosure",
        not air.blower_door_tested and air.qualitative is None)

    roof = model.roof
    roof_r = roof.roof_r if roof.roof_type.value == "cath_ceiling" else roof.ceiling_r
    req("roof.insulation", "Attic/roof insulation R-value", "Enclosure", roof_r is None)

    req("foundation.type", "Foundation type", "Enclosure",
        model.foundation.foundation_type is None)

    if not model.walls or all(w.insulation_r is None for w in model.walls):
        req("walls.insulation", "Wall insulation R-value", "Enclosure", True)

    win = model.windows
    req("windows", "Window area/ratio", "Enclosure",
        win.window_to_wall_ratio is None and win.area_sqft is None)
    review("windows.glazing", "Confirm window glazing/frame", "Enclosure",
           win.u_factor is None and win.shgc is None,
           f"Using {win.glazing.value}, low-e={win.low_e} — confirm or enter U/SHGC.")

    # -- Systems ------------------------------------------------------------
    heat = model.heating
    req("heating.type", "Heating system type & fuel", "Systems",
        heat.heating_type.value == "none")
    review("heating.efficiency", "Heating efficiency", "Systems",
           heat.heating_type.value != "none" and heat.efficiency_value is None,
           "No numeric efficiency — HES will use a shipment-weighted default by age.")
    review("heating.year", "Heating install year", "Systems",
           heat.heating_type.value != "none" and heat.year_installed is None)

    cool = model.cooling
    review("cooling.efficiency", "Cooling efficiency", "Systems",
           cool.cooling_type.value != "none" and cool.efficiency_value is None,
           "No numeric SEER/EER — HES will default by age.")
    review("cooling.year", "Cooling install year", "Systems",
           cool.cooling_type.value != "none" and cool.year_installed is None)

    wh = model.water_heater
    req("hot_water.fuel", "Water heater type & fuel", "Systems", wh.fuel is None)
    review("hot_water.efficiency", "Water heater efficiency/age", "Systems",
           wh.energy_factor is None and wh.year_installed is None,
           "No EF or install year — HES will default by age.")

    if model.pv.present:
        review("pv", "Confirm solar PV details", "Systems",
               not model.pv.capacity_kw,
               "PV detected but capacity unknown.")

    return gaps


def is_scoreable(model: HesBuilding) -> bool:
    """True when no *required* gaps remain (review gaps are allowed)."""
    return not any(g.severity == "required" for g in compute_gaps(model))
