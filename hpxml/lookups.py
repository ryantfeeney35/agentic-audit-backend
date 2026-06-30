"""Conversion tables + heuristics: free-text/qualitative audit data -> HES values.

These are deliberately conservative, well-commented defaults.  Every function
returns ``None`` when it cannot make a confident determination, so the gap
analysis (gaps.py) can flag the field for auditor confirmation rather than
silently guessing.  Centralizing them here keeps the mapper readable and makes
the assumptions reviewable/testable in one place.
"""
from __future__ import annotations

import re
from typing import Optional

from .hes_model import (
    CoolingType,
    EfficiencyUnits,
    ExteriorFinish,
    FoundationType,
    Fuel,
    GlazingLayers,
    HeatingType,
    Orientation,
    RoofColor,
    WallConstruction,
    WaterHeaterType,
)


def _norm(text: Optional[str]) -> str:
    return (text or "").strip().lower()


# ---------------------------------------------------------------------------
# Insulation: type + thickness -> nominal assembly R-value.
# R-per-inch values are industry mid-points; intentionally conservative.
# ---------------------------------------------------------------------------
R_PER_INCH = {
    "fiberglass_batt": 3.2,
    "fiberglass_blown": 2.5,
    "fiberglass": 3.0,
    "cellulose": 3.5,
    "mineral_wool": 3.3,
    "rock_wool": 3.3,
    "spray_foam_open": 3.6,
    "spray_foam_closed": 6.5,
    "spray_foam": 5.0,
    "rigid_foam": 5.0,
    "polyiso": 6.0,
    "xps": 5.0,
    "eps": 3.8,
    "vermiculite": 2.4,
}

_INSULATION_SYNONYMS = [
    (("closed", "closed cell", "closed-cell"), "spray_foam_closed"),
    (("open", "open cell", "open-cell"), "spray_foam_open"),
    (("spray", "foam"), "spray_foam"),
    (("cellulose",), "cellulose"),
    (("mineral", "rock wool", "rockwool"), "mineral_wool"),
    (("blown", "loose"), "fiberglass_blown"),
    (("batt", "fiberglass", "glass"), "fiberglass_batt"),
    (("polyiso", "iso"), "polyiso"),
    (("xps",), "xps"),
    (("eps",), "eps"),
    (("rigid",), "rigid_foam"),
    (("vermiculite",), "vermiculite"),
]


def insulation_key(insulation_type: Optional[str]) -> Optional[str]:
    t = _norm(insulation_type)
    if not t:
        return None
    for needles, key in _INSULATION_SYNONYMS:
        if any(n in t for n in needles):
            return key
    return None


