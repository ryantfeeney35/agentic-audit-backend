"""Canonical intermediate model for a Home Energy Score building.

This mirrors the minimum data set that the DOE Home Energy Score (HEScore)
needs, expressed in normalized enums/units.  The mapper populates it from
audit data; the serializer turns it into HPXML.  Keeping it separate from both
the DB models and the HPXML schema means the lookup/inference logic lives in
exactly one place (the mapper) and is unit-testable without a database.

Unit conventions:
  * areas in square feet
  * insulation as nominal assembly R-value (ft^2*F*h/Btu)
  * heating/cooling efficiency in native units (AFUE %, SEER, HSPF, COP, EER)
  * water-heater efficiency as Energy Factor (EF) / Uniform Energy Factor
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums — values chosen to map cleanly onto HEScore / HPXML enumerations.
# ---------------------------------------------------------------------------
class Orientation(str, Enum):
    NORTH = "north"
    NORTHEAST = "northeast"
    EAST = "east"
    SOUTHEAST = "southeast"
    SOUTH = "south"
    SOUTHWEST = "southwest"
    WEST = "west"
    NORTHWEST = "northwest"


class AirLeakageQuality(str, Enum):
    """Qualitative infiltration tightness (used when no blower-door test)."""
    TIGHT = "tight"
    AVERAGE = "average"
    LEAKY = "leaky"


class FoundationType(str, Enum):
    SLAB = "slab_on_grade"
    VENTED_CRAWL = "vented_crawlspace"
    UNVENTED_CRAWL = "unvented_crawlspace"
    UNCOND_BASEMENT = "uncond_basement"
    COND_BASEMENT = "cond_basement"
    AMBIENT = "ambient"  # open / pier-and-beam


class RoofType(str, Enum):
    VENTED_ATTIC = "vented_attic"
    CATHEDRAL = "cath_ceiling"


class RoofColor(str, Enum):
    WHITE = "white"
    LIGHT = "light"
    MEDIUM = "medium"
    MEDIUM_DARK = "medium_dark"
    DARK = "dark"
    COOL = "cool_color"


class WallConstruction(str, Enum):
    WOOD_STUD = "wood_stud"
    STEEL_FRAME = "steel_frame"
    DOUBLE_STUD = "double_stud"
    MASONRY = "masonry"  # CMU / concrete block
    STRUCTURAL_BRICK = "structural_brick"
    LOG = "log"


class ExteriorFinish(str, Enum):
    WOOD = "wood_siding"
    STUCCO = "stucco"
    VINYL = "vinyl_siding"
    ALUMINUM = "aluminum_siding"
    BRICK = "brick_veneer"
    FIBER_CEMENT = "fiber_cement"


class GlazingLayers(str, Enum):
    SINGLE = "single_pane"
    DOUBLE = "double_pane"
    TRIPLE = "triple_pane"


class FrameMaterial(str, Enum):
    ALUMINUM = "aluminum"
    ALUMINUM_THERMAL_BREAK = "aluminum_thermal_break"
    WOOD = "wood"
    VINYL = "vinyl"
    FIBERGLASS = "fiberglass"
    COMPOSITE = "composite"


class HeatingType(str, Enum):
    HEAT_PUMP = "heat_pump"
    MINI_SPLIT = "mini_split"
    CENTRAL_FURNACE = "central_furnace"
    WALL_FURNACE = "wall_furnace"
    BASEBOARD = "baseboard"
    BOILER = "boiler"
    GCHP = "gchp"  # ground-coupled heat pump
    WOOD_STOVE = "wood_stove"
    NONE = "none"


class CoolingType(str, Enum):
    HEAT_PUMP = "heat_pump"
    MINI_SPLIT = "mini_split"
    CENTRAL_AC = "split_dx"
    PACKAGED_AC = "packaged_dx"
    ROOM_AC = "dec"  # direct evaporative / room
    GCHP = "gchp"
    NONE = "none"


class Fuel(str, Enum):
    ELECTRIC = "electric"
    NATURAL_GAS = "natural_gas"
    FUEL_OIL = "fuel_oil"
    PROPANE = "lpg"
    WOOD_CORD = "cord_wood"
    WOOD_PELLET = "pellet_wood"


class EfficiencyUnits(str, Enum):
    AFUE = "afue"
    PERCENT = "percent"
    SEER = "seer"
    SEER2 = "seer2"
    EER = "eer"
    HSPF = "hspf"
    HSPF2 = "hspf2"
    COP = "cop"


class WaterHeaterType(str, Enum):
    STORAGE = "storage"
    TANKLESS = "tankless"
    HEAT_PUMP = "heat_pump"


class DuctLocation(str, Enum):
    CONDITIONED = "cond_space"
    UNCOND_ATTIC = "uncond_attic"
    UNCOND_BASEMENT = "uncond_basement"
    VENTED_CRAWL = "vented_crawl"
    UNVENTED_CRAWL = "unvented_crawl"
    UNDER_SLAB = "under_slab"
    EXTERIOR_WALL = "exterior_wall"
    OUTSIDE = "outside"


# ---------------------------------------------------------------------------
# Component models
# ---------------------------------------------------------------------------
class Address(BaseModel):
    street: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None


class AirInfiltration(BaseModel):
    blower_door_tested: bool = False
    cfm50: Optional[float] = None
    # Qualitative tightness used when no blower-door measurement exists.
    qualitative: Optional[AirLeakageQuality] = AirLeakageQuality.AVERAGE


class Roof(BaseModel):
    roof_type: RoofType = RoofType.VENTED_ATTIC
    color: RoofColor = RoofColor.MEDIUM
    exterior_finish: Optional[ExteriorFinish] = None
    # Insulation R at the appropriate plane:
    #   vented attic -> ceiling_r (attic floor)
    #   cathedral    -> roof_r (roof deck)
    ceiling_r: Optional[float] = None
    roof_r: Optional[float] = None
    radiant_barrier: bool = False
    area_sqft: Optional[float] = None


class Foundation(BaseModel):
    foundation_type: FoundationType = FoundationType.SLAB
    insulation_r: float = 0.0
    area_sqft: Optional[float] = None


class Wall(BaseModel):
    construction: WallConstruction = WallConstruction.WOOD_STUD
    exterior_finish: Optional[ExteriorFinish] = None
    insulation_r: Optional[float] = None
    orientation: Optional[Orientation] = None


class Windows(BaseModel):
    glazing: GlazingLayers = GlazingLayers.DOUBLE
    frame: Optional[FrameMaterial] = None
    low_e: bool = False
    u_factor: Optional[float] = None
    shgc: Optional[float] = None
    # Either an explicit area or a window-to-wall ratio (0..1).
    window_to_wall_ratio: Optional[float] = None
    area_sqft: Optional[float] = None


class HeatingSystem(BaseModel):
    heating_type: HeatingType = HeatingType.NONE
    fuel: Optional[Fuel] = None
    efficiency_units: Optional[EfficiencyUnits] = None
    efficiency_value: Optional[float] = None
    year_installed: Optional[int] = None


class CoolingSystem(BaseModel):
    cooling_type: CoolingType = CoolingType.NONE
    efficiency_units: Optional[EfficiencyUnits] = None
    efficiency_value: Optional[float] = None
    year_installed: Optional[int] = None


class Duct(BaseModel):
    location: DuctLocation = DuctLocation.CONDITIONED
    fraction: float = 1.0
    insulated: bool = False
    sealed: bool = False


class WaterHeater(BaseModel):
    wh_type: WaterHeaterType = WaterHeaterType.STORAGE
    fuel: Optional[Fuel] = None
    energy_factor: Optional[float] = None
    year_installed: Optional[int] = None


class Photovoltaics(BaseModel):
    present: bool = False
    capacity_kw: Optional[float] = None
    year_installed: Optional[int] = None
    azimuth: Optional[int] = None
    tilt: Optional[int] = None


# ---------------------------------------------------------------------------
# Root building model
# ---------------------------------------------------------------------------
class HesBuilding(BaseModel):
    # Identity / about
    audit_id: int
    address: Address = Field(default_factory=Address)
    assessment_date: Optional[str] = None  # ISO date
    assessment_type: str = "initial"  # initial|final|qa|...
    year_built: Optional[int] = None
    conditioned_floor_area: Optional[int] = None
    conditioned_floor_area_derived: bool = False  # True if estimated from gross
    num_floors_above_grade: Optional[int] = None
    bedrooms: Optional[int] = None
    front_orientation: Optional[Orientation] = None

    # Enclosure
    air_infiltration: AirInfiltration = Field(default_factory=AirInfiltration)
    roof: Roof = Field(default_factory=Roof)
    foundation: Foundation = Field(default_factory=Foundation)
    walls: List[Wall] = Field(default_factory=list)
    windows: Windows = Field(default_factory=Windows)

    # Systems
    heating: HeatingSystem = Field(default_factory=HeatingSystem)
    cooling: CoolingSystem = Field(default_factory=CoolingSystem)
    ducts: List[Duct] = Field(default_factory=list)
    water_heater: WaterHeater = Field(default_factory=WaterHeater)
    pv: Photovoltaics = Field(default_factory=Photovoltaics)
