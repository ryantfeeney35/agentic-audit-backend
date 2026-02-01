# backend/utils/providers/sdge_cmd.py
"""
SDG&E Connect My Data (CMD) Provider

Direct integration with San Diego Gas & Electric's Green Button
Connect My Data implementation using OAuth 2.0 and ESPI/Atom resources.

This provider enables direct utility data access without going through
a third-party aggregator. Requires SDG&E business application approval.

Environment variables required:
- SDGE_CMD_CLIENT_ID: OAuth client ID from SDG&E
- SDGE_CMD_CLIENT_SECRET: OAuth client secret
- SDGE_CMD_REDIRECT_URI: OAuth callback URL (must match SDG&E registration)
- SDGE_CMD_BASE_URL: API base URL (defaults to production)
- SDGE_CMD_AUTH_URL: OAuth authorization endpoint
- SDGE_CMD_TOKEN_URL: OAuth token endpoint

Reference: https://www.sdge.com/more-information/environment/green-button
"""

import os
import secrets
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from urllib.parse import urlencode

import requests

from . import (
    UtilityProvider,
    ProviderType,
    AuthResult,
    SyncResult,
    UsageSummary,
    ConnectionStatus,
)

logger = logging.getLogger(__name__)


def _get_db():
    """Lazy import of db to avoid circular imports."""
    from app import db
    return db


def _get_models():
    """Lazy import of models to avoid circular imports."""
    from models import UtilityConnection, UtilityUsageData, UtilityUsageSummary
    return UtilityConnection, UtilityUsageData, UtilityUsageSummary


def _get_green_button():
    """Lazy import of green button utils."""
    from utils.green_button import parse_espi_atom_xml, normalize_usage_to_summary
    return parse_espi_atom_xml, normalize_usage_to_summary