def parse_thickness_inches(value) -> Optional[float]:
    """Accept a number or a string like '6 in', 'R-19', '3.5"'."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value)
    m = re.search(r"(\d+(\.\d+)?)", s)
    return float(m.group(1)) if m else None


def insulation_r_value(insulation_type: Optional[str], thickness_inches) -> Optional[float]:
    """Estimate nominal R-value from material + thickness.

    If the text already encodes an R-value (e.g. 'R-19'), trust that instead.
    """
    t = _norm(insulation_type)
    m = re.search(r"r-?\s*(\d+(\.\d+)?)", t)
    if m:
        return float(m.group(1))
    key = insulation_key(insulation_type)
    inches = parse_thickness_inches(thickness_inches)
    if key and inches:
        return round(R_PER_INCH[key] * inches, 1)
    # No insulation observed -> R-0 is a meaningful, confident answer.
    if t in {"none", "no insulation", "uninsulated", "bare"}:
        return 0.0
    return None


# ---------------------------------------------------------------------------
# Fuel
# ---------------------------------------------------------------------------
def normalize_fuel(fuel: Optional[str]) -> Optional[Fuel]:
    t = _norm(fuel)
    if not t:
        return None
    if "elect" in t:
        return Fuel.ELECTRIC
    if "gas" in t and "propane" not in t:
        return Fuel.NATURAL_GAS
    if "propane" in t or "lpg" in t:
        return Fuel.PROPANE
    if "oil" in t:
        return Fuel.FUEL_OIL
    if "pellet" in t:
        return Fuel.WOOD_PELLET
    if "wood" in t or "cord" in t:
        return Fuel.WOOD_CORD
    return None


# ---------------------------------------------------------------------------
# HVAC system type
# ---------------------------------------------------------------------------
def normalize_heating_type(system_type: Optional[str], fuel: Optional[Fuel]) -> Optional[HeatingType]:
    t = _norm(system_type)
    if not t:
        return None
    if "mini" in t and "split" in t:
        return HeatingType.MINI_SPLIT
    if "ground" in t or "geo" in t:
        return HeatingType.GCHP
    if "heat pump" in t or "heatpump" in t:
        return HeatingType.HEAT_PUMP
    if "boiler" in t or "hydronic" in t:
        return HeatingType.BOILER
    if "baseboard" in t or "resistance" in t:
        return HeatingType.BASEBOARD
    if "wall" in t and "furnace" in t:
        return HeatingType.WALL_FURNACE
    if "furnace" in t or "forced air" in t:
        return HeatingType.CENTRAL_FURNACE
    if "wood" in t or "stove" in t:
        return HeatingType.WOOD_STOVE
    return None


def normalize_cooling_type(system_type: Optional[str]) -> Optional[CoolingType]:
    t = _norm(system_type)
    if not t:
        return None
    if "mini" in t and "split" in t:
        return CoolingType.MINI_SPLIT
    if "ground" in t or "geo" in t:
        return CoolingType.GCHP
    if "heat pump" in t or "heatpump" in t:
        return CoolingType.HEAT_PUMP
    if "package" in t:
        return CoolingType.PACKAGED_AC
    if "room" in t or "window unit" in t or "evaporative" in t or "swamp" in t:
        return CoolingType.ROOM_AC
    if "central" in t or "split" in t or "ac" in t or "a/c" in t or "air condition" in t:
        return CoolingType.CENTRAL_AC
    return None


# ---------------------------------------------------------------------------
# Efficiency rating free-text -> (units, value)
# Handles labels like 'SEER 16', 'AFUE 92%', 'HSPF 9', '16 SEER2'.
# ---------------------------------------------------------------------------
_EFF_PATTERNS = [
    (EfficiencyUnits.SEER2, r"seer2"),
    (EfficiencyUnits.SEER, r"seer"),
    (EfficiencyUnits.HSPF2, r"hspf2"),
    (EfficiencyUnits.HSPF, r"hspf"),
    (EfficiencyUnits.EER, r"eer"),
    (EfficiencyUnits.AFUE, r"afue"),
    (EfficiencyUnits.COP, r"cop"),
]


def parse_efficiency(rating: Optional[str]):
    """Return (EfficiencyUnits, float) or (None, None)."""
    t = _norm(rating)
    if not t:
        return None, None
    num_match = re.search(r"(\d+(\.\d+)?)", t)
    value = float(num_match.group(1)) if num_match else None
    for units, pat in _EFF_PATTERNS:
        if re.search(pat, t):
            return units, value
    # A bare percentage on a heating system is most likely AFUE.
    if "%" in t and value is not None:
        return EfficiencyUnits.AFUE, value
    return None, value


# ---------------------------------------------------------------------------
# Roof color -> HES solar-absorptance bucket
# ---------------------------------------------------------------------------
def normalize_roof_color(color: Optional[str]) -> Optional[RoofColor]:
    t = _norm(color)
    if not t:
        return None
    if "cool" in t or "reflect" in t:
        return RoofColor.COOL
    if "white" in t:
        return RoofColor.WHITE
    if any(c in t for c in ("light", "tan", "beige", "cream")):
        return RoofColor.LIGHT
    if any(c in t for c in ("black", "dark", "charcoal")):
        return RoofColor.DARK
    if any(c in t for c in ("brown", "gray", "grey", "red", "green", "blue")):
        return RoofColor.MEDIUM
    return None


# ---------------------------------------------------------------------------
# Exterior finish / siding
# ---------------------------------------------------------------------------
def normalize_exterior_finish(material: Optional[str]) -> Optional[ExteriorFinish]:
    t = _norm(material)
    if not t:
        return None
    if "stucco" in t:
        return ExteriorFinish.STUCCO
    if "vinyl" in t:
        return ExteriorFinish.VINYL
    if "alum" in t:
        return ExteriorFinish.ALUMINUM
    if "brick" in t:
        return ExteriorFinish.BRICK
    if "fiber" in t or "hardie" in t or "cement" in t:
        return ExteriorFinish.FIBER_CEMENT
    if "wood" in t or "cedar" in t or "lap" in t:
        return ExteriorFinish.WOOD
    return None


def normalize_wall_construction(material: Optional[str]) -> Optional[WallConstruction]:
    t = _norm(material)
    if not t:
        return None
    if "brick" in t:
        return WallConstruction.STRUCTURAL_BRICK
    if "block" in t or "cmu" in t or "concrete" in t or "masonry" in t:
        return WallConstruction.MASONRY
    if "steel" in t or "metal" in t:
        return WallConstruction.STEEL_FRAME
    if "log" in t:
        return WallConstruction.LOG
    # Stucco/vinyl/wood siding are finishes over a default wood-stud cavity.
    if any(k in t for k in ("wood", "vinyl", "stucco", "fiber", "lap", "frame")):
        return WallConstruction.WOOD_STUD
    return None


# ---------------------------------------------------------------------------
# Orientation
# ---------------------------------------------------------------------------
_ORIENTATIONS = {
    "n": Orientation.NORTH, "north": Orientation.NORTH,
    "ne": Orientation.NORTHEAST, "northeast": Orientation.NORTHEAST,
    "e": Orientation.EAST, "east": Orientation.EAST,
    "se": Orientation.SOUTHEAST, "southeast": Orientation.SOUTHEAST,
    "s": Orientation.SOUTH, "south": Orientation.SOUTH,
    "sw": Orientation.SOUTHWEST, "southwest": Orientation.SOUTHWEST,
    "w": Orientation.WEST, "west": Orientation.WEST,
    "nw": Orientation.NORTHWEST, "northwest": Orientation.NORTHWEST,
}


def normalize_orientation(value: Optional[str]) -> Optional[Orientation]:
    t = _norm(value).replace("-", "").replace(" ", "")
    return _ORIENTATIONS.get(t)


# ---------------------------------------------------------------------------
# Windows: glazing layers + frame from free text
# ---------------------------------------------------------------------------
def normalize_glazing(value: Optional[str]) -> Optional[GlazingLayers]:
    t = _norm(value)
    if not t:
        return None
    if "triple" in t or "3" in t:
        return GlazingLayers.TRIPLE
    if "double" in t or "dual" in t or "2" in t:
        return GlazingLayers.DOUBLE
    if "single" in t or "1" in t:
        return GlazingLayers.SINGLE
    return None


# ---------------------------------------------------------------------------
# Water heater
# ---------------------------------------------------------------------------
def normalize_wh_type(value: Optional[str]) -> Optional[WaterHeaterType]:
    t = _norm(value)
    if not t:
        return None
    if "heat pump" in t or "hpwh" in t or "hybrid" in t:
        return WaterHeaterType.HEAT_PUMP
    if "tankless" in t or "on demand" in t or "instant" in t:
        return WaterHeaterType.TANKLESS
    if "tank" in t or "storage" in t:
        return WaterHeaterType.STORAGE
    return None


# ---------------------------------------------------------------------------
# Foundation
# ---------------------------------------------------------------------------
def normalize_foundation(value: Optional[str]) -> Optional[FoundationType]:
    t = _norm(value)
    if not t:
        return None
    if "slab" in t:
        return FoundationType.SLAB
    if "pier" in t or "ambient" in t or "open" in t:
        return FoundationType.AMBIENT
    if "crawl" in t:
        if "unvent" in t or "sealed" in t or "closed" in t:
            return FoundationType.UNVENTED_CRAWL
        return FoundationType.VENTED_CRAWL
    if "basement" in t:
        if "cond" in t or "finish" in t or "heated" in t:
            return FoundationType.COND_BASEMENT
        return FoundationType.UNCOND_BASEMENT
    return None


# ---------------------------------------------------------------------------
# Conditioned floor area from gross.
# ---------------------------------------------------------------------------
# Property.sqft is gross; HEScore needs *conditioned* area.  Without a
# room-by-room takeoff we discount gross by a small factor to remove typical
# unconditioned area (attached garage, unfinished basement/porch).
DEFAULT_CONDITIONED_FRACTION = 0.92


def derive_conditioned_area(
    gross_sqft: Optional[int],
    fraction: float = DEFAULT_CONDITIONED_FRACTION,
) -> Optional[int]:
    if not gross_sqft:
        return None
    return int(round(gross_sqft * fraction))
