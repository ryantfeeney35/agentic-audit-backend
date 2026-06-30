"""Tests for the HPXML / Home Energy Score generation pipeline.

Covers the lookup heuristics, the audit->HesBuilding mapper, gap analysis,
HPXML serialization, and the HEScore API client (with mocked transport).
None of these require a database — the mapper operates on duck-typed objects.
"""
import types
import datetime
from xml.etree import ElementTree as ET

import pytest

from hpxml import lookups as L
from hpxml.hes_model import (
    CoolingType, EfficiencyUnits, Fuel, HeatingType, RoofType,
)
from hpxml.mapper import build_hes_model
from hpxml.serializer import to_hpxml, HPXML_NS
from hpxml.gaps import compute_gaps, is_scoreable


def _step(step_type, label="", ai=None, meta=None):
    return types.SimpleNamespace(
        step_type=step_type, label=label, ai_summary=ai or {}, meta=meta or {}
    )


def _audit(hes_inputs=None, steps=None, sqft=1800, year_built=1985):
    prop = types.SimpleNamespace(
        street="123 Main", city="San Diego", state="CA", zip_code="92101",
        year_built=year_built, sqft=sqft,
    )
    return types.SimpleNamespace(
        id=42, date=datetime.date(2026, 6, 1), property=prop,
        hes_inputs=hes_inputs or {}, steps=steps or [],
    )


def _full_audit():
    return _audit(
        hes_inputs={
            "bedrooms": 3, "num_floors_above_grade": 2, "front_orientation": "south",
            "air_infiltration": {"qualitative": "average"},
            "foundation": {"type": "vented crawlspace", "insulation_r": 0},
            "hot_water": {"type": "storage", "fuel": "natural gas", "year_installed": 2010},
            # A furnace+AC home: heating comes from the HVAC step; cooling is
            # supplied via the supplemental override (one step has one system_type).
            "cooling": {"type": "central ac", "efficiency_value": 15, "efficiency_units": "seer"},
        },
        steps=[
            _step("exterior", "Front", meta={"house_side": "front", "orientation": "south",
                                             "siding_material": "stucco", "glass_wall_ratio": "0.15"}),
            _step("roof", "Roof", ai={"color": "dark", "finish_type": "asphalt shingle"}),
            _step("insulation", "Attic", ai={"insulation_type": "fiberglass blown", "thickness_inches": 8}),
            _step("insulation", "Walls", meta={"insulation_type": "fiberglass batt", "thickness": 3.5, "location": "wall"}),
            _step("hvac", "HVAC", ai={"system_type": "Gas Furnace", "fuel_type": "Natural Gas",
                                      "efficiency_rating": "AFUE 92%", "ducting_type": "Flex",
                                      "ducting_condition": "Leaking"}),
            _step("interior", "Living", ai={"wall_to_glass_ratio": 0.18}),
        ],
    )


# --------------------------------------------------------------------- lookups
class TestLookups:
    def test_insulation_r_from_type_and_thickness(self):
        # fiberglass blown ~2.5/in * 8in = 20
        assert L.insulation_r_value("fiberglass blown", 8) == pytest.approx(20.0)

    def test_insulation_r_from_explicit_rvalue_text(self):
        assert L.insulation_r_value("R-19 batt", None) == 19.0

    def test_insulation_none_is_zero(self):
        assert L.insulation_r_value("none", None) == 0.0

    def test_insulation_unknown_returns_none(self):
        assert L.insulation_r_value("mystery", None) is None

    def test_parse_efficiency_variants(self):
        assert L.parse_efficiency("SEER 16") == (EfficiencyUnits.SEER, 16.0)
        assert L.parse_efficiency("AFUE 92%") == (EfficiencyUnits.AFUE, 92.0)
        assert L.parse_efficiency("16 SEER2")[0] == EfficiencyUnits.SEER2

    def test_fuel_normalization(self):
        assert L.normalize_fuel("Electric") == Fuel.ELECTRIC
        assert L.normalize_fuel("Natural Gas") == Fuel.NATURAL_GAS
        assert L.normalize_fuel("Propane") == Fuel.PROPANE

    def test_heating_type(self):
        assert L.normalize_heating_type("Split Heat Pump", None) == HeatingType.HEAT_PUMP
        assert L.normalize_heating_type("Gas Furnace", None) == HeatingType.CENTRAL_FURNACE

    def test_conditioned_area_derivation(self):
        assert L.derive_conditioned_area(2000) == 1840  # 0.92 factor
        assert L.derive_conditioned_area(None) is None


