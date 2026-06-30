"""Build a :class:`HesBuilding` from a stored audit.

Precedence for every field:
  1. Explicit auditor input in ``Audit.hes_inputs`` (the HES supplemental form).
  2. Inference from existing audit steps / AI summaries / room measurements.
  3. A safe default baked into the model (so HPXML always serializes).

The function never raises on missing data — that is the gap analyzer's job
(see gaps.py).  It returns the best-available model plus relies on
``conditioned_floor_area_derived`` etc. to signal low-confidence values.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import lookups as L
from .hes_model import (
    Address,
    AirInfiltration,
    AirLeakageQuality,
    CoolingSystem,
    Duct,
    DuctLocation,
    Foundation,
    HeatingSystem,
    HesBuilding,
    Photovoltaics,
    Roof,
    RoofType,
    Wall,
    WaterHeater,
    Windows,
)


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _steps_by_type(audit) -> Dict[str, list]:
    """Group an audit's steps by a normalized lower-case step_type."""
    grouped: Dict[str, list] = {}
    for step in getattr(audit, "steps", []) or []:
        key = (step.step_type or "").strip().lower()
        grouped.setdefault(key, []).append(step)
    return grouped


def _first(grouped: Dict[str, list], *types):
    for t in types:
        steps = grouped.get(t)
        if steps:
            return steps[0]
    return None


def _step_data(step) -> Dict[str, Any]:
    """Merge a step's structured AI summary with its raw meta (meta wins)."""
    if step is None:
        return {}
    merged: Dict[str, Any] = {}
    merged.update(_as_dict(getattr(step, "ai_summary", None)))
    merged.update(_as_dict(getattr(step, "meta", None)))
    return merged


def build_hes_model(audit, hes_inputs: Optional[Dict[str, Any]] = None) -> HesBuilding:
    """Construct the intermediate HES building model for an audit.

    ``hes_inputs`` defaults to ``audit.hes_inputs``; pass an override for tests.
    """
    hi: Dict[str, Any] = _as_dict(
        hes_inputs if hes_inputs is not None else getattr(audit, "hes_inputs", None)
    )
    prop = getattr(audit, "property", None)
    grouped = _steps_by_type(audit)

    model = HesBuilding(audit_id=audit.id)

    # -- About / identity ---------------------------------------------------
    if prop is not None:
        model.address = Address(
            street=prop.street, city=prop.city, state=prop.state, zip_code=prop.zip_code
        )
    model.assessment_date = (
        hi.get("assessment_date")
        or (audit.date.isoformat() if getattr(audit, "date", None) else None)
    )
    model.assessment_type = hi.get("assessment_type", "initial")
    model.year_built = hi.get("year_built") or getattr(prop, "year_built", None)
    model.bedrooms = hi.get("bedrooms")
    model.num_floors_above_grade = hi.get("num_floors_above_grade")

    # Conditioned floor area: explicit override, else derive from gross sqft.
    if hi.get("conditioned_floor_area"):
        model.conditioned_floor_area = int(hi["conditioned_floor_area"])
        model.conditioned_floor_area_derived = False
    else:
        gross = getattr(prop, "sqft", None)
        model.conditioned_floor_area = L.derive_conditioned_area(gross)
        model.conditioned_floor_area_derived = model.conditioned_floor_area is not None

    model.front_orientation = (
        L.normalize_orientation(hi.get("front_orientation"))
        or _infer_front_orientation(grouped)
    )

    # -- Enclosure ----------------------------------------------------------
    model.air_infiltration = _map_air_infiltration(hi)
    model.roof = _map_roof(hi, grouped)
    model.foundation = _map_foundation(hi)
    model.walls = _map_walls(hi, grouped)
    model.windows = _map_windows(hi, grouped)

    # -- Systems ------------------------------------------------------------
    hvac = _step_data(_first(grouped, "hvac"))
    model.heating = _map_heating(hi, hvac)
    model.cooling = _map_cooling(hi, hvac)
    model.ducts = _map_ducts(hi, hvac)
    model.water_heater = _map_water_heater(hi)
    model.pv = _map_pv(hi, grouped)

    return model


# ---------------------------------------------------------------------------
# Section mappers
# ---------------------------------------------------------------------------
def _infer_front_orientation(grouped):
    for step in grouped.get("exterior", []):
        data = _step_data(step)
        side = (data.get("house_side") or "").lower()
        if "front" in side:
            return L.normalize_orientation(data.get("orientation"))
    return None


def _map_air_infiltration(hi: Dict[str, Any]) -> AirInfiltration:
    air = _as_dict(hi.get("air_infiltration"))
    if air.get("blower_door_tested") and air.get("cfm50"):
        return AirInfiltration(
            blower_door_tested=True, cfm50=float(air["cfm50"]), qualitative=None
        )
    qual = air.get("qualitative")
    try:
        qual_enum = AirLeakageQuality(qual) if qual else AirLeakageQuality.AVERAGE
    except ValueError:
        qual_enum = AirLeakageQuality.AVERAGE
    return AirInfiltration(blower_door_tested=False, qualitative=qual_enum)


