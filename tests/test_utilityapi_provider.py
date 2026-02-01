# backend/tests/test_utilityapi_provider.py
"""
Tests for UtilityAPI Provider

Tests authorization form creation, webhook handling, and data sync
with mocked UtilityAPI responses.
"""

import pytest
import os
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock

# Set test environment before imports
os.environ["UTILITYAPI_API_KEY"] = "test_api_key"
os.environ["UTILITYAPI_WEBHOOK_URL"] = "http://localhost:8080/api/utility/webhook"

from utils.providers.utilityapi import UtilityAPIProvider
from utils.providers import AuthResult, SyncResult, ConnectionStatus


@pytest.fixture
def provider():
    """Create UtilityAPI provider with test configuration."""
    return UtilityAPIProvider()


@pytest.fixture
def mock_db():
    """Create mock db object."""
    db = MagicMock()
    db.session = MagicMock()
    return db


@pytest.fixture
def mock_connection():
    """Create mock UtilityConnection."""
    conn = MagicMock()
    conn.id = 123
    conn.user_id = "user-123"
    conn.audit_id = 1
    conn.status = "connected"
    conn.data_scope = "electric"
    conn.provider_metadata = {
        "authorization_uid": "auth-uid-456",
        "form_uid": "form-uid-789",
        "reference_id": "audit_1_abc123",
    }
    return conn


class TestUtilityAPIProviderAvailability:
    """Tests for provider availability checks."""
    
    def test_is_available_with_api_key(self, provider):
        """Should be available when API key is set."""
        assert provider.is_available() is True
    
    def test_is_not_available_without_api_key(self):
        """Should not be available without API key."""
        with patch.dict(os.environ, {"UTILITYAPI_API_KEY": ""}):
            provider = UtilityAPIProvider()
            assert provider.is_available() is False
    
    def test_provider_info(self, provider):
        """Should return correct provider info."""
        info = provider.get_info()
        
        assert info["name"] == "utilityapi"
        assert info["type"] == "aggregator"
        assert "*" in info["utilities"]
    
    def test_supports_all_utilities(self, provider):
        """Should support all utilities."""
        assert provider.supports_utility("SDGE") is True
        assert provider.supports_utility("PGE") is True
        assert provider.supports_utility("RANDOM_UTILITY") is True


class TestUtilityAPIProviderConnect:
    """Tests for authorization form creation."""
    
    def test_connect_creates_form(self, provider, mock_db):
        """Should create authorization form and return URL."""
        mock_conn = MagicMock()
        mock_conn.id = 123
        MockConnection = MagicMock(return_value=mock_conn)
        
        with patch("utils.providers.utilityapi._get_db", return_value=mock_db), \
             patch("utils.providers.utilityapi._get_models", return_value=(MockConnection, MagicMock(), MagicMock())), \
             patch("utils.providers.utilityapi.requests.post") as mock_post:
            
            mock_post.return_value = MagicMock(
                status_code=201,
                json=lambda: {
                    "url": "https://utilityapi.com/authorize/form-123",
                    "uid": "form-123",
                }
            )
            
            result = provider.connect(
                audit_id=1,
                user_id="user-123",
                utility_name="PGE",
                data_scope="electric"
            )
            
            assert result.success is True
            assert result.auth_url is not None
            assert "utilityapi.com" in result.auth_url
            assert result.connection_id == 123
            assert result.provider_name == "utilityapi"
    
    def test_connect_handles_api_error(self, provider, mock_db):
        """Should handle API errors gracefully."""
        MockConnection = MagicMock()
        
        with patch("utils.providers.utilityapi._get_db", return_value=mock_db), \
             patch("utils.providers.utilityapi._get_models", return_value=(MockConnection, MagicMock(), MagicMock())), \
             patch("utils.providers.utilityapi.requests.post") as mock_post:
            
            mock_post.return_value = MagicMock(
                status_code=500,
                text="Internal Server Error"
            )
            
            result = provider.connect(
                audit_id=1,
                user_id="user-123",
                utility_name="PGE"
            )
            
            assert result.success is False
            assert "API error" in result.error
    
    def test_connect_fails_without_api_key(self, mock_db):
        """Should fail gracefully without API key."""
        with patch.dict(os.environ, {"UTILITYAPI_API_KEY": ""}):
            provider = UtilityAPIProvider()
            
            result = provider.connect(
                audit_id=1,
                user_id="user-123",
                utility_name="PGE"
            )
            
            assert result.success is False
            assert "not configured" in result.error


