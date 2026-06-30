"""Serialize a :class:`HesBuilding` to an HPXML 3.0 document.

The output targets the subset of HPXML consumed by NREL's ``hescore-hpxml``
translator (the program behind the HEScore API ``submit_hpxml_inputs`` call).
HPXML is an ordered schema, so elements are emitted in canonical sequence.

NOTE: final element-level conformance is locked in by the Phase-0 validation
loop — run :func:`hpxml.validation.validate_with_translator` against the real
``hescore-hpxml`` package and iterate.  We also embed the intermediate HES JSON
inside ``<extension>`` to aid that debugging.
"""
from __future__ import annotations

import json
from datetime import datetime
from xml.dom import minidom
from xml.etree import ElementTree as ET

from .hes_model import (
    CoolingType,
    EfficiencyUnits,
    Fuel,
    GlazingLayers,
    HeatingType,
    HesBuilding,
    RoofColor,
    WaterHeaterType,
)

HPXML_NS = "http://hpxmlonline.com/2019/10"
SCHEMA_VERSION = "3.0"

# --- enum -> HPXML string maps ---------------------------------------------
_FUEL = {
    Fuel.ELECTRIC: "electricity",
    Fuel.NATURAL_GAS: "natural gas",
    Fuel.FUEL_OIL: "fuel oil",
    Fuel.PROPANE: "propane",
    Fuel.WOOD_CORD: "wood",
    Fuel.WOOD_PELLET: "wood pellets",
}

_ROOF_COLOR = {
    RoofColor.WHITE: "white",
    RoofColor.LIGHT: "light",
    RoofColor.MEDIUM: "medium",
    RoofColor.MEDIUM_DARK: "medium dark",
    RoofColor.DARK: "dark",
    RoofColor.COOL: "reflective",
}

_GLAZING_LAYERS = {
    GlazingLayers.SINGLE: "single-pane",
    GlazingLayers.DOUBLE: "double-pane",
    GlazingLayers.TRIPLE: "triple-pane",
}

_EFF_UNITS = {
    EfficiencyUnits.AFUE: "AFUE",
    EfficiencyUnits.PERCENT: "Percent",
    EfficiencyUnits.SEER: "SEER",
    EfficiencyUnits.SEER2: "SEER2",
    EfficiencyUnits.EER: "EER",
    EfficiencyUnits.HSPF: "HSPF",
    EfficiencyUnits.HSPF2: "HSPF2",
    EfficiencyUnits.COP: "COP",
}

_WH_TYPE = {
    WaterHeaterType.STORAGE: "storage water heater",
    WaterHeaterType.TANKLESS: "instantaneous water heater",
    WaterHeaterType.HEAT_PUMP: "heat pump water heater",
}


# --- small ET helpers -------------------------------------------------------
def _sub(parent, tag, text=None):
    el = ET.SubElement(parent, f"{{{HPXML_NS}}}{tag}")
    if text is not None:
        el.text = str(text)
    return el


def _sysid(parent, _id):
    """HPXML components carry a <SystemIdentifier id="..."/> as first child."""
    el = ET.SubElement(parent, f"{{{HPXML_NS}}}SystemIdentifier")
    el.set("id", _id)
    return el


def to_hpxml(model: HesBuilding, software_name: str = "AgenticAudit") -> str:
    ET.register_namespace("", HPXML_NS)
    root = ET.Element(f"{{{HPXML_NS}}}HPXML")
    root.set("schemaVersion", SCHEMA_VERSION)

    _header(root, software_name)
    building = _sub(root, "Building")
    bid = ET.SubElement(building, f"{{{HPXML_NS}}}BuildingID")
    bid.set("id", f"bldg-{model.audit_id}")

    _site(building, model)
    details = _sub(building, "BuildingDetails")
    _building_summary(details, model)
    _enclosure(details, model)
    _systems(details, model)

    _extension(details, model)

    return _pretty(root)


# --- document scaffolding ---------------------------------------------------
def _header(root, software_name):
    hdr = _sub(root, "XMLTransactionHeaderInformation")
    _sub(hdr, "XMLType", "HPXML")
    _sub(hdr, "XMLGeneratedBy", software_name)
    _sub(hdr, "CreatedDateAndTime", datetime.utcnow().isoformat() + "Z")
    _sub(hdr, "Transaction", "create")
    info = _sub(root, "SoftwareInfo")
    _sub(info, "SoftwareProgramUsed", software_name)


