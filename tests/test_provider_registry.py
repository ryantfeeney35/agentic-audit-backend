# backend/tests/test_provider_registry.py
"""
Tests for Provider Registry with Waterfall Fallback

Tests provider initialization, waterfall chain logic, and fallback behavior.
"""

import pytest
import os
from unittest.mock import Mock, patch, MagicMock

# Set test environment before imports
os.environ["SDGE_CMD_CLIENT_ID"] = "test_client_id"
os.environ["SDGE_CMD_CLIENT_SECRET"] = "test_client_secret"
os.environ["UTILITYAPI_API_KEY"] = "test_api_key"

from utils.providers.registry import ProviderRegistry
from utils.providers import AuthResult, ProviderType


@pytest.fixture
def registry():
    """Create a provider registry for testing."""
    return ProviderRegistry()


class TestProviderRegistryInitialization:
    """Tests for provider registry initialization."""
    
    def test_registry_initializes_providers(self, registry):
        """Should initialize available providers."""
        # Check that providers are initialized
        assert "sdge_cmd" in registry._providers
        assert "utilityapi" in registry._providers
        assert "manual" in registry._providers
    
    def test_get_provider_by_name(self, registry):
        """Should return provider by name."""
        sdge = registry.get_provider("sdge_cmd")
        assert sdge is not None
        assert sdge.provider_name == "sdge_cmd"
        
        utilityapi = registry.get_provider("utilityapi")
        assert utilityapi is not None
        assert utilityapi.provider_name == "utilityapi"
    
    def test_get_unknown_provider(self, registry):
        """Should return None for unknown provider."""
        provider = registry.get_provider("nonexistent")
        assert provider is None


class TestProviderRegistryWaterfallChains:
    """Tests for waterfall chain configuration."""
    
    def test_sdge_chain_starts_with_cmd(self, registry):
        """SDGE chain should start with direct CMD."""
        chain = registry.get_fallback_chain("SDGE")
        provider_names = [p.provider_name for p in chain]
        
        assert provider_names[0] == "sdge_cmd"
        assert "utilityapi" in provider_names
        assert "manual" in provider_names
    
    def test_pge_chain_starts_with_utilityapi(self, registry):
        """PGE chain should start with UtilityAPI (no CMD)."""
        chain = registry.get_fallback_chain("PGE")
        provider_names = [p.provider_name for p in chain]
        
        assert "sdge_cmd" not in provider_names
        assert provider_names[0] == "utilityapi"
        assert "manual" in provider_names
    
    def test_unknown_utility_uses_default_chain(self, registry):
        """Unknown utilities should use default chain."""
        chain = registry.get_fallback_chain("UNKNOWN_UTILITY")
        provider_names = [p.provider_name for p in chain]
        
        # Default chain is [utilityapi, manual]
        assert provider_names[0] == "utilityapi"
        assert provider_names[-1] == "manual"
    
    def test_available_providers_for_utility(self, registry):
        """Should return available provider names for a utility."""
        available = registry.get_available_providers("SDGE")
        
        assert isinstance(available, list)
        assert len(available) > 0
        # All returned providers should have required info fields
        for provider_info in available:
            assert "name" in provider_info
            assert "type" in provider_info
            assert "available" in provider_info


