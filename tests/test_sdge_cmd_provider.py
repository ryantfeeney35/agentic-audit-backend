# backend/tests/test_sdge_cmd_provider.py
"""
Tests for SDG&E Connect My Data (CMD) Provider

Tests OAuth flow, token management, and data sync with mocked
SDG&E API responses.
"""

import pytest
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock

# Set test environment before imports
os.environ["SDGE_CMD_CLIENT_ID"] = "test_client_id"
os.environ["SDGE_CMD_CLIENT_SECRET"] = "test_client_secret"
os.environ["SDGE_CMD_REDIRECT_URI"] = "http://localhost:8080/api/utility/callback"

from utils.providers.sdge_cmd import SDGECMDProvider
from utils.providers import AuthResult, SyncResult, ConnectionStatus


# Sample ESPI response for testing
SAMPLE_ESPI_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:espi="http://naesb.org/espi">
  <entry>
    <content>
      <espi:ServiceCategory>
        <espi:kind>0</espi:kind>
      </espi:ServiceCategory>
    </content>
  </entry>
  <entry>
    <content>
      <espi:IntervalBlock>
        <espi:IntervalReading>
          <espi:timePeriod>
            <espi:start>1704067200</espi:start>
            <espi:duration>3600</espi:duration>
          </espi:timePeriod>
          <espi:value>500</espi:value>
        </espi:IntervalReading>
      </espi:IntervalBlock>
    </content>
  </entry>