def _site(building, model):
    site = _sub(building, "Site")
    addr = model.address
    address = _sub(site, "Address")
    _sub(address, "Address1", addr.street or "")
    _sub(address, "CityMunicipality", addr.city or "")
    _sub(address, "StateCode", addr.state or "")
    _sub(address, "ZipCode", addr.zip_code or "")


def _building_summary(details, model: HesBuilding):
    summary = _sub(details, "BuildingSummary")
    site = _sub(summary, "Site")
    if model.front_orientation:
        _sub(site, "OrientationOfFrontOfHome", model.front_orientation.value.replace("_", ""))

    construction = _sub(summary, "BuildingConstruction")
    _sub(construction, "ResidentialFacilityType", "single-family detached")
    if model.num_floors_above_grade:
        _sub(construction, "NumberofConditionedFloorsAboveGrade", model.num_floors_above_grade)
    if model.bedrooms:
        _sub(construction, "NumberofBedrooms", model.bedrooms)
    if model.conditioned_floor_area:
        _sub(construction, "ConditionedFloorArea", model.conditioned_floor_area)
    if model.year_built:
        _sub(construction, "YearBuilt", model.year_built)


# --- enclosure --------------------------------------------------------------
def _enclosure(details, model: HesBuilding):
    enc = _sub(details, "Enclosure")
    _air_infiltration(enc, model)
    _roofs(enc, model)
    _walls(enc, model)
    _foundations(enc, model)
    _windows(enc, model)


def _air_infiltration(enc, model: HesBuilding):
    air = model.air_infiltration
    container = _sub(enc, "AirInfiltration")
    meas = _sub(container, "AirInfiltrationMeasurement")
    _sysid(meas, "infil-1")
    if air.blower_door_tested and air.cfm50:
        _sub(meas, "HousePressure", 50)
        leak = _sub(meas, "BuildingAirLeakage")
        _sub(leak, "UnitofMeasure", "CFM")
        _sub(leak, "AirLeakage", air.cfm50)
    else:
        # Qualitative path — translator reads the HES extension value below.
        ext = _sub(meas, "extension")
        _sub(ext, "QualitativeAirLeakage",
             air.qualitative.value if air.qualitative else "average")


def _roofs(enc, model: HesBuilding):
    roof = model.roof
    container = _sub(enc, "Roofs")
    el = _sub(container, "Roof")
    _sysid(el, "roof-1")
    if roof.area_sqft:
        _sub(el, "Area", roof.area_sqft)
    _sub(el, "RoofColor", _ROOF_COLOR.get(roof.color, "medium"))
    _sub(el, "RadiantBarrier", str(bool(roof.radiant_barrier)).lower())
    r = roof.roof_r if roof.roof_type.value == "cath_ceiling" else roof.ceiling_r
    ins = _sub(el, "Insulation")
    _sysid(ins, "roof-1-ins")
    _sub(ins, "AssemblyEffectiveRValue", r if r is not None else 0)
    ext = _sub(el, "extension")
    _sub(ext, "AtticType", roof.roof_type.value)


def _walls(enc, model: HesBuilding):
    container = _sub(enc, "Walls")
    for i, wall in enumerate(model.walls, start=1):
        el = _sub(container, "Wall")
        _sysid(el, f"wall-{i}")
        wtype = _sub(el, "WallType")
        # Map construction onto an HPXML WallType child element.
        construction = wall.construction.value
        if construction in ("masonry", "structural_brick"):
            _sub(wtype, "StructuralBrick" if construction == "structural_brick" else "ConcreteMasonryUnit")
        elif construction == "steel_frame":
            _sub(wtype, "SteelFrame")
        elif construction == "log":
            _sub(wtype, "LogWall")
        else:
            _sub(wtype, "WoodStud")
        if wall.exterior_finish:
            _sub(el, "Siding", wall.exterior_finish.value.replace("_", " "))
        if wall.orientation:
            _sub(el, "Orientation", wall.orientation.value.replace("_", ""))
        ins = _sub(el, "Insulation")
        _sysid(ins, f"wall-{i}-ins")
        _sub(ins, "AssemblyEffectiveRValue",
             wall.insulation_r if wall.insulation_r is not None else 0)