# ---------------------------------------------------------------------- mapper
class TestMapper:
    def test_conditioned_area_override_beats_derivation(self):
        m = build_hes_model(_audit(hes_inputs={"conditioned_floor_area": 1500}))
        assert m.conditioned_floor_area == 1500
        assert m.conditioned_floor_area_derived is False

    def test_conditioned_area_derived_flagged(self):
        m = build_hes_model(_audit(sqft=2000))
        assert m.conditioned_floor_area == 1840
        assert m.conditioned_floor_area_derived is True

    def test_heat_pump_collapses_to_single_system(self):
        m = build_hes_model(_audit(steps=[
            _step("hvac", "HVAC", ai={"system_type": "Split Heat Pump",
                                      "fuel_type": "Electric", "efficiency_rating": "SEER 16"})
        ]))
        assert m.heating.heating_type == HeatingType.HEAT_PUMP
        assert m.cooling.cooling_type == CoolingType.HEAT_PUMP

    def test_heating_ignores_cooling_only_efficiency(self):
        # "SEER 16" describes cooling; heating efficiency must not adopt it.
        m = build_hes_model(_audit(steps=[
            _step("hvac", "HVAC", ai={"system_type": "Furnace", "fuel_type": "Natural Gas",
                                      "efficiency_rating": "SEER 16"})
        ]))
        assert m.heating.efficiency_value is None

    def test_insulation_routed_to_correct_assembly(self):
        m = build_hes_model(_full_audit())
        assert m.roof.ceiling_r == pytest.approx(20.0)  # vented attic -> ceiling
        assert any(w.insulation_r == pytest.approx(11.2) for w in m.walls)

    def test_front_orientation_inferred_from_exterior_step(self):
        m = build_hes_model(_audit(steps=[
            _step("exterior", "Front", meta={"house_side": "front", "orientation": "west"})
        ]))
        assert m.front_orientation.value == "west"

    def test_pv_inferred_from_electrical_step(self):
        m = build_hes_model(_audit(steps=[
            _step("electrical", "Panel", ai={"solar_present": "Yes", "solar_system_size": "5 kW"})
        ]))
        assert m.pv.present is True
        assert m.pv.capacity_kw == pytest.approx(5.0)


# ------------------------------------------------------------------------ gaps
class TestGaps:
    def test_empty_audit_has_required_gaps(self):
        m = build_hes_model(_audit())
        gaps = compute_gaps(m)
        required = {g.key for g in gaps if g.severity == "required"}
        assert "bedrooms" in required
        assert "num_floors_above_grade" in required
        assert "foundation.type" not in required  # defaults to slab
        assert is_scoreable(m) is False

    def test_full_audit_is_scoreable(self):
        m = build_hes_model(_full_audit())
        assert is_scoreable(m) is True

    def test_derived_area_produces_review_gap(self):
        m = build_hes_model(_full_audit())
        review = {g.key for g in compute_gaps(m) if g.severity == "review"}
        assert "conditioned_floor_area" in review


# ------------------------------------------------------------------ serializer
class TestSerializer:
    def test_serializes_wellformed_xml(self):
        m = build_hes_model(_full_audit())
        xml = to_hpxml(m)
        root = ET.fromstring(xml)  # raises on malformed XML
        assert root.tag == f"{{{HPXML_NS}}}HPXML"
        assert root.get("schemaVersion") == "3.0"

    def test_key_elements_present(self):
        m = build_hes_model(_full_audit())
        xml = to_hpxml(m)
        for tag in ("BuildingDetails", "Enclosure", "Roofs", "Walls",
                    "Foundations", "Windows", "HVAC", "WaterHeating"):
            assert f"<{tag}>" in xml or f"<{tag} " in xml, f"missing {tag}"

    def test_address_roundtrips(self):
        m = build_hes_model(_full_audit())
        root = ET.fromstring(to_hpxml(m))
        ns = {"h": HPXML_NS}
        zip_el = root.find(".//h:Address/h:ZipCode", ns)
        assert zip_el is not None and zip_el.text == "92101"


# -------------------------------------------------------------------- HESClient
class TestHESClient:
    def _client(self, responses):
        """Build a client whose HTTP session returns queued fake responses."""
        from hpxml.hes_client import HESClient

        class FakeResp:
            def __init__(self, payload, status=200):
                self._payload, self.status_code, self.text = payload, status, str(payload)

            def json(self):
                return self._payload

        class FakeSession:
            def __init__(self, queue):
                self.queue, self.calls = list(queue), []

            def post(self, url, json=None, headers=None, timeout=None):
                self.calls.append((url, json))
                return FakeResp(self.queue.pop(0))

        client = HESClient(
            base_url="https://x", api_key="k", username="u", password="p",
            session=FakeSession(responses),
        )
        return client

    def test_score_building_happy_path(self):
        client = self._client([
            {"session_token": "tok"},                 # get_session_token
            {"building_id": "999"},                    # submit_address
            {"result": "OK"},                          # submit_hpxml_inputs
            {"base_score": 7},                         # retrieve_results
            {"recommendations": []},                   # retrieve_recommendations
            {"label_url": "https://label.pdf"},        # generate_label
        ])
        out = client.score_building(hpxml="<HPXML/>", address={"street": "1 A St"})
        assert out["building_id"] == "999"
        assert out["score"] == 7
        assert out["label_url"] == "https://label.pdf"

    def test_api_error_raises(self):
        from hpxml.hes_client import HESAPIError
        client = self._client([
            {"session_token": "tok"},
            {"error": "bad address"},
        ])
        with pytest.raises(HESAPIError):
            client.submit_address({"street": "bad"})

    def test_missing_config_raises(self):
        from hpxml.hes_client import HESClient, HESConfigError
        client = HESClient(base_url="", api_key="", username="", password="")
        with pytest.raises(HESConfigError):
            client.authenticate()
