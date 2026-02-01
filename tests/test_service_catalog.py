"""Unit tests for service catalog loading and matching."""

import json
import os
import tempfile
from pathlib import Path

import pytest

# Set a dummy OPENAI_API_KEY to prevent initialization errors when agents module is loaded
# This key is never used - tests don't make LLM calls
os.environ.setdefault("OPENAI_API_KEY", "sk-test-dummy-key-for-testing-only")

# Ensure the backend package is importable
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import directly from the services submodule to avoid loading the full agents module
# which initializes the LLM at import time
from agents.services.catalog import (
    ServiceEntry,
    ServiceCatalog,
    load_catalog,
    get_catalog,
    reload_catalog,
)
from agents.services.matcher import (
    match_recommendation_to_service,
    find_best_service_match,
    normalize_text,
    extract_key_phrases,
    MatchResult,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_catalog_data():
    """Minimal catalog data for testing."""
    return {
        "version": "1.0",
        "last_updated": "2026-01-22",
        "services": [
            {
                "id": "insulation-attic-install",
                "category": "Home Efficiency",
                "sub_category": "Insulation",
                "sub_category_detail": "Attic",
                "action": "Install",
                "order_of_completion": 4,
                "estimated_cost_usd": None,
                "estimated_kwh_savings": None,
                "rebate_eligible": True,
                "keywords": ["attic insulation", "install attic", "add insulation to attic"],
            },
            {
                "id": "hvac-unit-repair",
                "category": "HVAC",
                "sub_category": "Unit",
                "sub_category_detail": None,
                "action": "Repair",
                "order_of_completion": 6,
                "estimated_cost_usd": 500.0,
                "estimated_kwh_savings": 200.0,
                "rebate_eligible": False,
                "keywords": ["hvac repair", "furnace repair", "ac repair", "fix hvac"],
            },
            {
                "id": "hvac-ducting-seal",
                "category": "HVAC",
                "sub_category": "Ducting",
                "sub_category_detail": None,
                "action": "Repair",
                "order_of_completion": 5,
                "estimated_cost_usd": 300.0,
                "estimated_kwh_savings": 150.0,
                "rebate_eligible": False,
                "keywords": ["duct sealing", "seal ducts", "duct repair", "duct leak"],
            },
            {
                "id": "lighting-led-replacement",
                "category": "Electrical",
                "sub_category": "Lighting",
                "sub_category_detail": None,
                "action": "Replacement",
                "order_of_completion": 1,
                "estimated_cost_usd": 100.0,
                "estimated_kwh_savings": 500.0,
                "rebate_eligible": True,
                "keywords": ["led replacement", "replace lighting", "led bulbs", "led upgrade"],
            },
        ],
    }


@pytest.fixture
def temp_catalog_file(sample_catalog_data):
    """Create a temporary catalog file for testing."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(sample_catalog_data, f)
        temp_path = f.name
    
    yield temp_path
    
    # Cleanup
    os.unlink(temp_path)


@pytest.fixture
def loaded_catalog(temp_catalog_file):
    """Load catalog from temp file for testing."""
    # Clear any cached catalog
    get_catalog.cache_clear()
    
    # Set env var to use temp file
    old_path = os.environ.get("SERVICE_CATALOG_PATH")
    os.environ["SERVICE_CATALOG_PATH"] = temp_catalog_file
    
    catalog = load_catalog()
    
    yield catalog
    
    # Restore env var
    if old_path:
        os.environ["SERVICE_CATALOG_PATH"] = old_path
    else:
        os.environ.pop("SERVICE_CATALOG_PATH", None)
    
    get_catalog.cache_clear()


# ─────────────────────────────────────────────────────────────────────────────
# Catalog Loading Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestCatalogLoading:
    """Tests for catalog loading and validation."""

    def test_load_catalog_success(self, temp_catalog_file):
        """Test successful catalog loading from file."""
        catalog = load_catalog(temp_catalog_file)
        assert isinstance(catalog, ServiceCatalog)
        assert len(catalog.services) == 4
        assert catalog.version == "1.0"

    def test_load_catalog_missing_file(self):
        """Test that loading a missing file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_catalog("/nonexistent/path/catalog.json")

    def test_service_entry_properties(self, loaded_catalog):
        """Test ServiceEntry computed properties."""
        service = loaded_catalog.get_service_by_id("insulation-attic-install")
        assert service is not None
        assert service.display_name == "Insulation - Attic - Install"
        assert service.full_path == "Home Efficiency > Insulation > Attic > Install"

    def test_get_service_by_id(self, loaded_catalog):
        """Test looking up service by ID."""
        service = loaded_catalog.get_service_by_id("hvac-unit-repair")
        assert service is not None
        assert service.action == "Repair"
        assert service.category == "HVAC"
        
        # Non-existent ID
        assert loaded_catalog.get_service_by_id("nonexistent") is None

    def test_get_services_by_category(self, loaded_catalog):
        """Test filtering services by category."""
        hvac_services = loaded_catalog.get_services_by_category("HVAC")
        assert len(hvac_services) == 2
        assert all(s.category == "HVAC" for s in hvac_services)
        
        # Case-insensitive
        hvac_services_lower = loaded_catalog.get_services_by_category("hvac")
        assert len(hvac_services_lower) == 2

    def test_get_services_by_domain(self, loaded_catalog):
        """Test filtering services by agent domain."""
        insulation_services = loaded_catalog.get_services_by_domain("insulation")
        assert len(insulation_services) >= 1
        
        hvac_services = loaded_catalog.get_services_by_domain("hvac")
        assert len(hvac_services) == 2

    def test_to_taxonomy_text(self, loaded_catalog):
        """Test generating taxonomy text for LLM prompts."""
        taxonomy = loaded_catalog.to_taxonomy_text()
        assert "**HVAC**" in taxonomy or "**Home Efficiency**" in taxonomy
        assert "Insulation" in taxonomy
        assert "Install" in taxonomy or "Repair" in taxonomy


# ─────────────────────────────────────────────────────────────────────────────
# Matching Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestRecommendationMatching:
    """Tests for recommendation-to-service matching."""

    def test_normalize_text(self):
        """Test text normalization."""
        assert normalize_text("Install  ATTIC insulation!") == "install attic insulation"
        assert normalize_text("  LED bulbs... ") == "led bulbs"

    def test_extract_key_phrases(self):
        """Test key phrase extraction."""
        text = "Install blown-in attic insulation to improve R-value"
        phrases = extract_key_phrases(text)
        assert any("install" in p for p in phrases)
        assert any("insulation" in p for p in phrases)

    def test_match_exact_keyword(self, loaded_catalog, temp_catalog_file):
        """Test exact keyword matching."""
        # Set up environment for matching
        os.environ["SERVICE_CATALOG_PATH"] = temp_catalog_file
        get_catalog.cache_clear()
        
        result = match_recommendation_to_service("Seal ducts to reduce air leakage")
        assert result.service is not None
        assert result.service.id == "hvac-ducting-seal"
        assert result.match_type == "keyword"
        assert result.confidence >= 0.8
        
        get_catalog.cache_clear()

    def test_match_keyword_with_domain_filter(self, loaded_catalog, temp_catalog_file):
        """Test keyword matching filtered by domain."""
        os.environ["SERVICE_CATALOG_PATH"] = temp_catalog_file
        get_catalog.cache_clear()
        
        # Match should work with correct domain
        result = match_recommendation_to_service(
            "Add insulation to attic",
            domain="insulation"
        )
        assert result.service is not None
        assert "insulation" in result.service.id.lower()
        
        get_catalog.cache_clear()

    def test_match_no_match(self, loaded_catalog, temp_catalog_file):
        """Test that unrecognized recommendations return no match."""
        os.environ["SERVICE_CATALOG_PATH"] = temp_catalog_file
        get_catalog.cache_clear()
        
        result = match_recommendation_to_service("Install a swimming pool")
        assert result.service is None
        assert result.match_type == "none"
        assert result.confidence == 0.0
        
        get_catalog.cache_clear()

    def test_match_empty_text(self, loaded_catalog, temp_catalog_file):
        """Test matching with empty or whitespace text."""
        os.environ["SERVICE_CATALOG_PATH"] = temp_catalog_file
        get_catalog.cache_clear()
        
        result = match_recommendation_to_service("")
        assert result.service is None
        assert result.match_type == "none"
        
        result2 = match_recommendation_to_service("   ")
        assert result2.service is None
        
        get_catalog.cache_clear()

    def test_find_best_service_match_convenience(self, loaded_catalog, temp_catalog_file):
        """Test the convenience function for extracting match details."""
        os.environ["SERVICE_CATALOG_PATH"] = temp_catalog_file
        get_catalog.cache_clear()
        
        service_id, order, rebate, confidence = find_best_service_match(
            "Replace all lighting with LED bulbs",
            step_type="interior"
        )
        
        assert service_id == "lighting-led-replacement"
        assert order == 1
        assert rebate is True
        assert confidence > 0
        
        get_catalog.cache_clear()

    def test_find_best_service_match_no_match(self, loaded_catalog, temp_catalog_file):
        """Test convenience function returns None tuple when no match."""
        os.environ["SERVICE_CATALOG_PATH"] = temp_catalog_file
        get_catalog.cache_clear()
        
        service_id, order, rebate, confidence = find_best_service_match(
            "Build a new garage"
        )
        
        assert service_id is None
        assert order is None
        assert rebate is None
        assert confidence == 0.0
        
        get_catalog.cache_clear()


# ─────────────────────────────────────────────────────────────────────────────
# Integration Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestCatalogIntegration:
    """Integration tests using the real catalog file."""

    def test_load_real_catalog(self):
        """Test loading the actual service_catalog.json file."""
        # Clear cache to load fresh
        get_catalog.cache_clear()
        
        # Reset env var to use default path
        old_path = os.environ.pop("SERVICE_CATALOG_PATH", None)
        
        try:
            catalog = get_catalog()
            assert len(catalog.services) > 0
            
            # Verify expected services exist
            assert catalog.get_service_by_id("home-efficiency-insulation-attic-install") is not None
            assert catalog.get_service_by_id("hvac-unit-repair") is not None
            assert catalog.get_service_by_id("electrical-solar-installation") is not None
        finally:
            if old_path:
                os.environ["SERVICE_CATALOG_PATH"] = old_path
            get_catalog.cache_clear()

    def test_match_real_recommendations(self):
        """Test matching realistic recommendation text against real catalog."""
        get_catalog.cache_clear()
        old_path = os.environ.pop("SERVICE_CATALOG_PATH", None)
        
        try:
            # These should match real catalog entries
            test_cases = [
                ("Install additional blown-in attic insulation to achieve R-38", "home-efficiency-insulation-attic"),
                ("Repair or replace deteriorated HVAC ductwork", "hvac-ducting"),
                ("Replace incandescent bulbs with LED lighting", "electrical-lighting"),
                ("Consider installing a heat pump water heater", "electrical-appliances-water-heater"),
            ]
            
            for text, expected_id_prefix in test_cases:
                result = match_recommendation_to_service(text)
                assert result.service is not None, f"No match for: {text}"
                assert result.service.id.startswith(expected_id_prefix), \
                    f"Expected {expected_id_prefix}*, got {result.service.id} for: {text}"
        finally:
            if old_path:
                os.environ["SERVICE_CATALOG_PATH"] = old_path
            get_catalog.cache_clear()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