def _foundations(enc, model: HesBuilding):
    foundation = model.foundation
    container = _sub(enc, "Foundations")
    el = _sub(container, "Foundation")
    _sysid(el, "foundation-1")
    ftype = _sub(el, "FoundationType")
    f = foundation.foundation_type.value
    if f == "slab_on_grade":
        _sub(ftype, "SlabOnGrade")
    elif f == "ambient":
        _sub(ftype, "Ambient")
    elif "basement" in f:
        bsmt = _sub(ftype, "Basement")
        _sub(bsmt, "Conditioned", str(f == "cond_basement").lower())
    elif "crawl" in f:
        crawl = _sub(ftype, "Crawlspace")
        _sub(crawl, "Vented", str(f == "vented_crawlspace").lower())
    ext = _sub(el, "extension")
    _sub(ext, "FoundationInsulationRValue", foundation.insulation_r)


def _windows(enc, model: HesBuilding):
    win = model.windows
    container = _sub(enc, "Windows")
    el = _sub(container, "Window")
    _sysid(el, "window-1")
    if win.area_sqft:
        _sub(el, "Area", win.area_sqft)
    if win.u_factor is not None:
        _sub(el, "UFactor", win.u_factor)
    if win.shgc is not None:
        _sub(el, "SHGC", win.shgc)
    _sub(el, "FrameType")  # frame detail lives in extension when only qualitative
    _sub(el, "GlassLayers", _GLAZING_LAYERS.get(win.glazing, "double-pane"))
    if win.low_e:
        _sub(el, "GlassType", "low-e")
    ext = _sub(el, "extension")
    if win.window_to_wall_ratio is not None:
        _sub(ext, "WindowToWallRatio", win.window_to_wall_ratio)
    if win.frame:
        _sub(ext, "FrameMaterial", win.frame.value)


# --- systems ----------------------------------------------------------------
def _systems(details, model: HesBuilding):
    systems = _sub(details, "Systems")
    hvac = _sub(systems, "HVAC")
    plant = _sub(hvac, "HVACPlant")
    _heating(plant, model)
    _cooling(plant, model)
    _distribution(hvac, model)
    _water_heating(systems, model)
    _photovoltaics(systems, model)


def _heating(plant, model: HesBuilding):
    h = model.heating
    if h.heating_type.value == "none":
        return
    if h.heating_type in (HeatingType.HEAT_PUMP, HeatingType.MINI_SPLIT, HeatingType.GCHP):
        return  # emitted as a HeatPump in _cooling to avoid double-counting
    el = _sub(plant, "HeatingSystem")
    _sysid(el, "heating-1")
    htype = _sub(el, "HeatingSystemType")
    mapping = {
        HeatingType.CENTRAL_FURNACE: "Furnace",
        HeatingType.WALL_FURNACE: "WallFurnace",
        HeatingType.BOILER: "Boiler",
        HeatingType.BASEBOARD: "ElectricResistance",
        HeatingType.WOOD_STOVE: "Stove",
    }
    _sub(htype, mapping.get(h.heating_type, "Furnace"))
    if h.fuel:
        _sub(el, "HeatingSystemFuel", _FUEL.get(h.fuel, "natural gas"))
    if h.efficiency_value is not None and h.efficiency_units:
        eff = _sub(el, "AnnualHeatingEfficiency")
        _sub(eff, "Units", _EFF_UNITS.get(h.efficiency_units, "AFUE"))
        _sub(eff, "Value", h.efficiency_value)
    if h.year_installed:
        _sub(el, "YearInstalled", h.year_installed)


def _cooling(plant, model: HesBuilding):
    c = model.cooling
    h = model.heating
    is_hp = h.heating_type in (HeatingType.HEAT_PUMP, HeatingType.MINI_SPLIT, HeatingType.GCHP) \
        or c.cooling_type in (CoolingType.HEAT_PUMP, CoolingType.MINI_SPLIT, CoolingType.GCHP)
    if is_hp:
        _heat_pump(plant, model)
        return
    if c.cooling_type.value == "none":
        return
    el = _sub(plant, "CoolingSystem")
    _sysid(el, "cooling-1")
    _sub(el, "CoolingSystemType",
         "room air conditioner" if c.cooling_type == CoolingType.ROOM_AC
         else "central air conditioning")
    if c.efficiency_value is not None and c.efficiency_units:
        eff = _sub(el, "AnnualCoolingEfficiency")
        _sub(eff, "Units", _EFF_UNITS.get(c.efficiency_units, "SEER"))
        _sub(eff, "Value", c.efficiency_value)
    if c.year_installed:
        _sub(el, "YearInstalled", c.year_installed)