class TestUtilityAPIProviderCallback:
    """Tests for webhook/callback handling."""
    
    def test_handle_callback_with_state(self, provider, mock_connection, mock_db):
        """Should find connection by state and update status."""
        MockConnection = MagicMock()
        MockConnection.query.filter_by.return_value.first.return_value = mock_connection
        
        with patch("utils.providers.utilityapi._get_db", return_value=mock_db), \
             patch("utils.providers.utilityapi._get_models", return_value=(MockConnection, MagicMock(), MagicMock())):
            
            result = provider.handle_callback(
                state="audit_1_abc123",
                authorization_uid="auth-uid-456",
                meters=[{"meter_id": "m1"}]
            )
            
            assert result.success is True
            assert result.connection_id == 123
    
    def test_handle_callback_missing_state(self, provider, mock_db):
        """Should fail if no state or authorization_uid provided."""
        with patch("utils.providers.utilityapi._get_db", return_value=mock_db), \
             patch("utils.providers.utilityapi._get_models", return_value=(MagicMock(), MagicMock(), MagicMock())):
            
            result = provider.handle_callback()
            
            assert result.success is False
            assert "Missing" in result.error
    
    def test_handle_callback_connection_not_found(self, provider, mock_db):
        """Should fail if connection not found."""
        MockConnection = MagicMock()
        MockConnection.query.filter_by.return_value.first.return_value = None
        MockConnection.query.filter_by.return_value.all.return_value = []
        
        with patch("utils.providers.utilityapi._get_db", return_value=mock_db), \
             patch("utils.providers.utilityapi._get_models", return_value=(MockConnection, MagicMock(), MagicMock())):
            
            result = provider.handle_callback(
                state="invalid_state",
                authorization_uid="unknown_uid"
            )
            
            assert result.success is False
            assert "not found" in result.error


class TestUtilityAPIProviderSync:
    """Tests for usage data sync."""
    
    def test_sync_usage_success(self, provider, mock_connection, mock_db):
        """Should fetch and parse UtilityAPI data successfully."""
        MockConnection = MagicMock()
        MockUsageData = MagicMock()
        MockSummary = MagicMock()
        MockConnection.query.get.return_value = mock_connection
        MockSummary.query.filter_by.return_value.first.return_value = None
        
        with patch("utils.providers.utilityapi._get_db", return_value=mock_db), \
             patch("utils.providers.utilityapi._get_models", return_value=(MockConnection, MockUsageData, MockSummary)), \
             patch("utils.providers.utilityapi._get_green_button") as mock_gb, \
             patch("utils.providers.utilityapi.requests.get") as mock_get:
            
            # Mock green button functions
            mock_parse = MagicMock(return_value={
                "billing_periods": [{"usage": 500}],
                "intervals": [],
                "unit": "kWh",
            })
            mock_normalize = MagicMock(return_value={
                "fuel_type": "electric",
                "unit": "kWh",
                "annual_usage": 6000.0,
                "monthly_breakdown": [{"month": "2024-01", "usage": 500}],
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
                "data_quality_flags": [],
            })
            mock_gb.return_value = (mock_parse, mock_normalize)
            
            # Mock bills response
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {
                    "bills": [
                        {"start": "2024-01-01", "end": "2024-01-31", "total_kwh": 500}
                    ]
                }
            )
            
            result = provider.sync_usage(connection_id=123)
            
            assert result.success is True
    
    def test_sync_usage_connection_not_found(self, provider, mock_db):
        """Should fail if connection not found."""
        MockConnection = MagicMock()
        MockConnection.query.get.return_value = None
        
        with patch("utils.providers.utilityapi._get_db", return_value=mock_db), \
             patch("utils.providers.utilityapi._get_models", return_value=(MockConnection, MagicMock(), MagicMock())):
            
            result = provider.sync_usage(connection_id=999)
            
            assert result.success is False
            assert "not found" in result.error
    
    def test_sync_usage_not_connected(self, provider, mock_connection, mock_db):
        """Should fail if connection not in connected status."""
        mock_connection.status = "pending_authorization"
        MockConnection = MagicMock()
        MockConnection.query.get.return_value = mock_connection
        
        with patch("utils.providers.utilityapi._get_db", return_value=mock_db), \
             patch("utils.providers.utilityapi._get_models", return_value=(MockConnection, MagicMock(), MagicMock())):
            
            result = provider.sync_usage(connection_id=123)
            
            assert result.success is False
            assert "not ready" in result.error
    
    def test_sync_usage_no_authorization_uid(self, provider, mock_connection, mock_db):
        """Should fail if no authorization UID in metadata."""
        mock_connection.provider_metadata = {}
        MockConnection = MagicMock()
        MockConnection.query.get.return_value = mock_connection
        
        with patch("utils.providers.utilityapi._get_db", return_value=mock_db), \
             patch("utils.providers.utilityapi._get_models", return_value=(MockConnection, MagicMock(), MagicMock())):
            
            result = provider.sync_usage(connection_id=123)
            
            assert result.success is False
            assert "authorization UID" in result.error