class TestProviderRegistryConnectWithFallback:
    """Tests for waterfall fallback connection logic."""
    
    def test_connect_first_provider_succeeds(self, registry):
        """Should return first provider result if it succeeds."""
        mock_result = AuthResult(
            success=True,
            auth_url="https://test.com/auth",
            connection_id=123,
            provider_name="sdge_cmd"
        )
        
        with patch.object(registry._providers["sdge_cmd"], "connect", return_value=mock_result), \
             patch.object(registry._providers["sdge_cmd"], "is_available", return_value=True):
            
            result = registry.connect(
                audit_id=1,
                user_id="user-123",
                utility_name="SDGE",
                data_scope="electric"
            )
            
            assert result.success is True
            assert result.provider_name == "sdge_cmd"
    
    def test_connect_falls_back_on_failure(self, registry):
        """Should try next provider when first fails."""
        failed_result = AuthResult(
            success=False,
            error="CMD not configured",
            provider_name="sdge_cmd"
        )
        success_result = AuthResult(
            success=True,
            auth_url="https://utilityapi.com/auth",
            connection_id=456,
            provider_name="utilityapi"
        )
        
        with patch.object(registry._providers["sdge_cmd"], "connect", return_value=failed_result), \
             patch.object(registry._providers["sdge_cmd"], "is_available", return_value=True), \
             patch.object(registry._providers["utilityapi"], "connect", return_value=success_result), \
             patch.object(registry._providers["utilityapi"], "is_available", return_value=True):
            
            result = registry.connect(
                audit_id=1,
                user_id="user-123",
                utility_name="SDGE",
                data_scope="electric"
            )
            
            assert result.success is True
            assert result.provider_name == "utilityapi"
    
    def test_connect_skips_unavailable_providers(self, registry):
        """Should skip providers that are not available."""
        success_result = AuthResult(
            success=True,
            auth_url="https://utilityapi.com/auth",
            connection_id=789,
            provider_name="utilityapi"
        )
        
        with patch.object(registry._providers["sdge_cmd"], "is_available", return_value=False), \
             patch.object(registry._providers["utilityapi"], "connect", return_value=success_result), \
             patch.object(registry._providers["utilityapi"], "is_available", return_value=True):
            
            result = registry.connect(
                audit_id=1,
                user_id="user-123",
                utility_name="SDGE"
            )
            
            assert result.success is True
            assert result.provider_name == "utilityapi"
    
    def test_connect_all_providers_fail(self, registry):
        """Should return error when all providers fail."""
        fail_result = AuthResult(success=False, error="Provider failed")
        
        with patch.object(registry._providers["sdge_cmd"], "connect", return_value=fail_result), \
             patch.object(registry._providers["sdge_cmd"], "is_available", return_value=True), \
             patch.object(registry._providers["utilityapi"], "connect", return_value=fail_result), \
             patch.object(registry._providers["utilityapi"], "is_available", return_value=True), \
             patch.object(registry._providers["manual"], "connect", return_value=fail_result), \
             patch.object(registry._providers["manual"], "is_available", return_value=True):
            
            result = registry.connect(
                audit_id=1,
                user_id="user-123",
                utility_name="SDGE"
            )
            
            assert result.success is False
            assert "failed" in result.error.lower()


class TestProviderRegistryInfo:
    """Tests for provider information methods."""
    
    def test_get_available_providers_info(self, registry):
        """Should return info for all available providers."""
        info = registry.get_available_providers()
        
        assert isinstance(info, list)
        # Manual and at least one other provider should be available
        assert len(info) >= 2
        
        names = [p["name"] for p in info]
        # Manual should always be available
        assert "manual" in names
    
    def test_get_supported_utilities(self, registry):
        """Should return list of supported utilities."""
        utilities = registry.get_supported_utilities()
        
        assert isinstance(utilities, list)
        assert len(utilities) > 0
        
        # Should include major California utilities
        utility_names = [u["name"] for u in utilities]
        assert "SDGE" in utility_names
    
    def test_get_waterfall_chains(self, registry):
        """Should return waterfall chain configuration."""
        chains = registry.get_waterfall_chains()
        
        assert isinstance(chains, dict)
        assert "SDGE" in chains
        assert chains["SDGE"][0] == "sdge_cmd"


class TestManualEntryProvider:
    """Tests for manual entry provider."""
    
    def test_manual_provider_always_available(self, registry):
        """Manual provider should always be available."""
        manual = registry.get_provider("manual")
        assert manual is not None
        assert manual.is_available() is True
    
    def test_manual_provider_no_redirect(self, registry):
        """Manual provider should not require redirect."""
        manual = registry.get_provider("manual")
        info = manual.get_info()
        
        assert info["type"] == "manual"
    
    def test_manual_provider_supports_all_utilities(self, registry):
        """Manual provider should support all utilities."""
        manual = registry.get_provider("manual")
        
        assert manual.supports_utility("SDGE") is True
        assert manual.supports_utility("PGE") is True
        assert manual.supports_utility("RANDOM") is True