def _heat_pump(plant, model: HesBuilding):
    h, c = model.heating, model.cooling
    el = _sub(plant, "HeatPump")
    _sysid(el, "heatpump-1")
    hp_kind = "mini-split" if (
        h.heating_type == HeatingType.MINI_SPLIT or c.cooling_type == CoolingType.MINI_SPLIT
    ) else ("ground-to-air" if h.heating_type == HeatingType.GCHP else "air-to-air")
    _sub(el, "HeatPumpType", hp_kind)
    if c.efficiency_value is not None and c.efficiency_units:
        eff = _sub(el, "AnnualCoolingEfficiency")
        _sub(eff, "Units", _EFF_UNITS.get(c.efficiency_units, "SEER"))
        _sub(eff, "Value", c.efficiency_value)
    if h.efficiency_value is not None and h.efficiency_units:
        eff = _sub(el, "AnnualHeatingEfficiency")
        _sub(eff, "Units", _EFF_UNITS.get(h.efficiency_units, "HSPF"))
        _sub(eff, "Value", h.efficiency_value)
    year = h.year_installed or c.year_installed
    if year:
        _sub(el, "YearInstalled", year)


def _distribution(hvac, model: HesBuilding):
    if not model.ducts:
        return
    dist = _sub(hvac, "HVACDistribution")
    _sysid(dist, "dist-1")
    air = _sub(dist, "DistributionSystemType")
    air = _sub(air, "AirDistribution")
    for i, duct in enumerate(model.ducts, start=1):
        d = _sub(air, "Ducts")
        _sub(d, "DuctType", "supply")
        _sub(d, "DuctInsulationRValue", 6 if duct.insulated else 0)
        _sub(d, "DuctLocation", duct.location.value)
        _sub(d, "FractionDuctArea", duct.fraction)
        ext = _sub(d, "extension")
        _sub(ext, "DuctSealed", str(duct.sealed).lower())


def _water_heating(systems, model: HesBuilding):
    wh = model.water_heater
    if wh.fuel is None and wh.energy_factor is None and wh.year_installed is None:
        return
    container = _sub(systems, "WaterHeating")
    el = _sub(container, "WaterHeatingSystem")
    _sysid(el, "dhw-1")
    _sub(el, "WaterHeaterType", _WH_TYPE.get(wh.wh_type, "storage water heater"))
    if wh.fuel:
        _sub(el, "FuelType", _FUEL.get(wh.fuel, "natural gas"))
    if wh.energy_factor is not None:
        _sub(el, "EnergyFactor", wh.energy_factor)
    if wh.year_installed:
        _sub(el, "YearInstalled", wh.year_installed)


def _photovoltaics(systems, model: HesBuilding):
    pv = model.pv
    if not pv.present:
        return
    container = _sub(systems, "Photovoltaics")
    el = _sub(container, "PVSystem")
    _sysid(el, "pv-1")
    if pv.capacity_kw:
        _sub(el, "MaxPowerOutput", int(pv.capacity_kw * 1000))  # watts
    if pv.azimuth is not None:
        _sub(el, "ArrayAzimuth", pv.azimuth)
    if pv.tilt is not None:
        _sub(el, "ArrayTilt", pv.tilt)
    if pv.year_installed:
        _sub(el, "YearInverterManufactured", pv.year_installed)


def _extension(details, model: HesBuilding):
    """Embed the normalized HES intermediate model for translator debugging."""
    ext = _sub(details, "extension")
    blob = _sub(ext, "HEScoreIntermediate")
    blob.text = json.dumps(model.model_dump(mode="json"), separators=(",", ":"))


def _pretty(root) -> str:
    raw = ET.tostring(root, encoding="utf-8")
    return minidom.parseString(raw).toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")