</feed>
"""


@pytest.fixture
def provider():
    """Create SDG&E CMD provider with test credentials."""
    return SDGECMDProvider()


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
    conn.oauth_state = "test_state:123"
    conn.access_token = "test_access_token"
    conn.refresh_token = "test_refresh_token"
    conn.token_expires_at = datetime.utcnow() + timedelta(hours=1)
    conn.provider_metadata = {"subscription_id": "sub-123"}
    conn.data_scope = "electric"
    return conn


class TestSDGECMDProviderAvailability:
    """Tests for provider availability checks."""
    
    def test_is_available_with_credentials(self, provider):
        """Should be available when all credentials are set."""
        assert provider.is_available() is True
    
    def test_is_not_available_without_client_id(self):
        """Should not be available without client ID."""
        with patch.dict(os.environ, {"SDGE_CMD_CLIENT_ID": ""}):
            provider = SDGECMDProvider()
            assert provider.is_available() is False
    
    def test_provider_info(self, provider):
        """Should return correct provider info."""
        info = provider.get_info()
        
        assert info["name"] == "sdge_cmd"
        assert info["type"] == "direct_cmd"
        assert "SDGE" in info["utilities"]
    
    def test_supports_sdge(self, provider):
        """Should support SDG&E utility."""
        assert provider.supports_utility("SDGE") is True
        assert provider.supports_utility("sdge") is True
        assert provider.supports_utility("PGE") is False


class TestSDGECMDProviderConnect:
    """Tests for OAuth connection initiation."""
    
    def test_connect_creates_auth_url(self, provider, mock_db):
        """Should create auth URL with correct parameters."""
        mock_conn_instance = MagicMock()
        mock_conn_instance.id = 123
        MockConnection = MagicMock(return_value=mock_conn_instance)
        
        with patch("utils.providers.sdge_cmd._get_db", return_value=mock_db), \
             patch("utils.providers.sdge_cmd._get_models", return_value=(MockConnection, MagicMock(), MagicMock())):
            
            result = provider.connect(
                audit_id=1,
                user_id="user-123",
                utility_name="SDGE",
                data_scope="electric"
            )
            
            assert result.success is True
            assert result.auth_url is not None
            assert "authorize" in result.auth_url
            assert result.connection_id == 123
            assert result.requires_redirect is True
    
    def test_connect_includes_oauth_params(self, provider, mock_db):
        """Should include required OAuth parameters in auth URL."""
        mock_conn = MagicMock()
        mock_conn.id = 1
        MockConnection = MagicMock(return_value=mock_conn)
        
        with patch("utils.providers.sdge_cmd._get_db", return_value=mock_db), \
             patch("utils.providers.sdge_cmd._get_models", return_value=(MockConnection, MagicMock(), MagicMock())):
            
            result = provider.connect(
                audit_id=1,
                user_id="user-123",
                utility_name="SDGE"
            )
            
            assert "client_id=test_client_id" in result.auth_url
            assert "redirect_uri=" in result.auth_url
            assert "response_type=code" in result.auth_url
            assert "state=" in result.auth_url
    
    def test_connect_rejects_non_sdge(self, provider, mock_db):
        """Should reject non-SDG&E utilities."""
        # Need to mock db to prevent import chain errors
        with patch("utils.providers.sdge_cmd._get_db", return_value=mock_db), \
             patch("utils.providers.sdge_cmd._get_models", return_value=(MagicMock(), MagicMock(), MagicMock())):
            
            result = provider.connect(
                audit_id=1,
                user_id="user-123",
                utility_name="PGE"
            )
            
            assert result.success is False
            assert "SDG&E" in result.error
    
    def test_connect_fails_without_credentials(self, mock_db):
        """Should fail gracefully without credentials."""
        with patch.dict(os.environ, {"SDGE_CMD_CLIENT_ID": ""}):
            provider = SDGECMDProvider()
            
            with patch("utils.providers.sdge_cmd._get_db", return_value=mock_db), \
                 patch("utils.providers.sdge_cmd._get_models", return_value=(MagicMock(), MagicMock(), MagicMock())):
                
                result = provider.connect(
                    audit_id=1,
                    user_id="user-123",
                    utility_name="SDGE"
                )
                
                assert result.success is False
                assert "not configured" in result.error


class TestSDGECMDProviderCallback:
    """Tests for OAuth callback handling."""
    
    def test_handle_callback_exchanges_token(self, provider, mock_connection, mock_db):
        """Should exchange code for tokens on successful callback."""
        MockConnection = MagicMock()
        MockConnection.query.get.return_value = mock_connection
        
        with patch("utils.providers.sdge_cmd._get_db", return_value=mock_db), \
             patch("utils.providers.sdge_cmd._get_models", return_value=(MockConnection, MagicMock(), MagicMock())), \
             patch("utils.providers.sdge_cmd.requests.post") as mock_post:
            
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {
                    "access_token": "new_access_token",
                    "refresh_token": "new_refresh_token",
                    "expires_in": 3600,
                }
            )
            
            result = provider.handle_callback(
                code="auth_code_123",
                state="test_state:123"
            )
            
            assert result.success is True
            assert result.connection_id == 123
            mock_post.assert_called_once()
    
    def test_handle_callback_invalid_state(self, provider, mock_db):
        """Should fail on invalid state parameter."""
        # Need to mock db to prevent import chain errors
        with patch("utils.providers.sdge_cmd._get_db", return_value=mock_db), \
             patch("utils.providers.sdge_cmd._get_models", return_value=(MagicMock(), MagicMock(), MagicMock())):
            
            result = provider.handle_callback(
                code="auth_code",
                state="invalid_state_no_id"
            )
            
            assert result.success is False
            assert "Invalid state" in result.error
    
    def test_handle_callback_state_mismatch(self, provider, mock_connection, mock_db):
        """Should fail on state mismatch (CSRF protection)."""
        mock_connection.oauth_state = "original_state:123"
        MockConnection = MagicMock()
        MockConnection.query.get.return_value = mock_connection
        
        with patch("utils.providers.sdge_cmd._get_db", return_value=mock_db), \
             patch("utils.providers.sdge_cmd._get_models", return_value=(MockConnection, MagicMock(), MagicMock())):
            
            result = provider.handle_callback(
                code="auth_code",
                state="different_state:123"
            )
            
            assert result.success is False
            assert "verification failed" in result.error
    
    def test_handle_callback_token_error(self, provider, mock_connection, mock_db):
        """Should handle token exchange errors."""
        MockConnection = MagicMock()
        MockConnection.query.get.return_value = mock_connection
        
        with patch("utils.providers.sdge_cmd._get_db", return_value=mock_db), \
             patch("utils.providers.sdge_cmd._get_models", return_value=(MockConnection, MagicMock(), MagicMock())), \
             patch("utils.providers.sdge_cmd.requests.post") as mock_post:
            
            mock_post.return_value = MagicMock(
                status_code=400,
                json=lambda: {"error": "invalid_grant"},
                text="Invalid grant"
            )
            
            result = provider.handle_callback(
                code="invalid_code",
                state="test_state:123"
            )
            
            assert result.success is False
            assert "Token exchange failed" in result.error


class TestSDGECMDProviderSync:
    """Tests for usage data sync."""
    
    def test_sync_usage_success(self, provider, mock_connection, mock_db):
        """Should fetch and parse ESPI data successfully."""
        MockConnection = MagicMock()
        MockUsageData = MagicMock()
        MockSummary = MagicMock()
        MockConnection.query.get.return_value = mock_connection
        MockSummary.query.filter_by.return_value.first.return_value = None
        
        with patch("utils.providers.sdge_cmd._get_db", return_value=mock_db), \
             patch("utils.providers.sdge_cmd._get_models", return_value=(MockConnection, MockUsageData, MockSummary)), \
             patch("utils.providers.sdge_cmd._get_green_button") as mock_gb, \
             patch("utils.providers.sdge_cmd.requests.get") as mock_get:
            
            # Mock green button functions - parse returns dict with expected shape
            mock_parse = MagicMock(return_value={
                "intervals": [{"value": 500}],
                "billing_periods": [],
                "unit": "kWh",
                "fuel_type": "electric",
            })
            mock_normalize = MagicMock(return_value={
                "fuel_type": "electric",
                "unit": "kWh",
                "annual_usage": 6000.0,
                "monthly_breakdown": [{"month": "2024-01", "usage": 500}],
                "start_date": "2024-01-01",
                "end_date": "2024-01-31",
                "months_covered": 1,
                "data_quality_flags": [],
            })
            mock_gb.return_value = (mock_parse, mock_normalize)
            
            mock_get.return_value = MagicMock(
                status_code=200,
                text=SAMPLE_ESPI_RESPONSE
            )
            
            result = provider.sync_usage(connection_id=123)
            
            assert result.success is True
            mock_get.assert_called_once()
    
    def test_sync_usage_connection_not_found(self, provider, mock_db):
        """Should fail if connection not found."""
        MockConnection = MagicMock()
        MockConnection.query.get.return_value = None
        
        with patch("utils.providers.sdge_cmd._get_db", return_value=mock_db), \
             patch("utils.providers.sdge_cmd._get_models", return_value=(MockConnection, MagicMock(), MagicMock())):
            
            result = provider.sync_usage(connection_id=999)
            
            assert result.success is False
            assert "not found" in result.error
    
    def test_sync_usage_not_connected(self, provider, mock_connection, mock_db):
        """Should fail if connection not in connected status."""
        mock_connection.status = "pending_authorization"
        MockConnection = MagicMock()
        MockConnection.query.get.return_value = mock_connection
        
        with patch("utils.providers.sdge_cmd._get_db", return_value=mock_db), \
             patch("utils.providers.sdge_cmd._get_models", return_value=(MockConnection, MagicMock(), MagicMock())):
            
            result = provider.sync_usage(connection_id=123)
            
            assert result.success is False
            assert "not ready" in result.error


class TestSDGECMDProviderRevoke:
    """Tests for connection revocation."""
    
    def test_revoke_clears_tokens(self, provider, mock_connection, mock_db):
        """Should clear tokens and mark as revoked."""
        MockConnection = MagicMock()
        MockConnection.query.get.return_value = mock_connection
        
        with patch("utils.providers.sdge_cmd._get_db", return_value=mock_db), \
             patch("utils.providers.sdge_cmd._get_models", return_value=(MockConnection, MagicMock(), MagicMock())):
            
            result = provider.revoke(connection_id=123)
            
            assert result is True
            assert mock_connection.status == ConnectionStatus.REVOKED.value
    
    def test_revoke_not_found(self, provider, mock_db):
        """Should return False if connection not found."""
        MockConnection = MagicMock()
        MockConnection.query.get.return_value = None
        
        with patch("utils.providers.sdge_cmd._get_db", return_value=mock_db), \
             patch("utils.providers.sdge_cmd._get_models", return_value=(MockConnection, MagicMock(), MagicMock())):
            
            result = provider.revoke(connection_id=999)
            
            assert result is False


class TestSDGECMDProviderTokenRefresh:
    """Tests for token refresh logic."""
    
    def test_needs_token_refresh_when_expired(self, provider):
        """Should need refresh when token is expired."""
        mock_conn = MagicMock()
        mock_conn.token_expires_at = datetime.utcnow() - timedelta(minutes=10)
        
        assert provider._needs_token_refresh(mock_conn) is True
    
    def test_needs_token_refresh_within_buffer(self, provider):
        """Should need refresh when token expires within 5 minutes."""
        mock_conn = MagicMock()
        mock_conn.token_expires_at = datetime.utcnow() + timedelta(minutes=3)
        
        assert provider._needs_token_refresh(mock_conn) is True
    
    def test_no_refresh_needed_when_valid(self, provider):
        """Should not need refresh when token is valid."""
        mock_conn = MagicMock()
        mock_conn.token_expires_at = datetime.utcnow() + timedelta(hours=1)
        
        assert provider._needs_token_refresh(mock_conn) is False
    
    def test_refresh_token_success(self, provider, mock_db):
        """Should refresh token successfully."""
        mock_conn = MagicMock()
        mock_conn.id = 123
        mock_conn.refresh_token = "old_refresh_token"
        
        with patch("utils.providers.sdge_cmd._get_db", return_value=mock_db), \
             patch("utils.providers.sdge_cmd.requests.post") as mock_post:
            
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {
                    "access_token": "new_access_token",
                    "refresh_token": "new_refresh_token",
                    "expires_in": 3600,
                }
            )
            
            result = provider._refresh_token(mock_conn)
            
            assert result is True
            mock_post.assert_called_once()


class TestSDGECMDProviderHelpers:
    """Tests for helper methods."""
    
    def test_build_scope_electric(self, provider):
        """Should build scope for electric data."""
        scope = provider._build_scope("electric")
        
        assert "FB=1_1" in scope
        assert "HistoricalPeriod" in scope
    
    def test_build_scope_gas(self, provider):
        """Should build scope for gas data."""
        scope = provider._build_scope("gas")
        
        assert "FB=1_1" in scope
    
    def test_get_resource_uri_from_metadata(self, provider):
        """Should use resource URI from metadata if available."""
        mock_conn = MagicMock()
        mock_conn.provider_metadata = {
            "resource_uri": "https://api.sdge.com/custom/uri"
        }
        
        uri = provider._get_resource_uri(mock_conn)
        
        assert uri == "https://api.sdge.com/custom/uri"
    
    def test_get_resource_uri_from_subscription(self, provider):
        """Should construct URI from subscription ID."""
        mock_conn = MagicMock()
        mock_conn.provider_metadata = {
            "subscription_id": "sub-12345"
        }
        
        uri = provider._get_resource_uri(mock_conn)
        
        assert "sub-12345" in uri
        assert "UsagePoint" in uri


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