class SDGECMDProvider(UtilityProvider):
    """
    SDG&E Connect My Data (Green Button CMD) direct integration.
    
    Implements the UtilityProvider interface for direct OAuth-based
    access to SDG&E customer utility data via their ESPI API.
    """
    
    provider_name = "sdge_cmd"
    provider_type = ProviderType.DIRECT_CMD
    display_name = "SDG&E Connect My Data"
    supported_utilities = ["SDGE"]
    
    # SDG&E CMD endpoints (defaults, overridable via env)
    DEFAULT_BASE_URL = "https://api.sdge.com/greenbutton"
    DEFAULT_AUTH_URL = "https://myaccount.sdge.com/portal/oauth/authorize"
    DEFAULT_TOKEN_URL = "https://api.sdge.com/oauth/token"
    
    def __init__(self):
        """Initialize provider with credentials from environment."""
        self.client_id = os.getenv("SDGE_CMD_CLIENT_ID")
        self.client_secret = os.getenv("SDGE_CMD_CLIENT_SECRET")
        self.redirect_uri = os.getenv("SDGE_CMD_REDIRECT_URI")
        self.base_url = os.getenv("SDGE_CMD_BASE_URL", self.DEFAULT_BASE_URL)
        self.auth_url = os.getenv("SDGE_CMD_AUTH_URL", self.DEFAULT_AUTH_URL)
        self.token_url = os.getenv("SDGE_CMD_TOKEN_URL", self.DEFAULT_TOKEN_URL)
        
        # Request timeout (seconds)
        self.timeout = int(os.getenv("SDGE_CMD_TIMEOUT", "30"))
    
    def is_available(self) -> bool:
        """
        Check if SDG&E CMD is properly configured.
        
        Requires client_id, client_secret, and redirect_uri to be set.
        """
        available = bool(
            self.client_id and 
            self.client_secret and 
            self.redirect_uri
        )
        if not available:
            logger.debug("SDG&E CMD not available: missing credentials")
        return available
    
    def connect(
        self, 
        audit_id: int, 
        user_id: str, 
        utility_name: str,
        data_scope: str = 'electric'
    ) -> AuthResult:
        """
        Initiate SDG&E OAuth authorization flow.
        
        Creates a pending connection record and returns the authorization
        URL for redirecting the user to SDG&E's My Account portal.
        
        Args:
            audit_id: ID of the audit
            user_id: ID of the user
            utility_name: Should be 'SDGE'
            data_scope: 'electric', 'gas', or 'both'
            
        Returns:
            AuthResult with auth_url for OAuth redirect
        """
        db = _get_db()
        UtilityConnection, _, _ = _get_models()
        
        if not self.is_available():
            return AuthResult(
                success=False,
                error="SDG&E Connect My Data is not configured. Please contact support."
            )
        
        if utility_name.upper() != "SDGE":
            return AuthResult(
                success=False,
                error=f"This provider only supports SDG&E, not {utility_name}"
            )
        
        try:
            # Generate secure random state
            state_token = secrets.token_urlsafe(32)
            
            # Create pending connection record
            connection = UtilityConnection(
                user_id=user_id,
                audit_id=audit_id,
                utility_name="SDGE",
                provider_name=self.provider_name,
                data_scope=data_scope,
                status=ConnectionStatus.PENDING_AUTHORIZATION.value,
                oauth_state=state_token,
            )
            db.session.add(connection)
            db.session.commit()
            
            # Encode connection_id in state for callback routing
            # Format: "{random_token}:{connection_id}"
            state_with_id = f"{state_token}:{connection.id}"
            
            # Update connection with full state
            connection.oauth_state = state_with_id
            db.session.commit()
            
            # Build OAuth authorization URL
            auth_params = {
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "response_type": "code",
                "scope": self._build_scope(data_scope),
                "state": state_with_id,
            }
            
            auth_url = f"{self.auth_url}?{urlencode(auth_params)}"
            
            logger.info(f"SDG&E CMD connect initiated for audit {audit_id}, connection {connection.id}")
            
            return AuthResult(
                success=True,
                auth_url=auth_url,
                state=state_with_id,
                connection_id=connection.id,
                provider_name=self.provider_name,
                requires_redirect=True,
            )
            
        except Exception as e:
            logger.exception(f"Failed to initiate SDG&E CMD connection: {e}")
            db.session.rollback()
            return AuthResult(
                success=False,
                error=f"Failed to initiate connection: {str(e)}"
            )
    
    def handle_callback(self, code: str, state: str) -> AuthResult:
        """
        Handle OAuth callback from SDG&E.
        
        Exchanges authorization code for access/refresh tokens,
        persists encrypted tokens, and marks connection as active.
        
        Args:
            code: OAuth authorization code from SDG&E
            state: OAuth state parameter (contains connection_id)
            
        Returns:
            AuthResult indicating success or failure
        """
        db = _get_db()
        UtilityConnection, _, _ = _get_models()
        
        # Extract connection_id from state
        try:
            _, conn_id_str = state.rsplit(":", 1)
            conn_id = int(conn_id_str)
        except (ValueError, AttributeError) as e:
            logger.error(f"Invalid state parameter format: {state}")
            return AuthResult(
                success=False,
                error="Invalid state parameter"
            )
        
        # Look up connection
        connection = UtilityConnection.query.get(conn_id)
        if not connection:
            logger.error(f"Connection {conn_id} not found")
            return AuthResult(
                success=False,
                error=f"Connection not found"
            )
        
        # Verify state matches (security check)
        if connection.oauth_state != state:
            logger.error(f"State mismatch for connection {conn_id}")
            connection.status = ConnectionStatus.FAILED.value
            connection.last_sync_error = "State verification failed"
            db.session.commit()
            return AuthResult(
                success=False,
                error="State verification failed",
                connection_id=conn_id
            )
        
        try:
            # Exchange code for tokens
            token_response = requests.post(
                self.token_url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=self.timeout,
            )
            
            if token_response.status_code != 200:
                error_msg = f"Token exchange failed: {token_response.status_code}"
                try:
                    error_detail = token_response.json()
                    error_msg = f"{error_msg} - {error_detail.get('error_description', error_detail)}"
                except:
                    error_msg = f"{error_msg} - {token_response.text[:200]}"
                
                logger.error(error_msg)
                connection.status = ConnectionStatus.FAILED.value
                connection.last_sync_error = error_msg
                db.session.commit()
                
                return AuthResult(
                    success=False,
                    error=error_msg,
                    connection_id=conn_id
                )
            
            tokens = token_response.json()
            
            # Persist encrypted tokens
            connection.access_token = tokens.get("access_token")
            connection.refresh_token = tokens.get("refresh_token")
            
            # Calculate expiration
            expires_in = tokens.get("expires_in", 3600)
            connection.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
            
            # Store provider-specific metadata
            connection.provider_metadata = {
                "subscription_id": tokens.get("subscription_id"),
                "resource_uri": tokens.get("resourceURI"),
                "authorized_at": datetime.utcnow().isoformat(),
                "scope": tokens.get("scope"),
            }
            
            connection.status = ConnectionStatus.CONNECTED.value
            connection.last_sync_error = None
            connection.oauth_state = None  # Clear state after successful use
            
            db.session.commit()
            
            logger.info(f"SDG&E CMD connection {conn_id} authorized successfully")
            
            return AuthResult(
                success=True,
                connection_id=conn_id,
                provider_name=self.provider_name,
            )
            
        except requests.RequestException as e:
            logger.exception(f"Network error during token exchange: {e}")
            connection.status = ConnectionStatus.FAILED.value
            connection.last_sync_error = f"Network error: {str(e)}"
            db.session.commit()
            
            return AuthResult(
                success=False,
                error=f"Network error during authorization: {str(e)}",
                connection_id=conn_id
            )
        except Exception as e:
            logger.exception(f"Unexpected error during callback: {e}")
            connection.status = ConnectionStatus.FAILED.value
            connection.last_sync_error = str(e)
            db.session.commit()
            
            return AuthResult(
                success=False,
                error=f"Authorization failed: {str(e)}",
                connection_id=conn_id
            )
    
    def sync_usage(self, connection_id: int) -> SyncResult:
        """
        Fetch and persist usage data from SDG&E.
        
        Retrieves ESPI UsagePoint/IntervalBlock data, parses the Atom/XML
        response, and creates normalized usage summary.
        
        Args:
            connection_id: ID of the UtilityConnection
            
        Returns:
            SyncResult with import statistics
        """
        db = _get_db()
        UtilityConnection, UtilityUsageData, UtilityUsageSummary = _get_models()
        parse_espi_atom_xml, normalize_usage_to_summary = _get_green_button()
        
        connection = UtilityConnection.query.get(connection_id)
        if not connection:
            return SyncResult(success=False, error=f"Connection {connection_id} not found")
        
        if connection.status != ConnectionStatus.CONNECTED.value:
            return SyncResult(
                success=False, 
                error=f"Connection not ready (status: {connection.status})"
            )
        
        try:
            # Refresh token if needed
            if self._needs_token_refresh(connection):
                refresh_result = self._refresh_token(connection)
                if not refresh_result:
                    return SyncResult(success=False, error="Token refresh failed")
            
            # Get access token
            access_token = connection.access_token
            if not access_token:
                return SyncResult(success=False, error="No access token available")
            
            # Update status
            connection.status = ConnectionStatus.SYNC_IN_PROGRESS.value
            db.session.commit()
            
            # Determine resource URI
            resource_uri = self._get_resource_uri(connection)
            
            # Fetch ESPI data
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/atom+xml",
            }
            
            logger.info(f"Fetching ESPI data from {resource_uri}")
            
            response = requests.get(
                resource_uri,
                headers=headers,
                timeout=self.timeout,
            )
            
            if response.status_code == 401:
                # Token may have expired, try refresh
                if self._refresh_token(connection):
                    # Retry with new token
                    access_token = connection.access_token
                    headers["Authorization"] = f"Bearer {access_token}"
                    response = requests.get(resource_uri, headers=headers, timeout=self.timeout)
            
            if response.status_code != 200:
                error_msg = f"Data fetch failed: HTTP {response.status_code}"
                logger.error(f"{error_msg} - {response.text[:500]}")
                connection.status = ConnectionStatus.FAILED.value
                connection.last_sync_error = error_msg
                db.session.commit()
                return SyncResult(success=False, error=error_msg)
            
            # Parse ESPI Atom/XML
            raw_xml = response.text
            parsed_data = parse_espi_atom_xml(raw_xml)
            
            if "error" in parsed_data:
                connection.status = ConnectionStatus.CONNECTED.value
                connection.last_sync_error = parsed_data["error"]
                db.session.commit()
                return SyncResult(
                    success=False, 
                    error=f"Failed to parse response: {parsed_data['error']}"
                )
            
            # Persist raw data
            raw_record = UtilityUsageData(
                user_id=connection.user_id,
                audit_id=connection.audit_id,
                connection_id=connection.id,
                period_start=datetime.utcnow().date(),  # Placeholder
                period_end=datetime.utcnow().date(),
                usage_amount=0,  # Aggregated separately
                unit=parsed_data.get("unit", "kWh"),
                source="api",
                raw_data={
                    "format": "espi_atom_xml",
                    "fetched_at": datetime.utcnow().isoformat(),
                    "intervals_count": len(parsed_data.get("intervals", [])),
                    "billing_periods_count": len(parsed_data.get("billing_periods", [])),
                },
            )
            db.session.add(raw_record)
            
            # Normalize and persist summary
            summary_data = normalize_usage_to_summary(
                parsed_data, 
                fuel_type=connection.data_scope or "electric",
                data_format="espi_atom_xml"
            )
            
            # Create or update summary record
            summary = UtilityUsageSummary.query.filter_by(
                connection_id=connection.id
            ).first()
            
            if not summary:
                summary = UtilityUsageSummary(
                    connection_id=connection.id,
                    audit_id=connection.audit_id,
                    user_id=connection.user_id,
                )
                db.session.add(summary)
            
            # Update summary fields
            summary.fuel_type = summary_data.get("fuel_type", "electric")
            summary.unit = summary_data.get("unit", "kWh")
            summary.annual_usage = summary_data.get("annual_usage")
            summary.annual_cost_usd = summary_data.get("annual_cost_usd")
            summary.monthly_breakdown = summary_data.get("monthly_breakdown", [])
            summary.seasonal_pattern = summary_data.get("seasonal_pattern")
            summary.data_quality_flags = summary_data.get("data_quality_flags", [])
            summary.updated_at = datetime.utcnow()
            
            # Parse dates
            if summary_data.get("start_date"):
                try:
                    summary.start_date = datetime.fromisoformat(summary_data["start_date"]).date()
                except:
                    pass
            if summary_data.get("end_date"):
                try:
                    summary.end_date = datetime.fromisoformat(summary_data["end_date"]).date()
                except:
                    pass
            
            # Update connection
            connection.status = ConnectionStatus.CONNECTED.value
            connection.last_sync_at = datetime.utcnow()
            connection.last_sync_error = None
            
            db.session.commit()
            
            months_covered = summary_data.get("months_covered", len(summary_data.get("monthly_breakdown", [])))
            
            logger.info(f"SDG&E CMD sync complete for connection {connection_id}: {months_covered} months")
            
            return SyncResult(
                success=True,
                records_imported=len(parsed_data.get("intervals", [])),
                date_range_start=summary_data.get("start_date"),
                date_range_end=summary_data.get("end_date"),
                months_covered=months_covered,
                warnings=summary_data.get("data_quality_flags", []),
            )
            
        except requests.RequestException as e:
            logger.exception(f"Network error during sync: {e}")
            connection.status = ConnectionStatus.CONNECTED.value
            connection.last_sync_error = f"Network error: {str(e)}"
            db.session.commit()
            return SyncResult(success=False, error=f"Network error: {str(e)}")
            
        except Exception as e:
            logger.exception(f"Unexpected error during sync: {e}")
            connection.status = ConnectionStatus.CONNECTED.value
            connection.last_sync_error = str(e)
            db.session.commit()
            return SyncResult(success=False, error=f"Sync failed: {str(e)}")
    
    def get_normalized_usage(self, connection_id: int) -> Optional[UsageSummary]:
        """
        Return cached normalized usage summary.
        
        Args:
            connection_id: ID of the UtilityConnection
            
        Returns:
            UsageSummary or None
        """
        _, _, UtilityUsageSummary = _get_models()
        
        summary = UtilityUsageSummary.query.filter_by(
            connection_id=connection_id
        ).first()
        
        if not summary:
            return None
        
        return UsageSummary(
            fuel_type=summary.fuel_type or "electric",
            start_date=summary.start_date.isoformat() if summary.start_date else "",
            end_date=summary.end_date.isoformat() if summary.end_date else "",
            annual_usage=summary.annual_usage or 0,
            annual_cost_usd=summary.annual_cost_usd,
            monthly_breakdown=summary.monthly_breakdown or [],
            seasonal_pattern=summary.seasonal_pattern,
            tou_data=summary.tou_data,
            data_quality_flags=summary.data_quality_flags or [],
            unit=summary.unit or "kWh",
        )
    
    def revoke(self, connection_id: int) -> bool:
        """
        Revoke SDG&E authorization.
        
        Attempts to call SDG&E's revocation endpoint (if available),
        then marks the connection as revoked and clears tokens.
        
        Args:
            connection_id: ID of the UtilityConnection
            
        Returns:
            True if revocation successful
        """
        db = _get_db()
        UtilityConnection, _, _ = _get_models()
        
        connection = UtilityConnection.query.get(connection_id)
        if not connection:
            return False
        
        try:
            # Attempt to revoke at SDG&E (if they support it)
            # Most utilities don't have a programmatic revocation endpoint
            # User must revoke via My Account portal
            
            access_token = connection.access_token
            if access_token:
                # Try revocation endpoint if configured
                revoke_url = os.getenv("SDGE_CMD_REVOKE_URL")
                if revoke_url:
                    try:
                        requests.post(
                            revoke_url,
                            data={"token": access_token},
                            headers={"Content-Type": "application/x-www-form-urlencoded"},
                            timeout=10,
                        )
                    except Exception as e:
                        logger.warning(f"Revocation endpoint call failed: {e}")
            
            # Clear tokens and mark as revoked
            connection.access_token = None
            connection.refresh_token = None
            connection.token_expires_at = None
            connection.status = ConnectionStatus.REVOKED.value
            connection.provider_metadata = connection.provider_metadata or {}
            connection.provider_metadata["revoked_at"] = datetime.utcnow().isoformat()
            
            db.session.commit()
            
            logger.info(f"SDG&E CMD connection {connection_id} revoked")
            return True
            
        except Exception as e:
            logger.exception(f"Error revoking connection: {e}")
            db.session.rollback()
            return False
    
    def _build_scope(self, data_scope: str) -> str:
        """
        Build OAuth scope parameter for SDG&E.
        
        SDG&E uses FB (Fuel Type Billing) parameter in scope.
        
        Args:
            data_scope: 'electric', 'gas', or 'both'
            
        Returns:
            Scope string for OAuth request
        """
        # SDG&E scope format may vary - adjust based on actual API docs
        scope_parts = ["FB=1_1"]  # Version 1.1 of Green Button
        
        if data_scope == "electric":
            scope_parts.append("DataCustodianScopeSelection=2")  # Electric only
        elif data_scope == "gas":
            scope_parts.append("DataCustodianScopeSelection=1")  # Gas only
        else:
            scope_parts.append("DataCustodianScopeSelection=3")  # Both
        
        # Request 13 months of history
        scope_parts.append("HistoricalPeriod=P13M")
        
        return " ".join(scope_parts)
    
    def _needs_token_refresh(self, connection) -> bool:
        """Check if token needs refresh (expires within 5 minutes)."""
        if not connection.token_expires_at:
            return False
        return connection.token_expires_at < datetime.utcnow() + timedelta(minutes=5)
    
    def _refresh_token(self, connection) -> bool:
        """
        Refresh OAuth access token.
        
        Args:
            connection: UtilityConnection instance
            
        Returns:
            True if refresh successful
        """
        db = _get_db()
        
        refresh_token = connection.refresh_token
        if not refresh_token:
            logger.error(f"No refresh token for connection {connection.id}")
            return False
        
        try:
            response = requests.post(
                self.token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=self.timeout,
            )
            
            if response.status_code != 200:
                logger.error(f"Token refresh failed: {response.status_code} - {response.text[:200]}")
                return False
            
            tokens = response.json()
            
            connection.access_token = tokens.get("access_token")
            if tokens.get("refresh_token"):
                connection.refresh_token = tokens.get("refresh_token")
            
            expires_in = tokens.get("expires_in", 3600)
            connection.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
            
            db.session.commit()
            
            logger.info(f"Token refreshed for connection {connection.id}")
            return True
            
        except Exception as e:
            logger.exception(f"Token refresh error: {e}")
            return False
    
    def _get_resource_uri(self, connection) -> str:
        """
        Determine the ESPI resource URI for data retrieval.
        
        Args:
            connection: UtilityConnection instance
            
        Returns:
            ESPI resource URI
        """
        # Check if stored during authorization
        metadata = connection.provider_metadata or {}
        if metadata.get("resource_uri"):
            return metadata["resource_uri"]
        
        # Construct default URI based on subscription
        subscription_id = metadata.get("subscription_id")
        if subscription_id:
            return f"{self.base_url}/espi/1_1/resource/Subscription/{subscription_id}/UsagePoint"
        
        # Fallback to batch endpoint
        return f"{self.base_url}/espi/1_1/resource/Batch/Subscription"


# Export
__all__ = ["SDGECMDProvider"]