def _map_roof(hi: Dict[str, Any], grouped) -> Roof:
    roof = Roof()
    rh = _as_dict(hi.get("roof"))
    roof_step = _step_data(_first(grouped, "roof"))

    rtype = rh.get("roof_type")
    if rtype:
        try:
            roof.roof_type = RoofType(rtype)
        except ValueError:
            pass

    color = L.normalize_roof_color(rh.get("color") or roof_step.get("color"))
    if color:
        roof.color = color
    roof.radiant_barrier = bool(rh.get("radiant_barrier", False))

    # Attic-floor / roof-deck insulation from explicit input or insulation steps.
    ins_r = rh.get("ceiling_r")
    if ins_r is None:
        ins_r = _insulation_r_for(grouped, ("attic", "ceiling", "roof"))
    if ins_r is not None:
        if roof.roof_type == RoofType.CATHEDRAL:
            roof.roof_r = float(ins_r)
        else:
            roof.ceiling_r = float(ins_r)
    if rh.get("area_sqft"):
        roof.area_sqft = float(rh["area_sqft"])
    return roof


def _map_foundation(hi: Dict[str, Any]) -> Foundation:
    fh = _as_dict(hi.get("foundation"))
    ftype = L.normalize_foundation(fh.get("type"))
    foundation = Foundation()
    if ftype:
        foundation.foundation_type = ftype
    foundation.insulation_r = float(fh.get("insulation_r") or 0.0)
    if fh.get("area_sqft"):
        foundation.area_sqft = float(fh["area_sqft"])
    return foundation


def _map_walls(hi: Dict[str, Any], grouped) -> List[Wall]:
    walls: List[Wall] = []
    wall_r = _insulation_r_for(grouped, ("wall", "exterior"))
    wh = _as_dict(hi.get("walls"))
    if wh.get("insulation_r") is not None:
        wall_r = float(wh["insulation_r"])

    exterior_steps = grouped.get("exterior", [])
    if exterior_steps:
        for step in exterior_steps:
            data = _step_data(step)
            walls.append(
                Wall(
                    construction=L.normalize_wall_construction(
                        data.get("siding_material") or data.get("siding_type")
                    )
                    or Wall().construction,
                    exterior_finish=L.normalize_exterior_finish(
                        data.get("siding_material") or data.get("siding_type")
                    ),
                    insulation_r=wall_r,
                    orientation=L.normalize_orientation(
                        data.get("orientation") or data.get("house_side")
                    ),
                )
            )
    else:
        walls.append(
            Wall(
                construction=L.normalize_wall_construction(wh.get("construction"))
                or Wall().construction,
                exterior_finish=L.normalize_exterior_finish(wh.get("exterior_finish")),
                insulation_r=wall_r,
            )
        )
    return walls


def _map_windows(hi: Dict[str, Any], grouped) -> Windows:
    windows = Windows()
    wh = _as_dict(hi.get("windows"))

    glazing = L.normalize_glazing(wh.get("glazing"))
    if glazing:
        windows.glazing = glazing
    if wh.get("frame"):
        try:
            from .hes_model import FrameMaterial
            windows.frame = FrameMaterial(wh["frame"])
        except ValueError:
            pass
    windows.low_e = bool(wh.get("low_e", False))
    windows.u_factor = wh.get("u_factor")
    windows.shgc = wh.get("shgc")

    # Window-to-wall ratio: explicit, else average the interior/exterior ratios.
    ratio = wh.get("window_to_wall_ratio")
    if ratio is None:
        ratio = _infer_window_ratio(grouped)
    windows.window_to_wall_ratio = ratio
    if wh.get("area_sqft"):
        windows.area_sqft = float(wh["area_sqft"])
    return windows


def _infer_window_ratio(grouped) -> Optional[float]:
    ratios = []
    for step in grouped.get("interior", []):
        v = _step_data(step).get("wall_to_glass_ratio")
        if isinstance(v, (int, float)):
            ratios.append(float(v))
    for step in grouped.get("exterior", []):
        v = _step_data(step).get("glass_wall_ratio")
        try:
            ratios.append(float(v))
        except (TypeError, ValueError):
            continue
    return round(sum(ratios) / len(ratios), 3) if ratios else None