class TestUtilityAPIProviderRevoke:
    """Tests for authorization revocation."""
    
    def test_revoke_success(self, provider, mock_connection, mock_db):
        """Should revoke authorization and update status."""
        MockConnection = MagicMock()
        MockConnection.query.get.return_value = mock_connection
        
        with patch("utils.providers.utilityapi._get_db", return_value=mock_db), \
             patch("utils.providers.utilityapi._get_models", return_value=(MockConnection, MagicMock(), MagicMock())), \
             patch("utils.providers.utilityapi.requests.delete") as mock_delete:
            
            mock_delete.return_value = MagicMock(status_code=204)
            
            result = provider.revoke(connection_id=123)
            
            assert result is True
            assert mock_connection.status == ConnectionStatus.REVOKED.value
    
    def test_revoke_not_found(self, provider, mock_db):
        """Should return False if connection not found."""
        MockConnection = MagicMock()
        MockConnection.query.get.return_value = None
        
        with patch("utils.providers.utilityapi._get_db", return_value=mock_db), \
             patch("utils.providers.utilityapi._get_models", return_value=(MockConnection, MagicMock(), MagicMock())):
            
            result = provider.revoke(connection_id=999)
            
            assert result is False


class TestUtilityAPIProviderHelpers:
    """Tests for helper methods."""
    
    def test_map_utility_name_known(self, provider):
        """Should map known utility names."""
        assert provider._map_utility_name("SDGE") == "SDGE"
        assert provider._map_utility_name("SDG&E") == "SDGE"
        assert provider._map_utility_name("PGE") == "PGE"
        assert provider._map_utility_name("PG&E") == "PGE"
    
    def test_map_utility_name_unknown(self, provider):
        """Should pass through unknown utility names."""
        assert provider._map_utility_name("UNKNOWN") == "UNKNOWN"
        assert provider._map_utility_name("custom") == "CUSTOM"
    
    def test_verify_webhook_signature_valid(self, provider):
        """Should verify valid signature."""
        provider.webhook_secret = "test_secret"
        
        import hmac
        import hashlib
        
        payload = b'{"event": "test"}'
        signature = hmac.new(
            b"test_secret",
            payload,
            hashlib.sha256
        ).hexdigest()
        
        assert provider.verify_webhook_signature(payload, signature) is True
    
    def test_verify_webhook_signature_invalid(self, provider):
        """Should reject invalid signature."""
        provider.webhook_secret = "test_secret"
        
        payload = b'{"event": "test"}'
        invalid_signature = "invalid_signature"
        
        assert provider.verify_webhook_signature(payload, invalid_signature) is False
    
    def test_verify_webhook_no_secret(self, provider):
        """Should skip verification if no secret configured."""
        provider.webhook_secret = None
        
        # Should return True (skip verification)
        assert provider.verify_webhook_signature(b'payload', 'any_sig') is True