def _map_heating(hi: Dict[str, Any], hvac: Dict[str, Any]) -> HeatingSystem:
    hh = _as_dict(hi.get("heating"))
    fuel = L.normalize_fuel(hh.get("fuel") or hvac.get("fuel_type"))
    htype = L.normalize_heating_type(
        hh.get("type") or hvac.get("system_type"), fuel
    )
    units, value = L.parse_efficiency(hh.get("efficiency_value_raw") or hvac.get("efficiency_rating"))
    if hh.get("efficiency_units"):
        from .hes_model import EfficiencyUnits
        try:
            units = EfficiencyUnits(hh["efficiency_units"])
        except ValueError:
            pass
    if hh.get("efficiency_value") is not None:
        value = float(hh["efficiency_value"])
    # A shared HVAC label may carry a cooling metric (SEER/EER); that does not
    # describe heating, so drop it and let HES default by age instead.
    from .hes_model import EfficiencyUnits as EU
    if units in (EU.SEER, EU.SEER2, EU.EER):
        units, value = None, None
    return HeatingSystem(
        heating_type=htype or HeatingSystem().heating_type,
        fuel=fuel,
        efficiency_units=units,
        efficiency_value=value,
        year_installed=hh.get("year_installed"),
    )


def _map_cooling(hi: Dict[str, Any], hvac: Dict[str, Any]) -> CoolingSystem:
    ch = _as_dict(hi.get("cooling"))
    ctype = L.normalize_cooling_type(ch.get("type") or hvac.get("system_type"))
    units, value = L.parse_efficiency(ch.get("efficiency_value_raw") or hvac.get("efficiency_rating"))
    if ch.get("efficiency_units"):
        from .hes_model import EfficiencyUnits
        try:
            units = EfficiencyUnits(ch["efficiency_units"])
        except ValueError:
            pass
    if ch.get("efficiency_value") is not None:
        value = float(ch["efficiency_value"])
    # Only keep cooling efficiency if the units actually describe cooling.
    from .hes_model import EfficiencyUnits as EU
    if units in (EU.AFUE, EU.PERCENT, EU.HSPF, EU.HSPF2):
        units, value = None, None
    return CoolingSystem(
        cooling_type=ctype or CoolingSystem().cooling_type,
        efficiency_units=units,
        efficiency_value=value,
        year_installed=ch.get("year_installed"),
    )


def _map_ducts(hi: Dict[str, Any], hvac: Dict[str, Any]) -> List[Duct]:
    explicit = hi.get("ducts")
    if isinstance(explicit, list) and explicit:
        ducts = []
        for d in explicit:
            d = _as_dict(d)
            try:
                loc = DuctLocation(d.get("location"))
            except ValueError:
                loc = DuctLocation.CONDITIONED
            ducts.append(
                Duct(
                    location=loc,
                    fraction=float(d.get("fraction", 1.0)),
                    insulated=bool(d.get("insulated", False)),
                    sealed=bool(d.get("sealed", False)),
                )
            )
        return ducts

    # Infer a single duct run from the HVAC step's ducting condition.
    duct_type = (hvac.get("ducting_type") or "").lower()
    if not duct_type or "none" in duct_type:
        return []
    condition = (hvac.get("ducting_condition") or "").lower()
    return [
        Duct(
            location=DuctLocation.UNCOND_ATTIC,
            fraction=1.0,
            insulated="insulat" in condition and "poor" not in condition,
            sealed="leak" not in condition and "damag" not in condition,
        )
    ]


def _map_water_heater(hi: Dict[str, Any]) -> WaterHeater:
    wh = _as_dict(hi.get("hot_water"))
    wtype = L.normalize_wh_type(wh.get("type"))
    fuel = L.normalize_fuel(wh.get("fuel"))
    return WaterHeater(
        wh_type=wtype or WaterHeater().wh_type,
        fuel=fuel,
        energy_factor=wh.get("energy_factor"),
        year_installed=wh.get("year_installed"),
    )


def _map_pv(hi: Dict[str, Any], grouped) -> Photovoltaics:
    ph = _as_dict(hi.get("pv"))
    present = ph.get("present")
    capacity = ph.get("capacity_kw")

    if present is None:
        elec = _step_data(_first(grouped, "electrical"))
        solar = (elec.get("solar_present") or "").lower()
        if solar == "yes":
            present = True
            size = elec.get("solar_system_size") or ""
            num = L.parse_thickness_inches(size)  # reuses generic number parse
            if num and "kw" in str(size).lower():
                capacity = num
    return Photovoltaics(
        present=bool(present),
        capacity_kw=capacity,
        year_installed=ph.get("year_installed"),
        azimuth=ph.get("azimuth"),
        tilt=ph.get("tilt"),
    )


# ---------------------------------------------------------------------------
# Insulation helper
# ---------------------------------------------------------------------------
def _insulation_r_for(grouped, keywords) -> Optional[float]:
    """Find the best R-value among insulation steps matching a body area."""
    best: Optional[float] = None
    for step in grouped.get("insulation", []):
        data = _step_data(step)
        label = " ".join(
            str(data.get(k, "")) for k in ("location", "area", "label")
        ).lower()
        label += " " + (getattr(step, "label", "") or "").lower()
        if keywords and not any(k in label for k in keywords):
            continue
        r = L.insulation_r_value(
            data.get("insulation_type") or data.get("quality"),
            data.get("thickness_inches") or data.get("thickness"),
        )
        if r is not None and (best is None or r > best):
            best = r
    return best
