# backend/utils/providers/utilityapi.py
"""
UtilityAPI Provider Implementation

This module implements the UtilityProvider interface for UtilityAPI,
a third-party aggregator that supports multiple utilities nationwide.
Used as fallback when direct CMD connections are unavailable.

UtilityAPI Flow:
1. Create authorization form via API
2. User completes form (redirects to utility portal)
3. Receive webhook when authorization is complete
4. Fetch bills/intervals data via API
5. Parse and normalize to common format
"""

import os
import logging
import requests
import secrets
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from . import (
    UtilityProvider,
    ProviderType,
    ConnectionStatus,
    DataScope,
    AuthResult,
    SyncResult,
    UsageSummary,
)

logger = logging.getLogger(__name__)


def _get_db():
    """Lazy import of db to avoid circular imports."""
    from app import db
    return db


def _get_models():
    """Lazy import of models to avoid circular imports."""
    from models import UtilityConnection, UtilityUsageData, UtilityUsageSummary, UtilityIntervalData
    return UtilityConnection, UtilityUsageData, UtilityUsageSummary, UtilityIntervalData


def _get_green_button():
    """Lazy import of green button utils."""
    from utils.green_button import parse_aggregator_response, normalize_usage_to_summary
    return parse_aggregator_response, normalize_usage_to_summary


class UtilityAPIProvider(UtilityProvider):
    """
    UtilityAPI aggregator provider.
    
    Supports multiple utilities nationwide via a single integration.
    Acts as fallback when direct CMD connections fail or are unavailable.
    
    API Documentation: https://utilityapi.com/docs
    """
    
    provider_name = "utilityapi"
    provider_type = ProviderType.AGGREGATOR
    supported_utilities = ["*"]  # Supports all utilities
    
    def __init__(self):
        """Initialize UtilityAPI provider with configuration from environment."""
        self.api_key = os.getenv("UTILITYAPI_API_KEY")
        self.base_url = os.getenv("UTILITYAPI_BASE_URL", "https://utilityapi.com/api/v2")
        self.webhook_url = os.getenv("UTILITYAPI_WEBHOOK_URL")
        self.webhook_secret = os.getenv("UTILITYAPI_WEBHOOK_SECRET")
        self.timeout = 30
    
    def is_available(self) -> bool:
        """Check if UtilityAPI is configured and available."""
        return bool(self.api_key)
    
    def supports_utility(self, utility_name: str) -> bool:
        """UtilityAPI supports all utilities."""
        return True
    
    def get_info(self) -> Dict[str, Any]:
        """Return provider information for API responses."""
        return {
            "name": self.provider_name,
            "type": self.provider_type.value,
            "utilities": self.supported_utilities,
            "available": self.is_available(),
            "description": "Third-party aggregator supporting multiple utilities",
        }
    
    def connect(
        self,
        audit_id: int,
        user_id: str,
        utility_name: str = "unknown",
        data_scope: str = "electric",
    ) -> AuthResult:
        """
        Create UtilityAPI authorization form and return URL for user.
        
        Args:
            audit_id: Associated audit ID
            user_id: User ID making the connection
            utility_name: Name of the utility (e.g., 'SDGE', 'PGE')
            data_scope: Type of data to request (electric, gas, both)
            
        Returns:
            AuthResult with authorization form URL
        """
        if not self.is_available():
            return AuthResult(
                success=False,
                error="UtilityAPI not configured. Missing API key.",
                provider_name=self.provider_name,
            )
        
        db = _get_db()
        UtilityConnection, _, _ = _get_models()
        
        try:
            # Generate unique reference ID for tracking
            reference_id = f"audit_{audit_id}_{secrets.token_hex(8)}"
            
            # Create authorization form via UtilityAPI
            form_response = self._create_authorization_form(
                utility_name=utility_name,
                reference_id=reference_id,
                data_scope=data_scope,
            )
            
            if not form_response.get("success"):
                return AuthResult(
                    success=False,
                    error=form_response.get("error", "Failed to create authorization form"),
                    provider_name=self.provider_name,
                )
            
            form_url = form_response.get("form_url")
            form_uid = form_response.get("uid")
            
            # Create pending connection record
            connection = UtilityConnection(
                user_id=user_id,
                audit_id=audit_id,
                provider_name=self.provider_name,
                utility_name=utility_name.upper(),
                status=ConnectionStatus.PENDING_AUTHORIZATION.value,
                data_scope=data_scope,
                oauth_state=reference_id,  # Store reference for webhook matching
                provider_metadata={
                    "form_uid": form_uid,
                    "reference_id": reference_id,
                    "created_at": datetime.utcnow().isoformat(),
                },
            )
            db.session.add(connection)
            db.session.commit()
            
            logger.info(f"UtilityAPI authorization form created for audit {audit_id}, form UID: {form_uid}")
            
            return AuthResult(
                success=True,
                auth_url=form_url,
                state=reference_id,
                connection_id=connection.id,
                provider_name=self.provider_name,
                requires_redirect=True,
            )
            
        except Exception as e:
            logger.exception(f"Error creating UtilityAPI connection: {e}")
            db.session.rollback()
            return AuthResult(
                success=False,
                error=f"Connection failed: {str(e)}",
                provider_name=self.provider_name,
            )
    
    def handle_callback(self, code: str = None, state: str = None, **kwargs) -> AuthResult:
        """
        Handle UtilityAPI callback/webhook when authorization is complete.
        
        Note: UtilityAPI uses webhooks rather than OAuth callbacks.
        This method handles the webhook payload when authorization completes.
        
        Args:
            code: Not used for UtilityAPI (webhook-based)
            state: Reference ID to match connection
            **kwargs: Additional webhook payload data
            
        Returns:
            AuthResult indicating success/failure
        """
        db = _get_db()
        UtilityConnection, _, _ = _get_models()
        
        # UtilityAPI webhook provides authorization_uid and meters data
        authorization_uid = kwargs.get("authorization_uid")
        meters = kwargs.get("meters", [])
        
        if not state and not authorization_uid:
            return AuthResult(
                success=False,
                error="Missing reference ID or authorization UID",
                provider_name=self.provider_name,
            )
        
        try:
            # Find connection by reference ID (stored in oauth_state)
            connection = None
            if state:
                connection = UtilityConnection.query.filter_by(
                    oauth_state=state,
                    provider_name=self.provider_name,
                ).first()
            
            if not connection and authorization_uid:
                # Try to find by authorization_uid in metadata
                connections = UtilityConnection.query.filter_by(
                    provider_name=self.provider_name,
                    status=ConnectionStatus.PENDING_AUTHORIZATION.value,
                ).all()
                for conn in connections:
                    if conn.provider_metadata.get("authorization_uid") == authorization_uid:
                        connection = conn
                        break
            
            if not connection:
                return AuthResult(
                    success=False,
                    error="Connection not found for callback",
                    provider_name=self.provider_name,
                )
            
            # Update connection with authorization data
            connection.provider_metadata = {
                **connection.provider_metadata,
                "authorization_uid": authorization_uid,
                "meters": meters,
                "authorized_at": datetime.utcnow().isoformat(),
            }
            connection.status = ConnectionStatus.CONNECTED.value
            connection.error_message = None
            
            db.session.commit()
            
            logger.info(f"UtilityAPI authorization complete for connection {connection.id}")
            
            return AuthResult(
                success=True,
                connection_id=connection.id,
                provider_name=self.provider_name,
            )
            
        except Exception as e:
            logger.exception(f"Error handling UtilityAPI callback: {e}")
            db.session.rollback()
            return AuthResult(
                success=False,
                error=f"Callback processing failed: {str(e)}",
                provider_name=self.provider_name,
            )
    
    def sync_usage(self, connection_id: int) -> SyncResult:
        """
        Fetch and persist usage data from UtilityAPI.
        
        Stores individual bill records in utility_usage_data and creates
        a normalized summary in utility_usage_summary.
        
        Args:
            connection_id: ID of the UtilityConnection to sync
            
        Returns:
            SyncResult with import details
        """
        db = _get_db()
        UtilityConnection, UtilityUsageData, UtilityUsageSummary, UtilityIntervalData = _get_models()
        parse_aggregator_response, normalize_usage_to_summary = _get_green_button()
        
        connection = UtilityConnection.query.get(connection_id)
        if not connection:
            return SyncResult(success=False, error="Connection not found")
        
        if connection.status != ConnectionStatus.CONNECTED.value:
            return SyncResult(
                success=False,
                error=f"Connection not ready for sync. Status: {connection.status}"
            )
        
        try:
            connection.status = ConnectionStatus.SYNC_IN_PROGRESS.value
            db.session.commit()
            
            # Get authorization UID from metadata
            authorization_uid = connection.provider_metadata.get("authorization_uid")
            if not authorization_uid:
                connection.status = ConnectionStatus.CONNECTED.value
                connection.last_sync_error = "No authorization UID found"
                db.session.commit()
                return SyncResult(success=False, error="No authorization UID found")
            
            # Fetch bills data from UtilityAPI
            bills_response = self._fetch_bills(authorization_uid)
            if not bills_response.get("success"):
                connection.status = ConnectionStatus.CONNECTED.value
                connection.last_sync_error = bills_response.get("error")
                db.session.commit()
                return SyncResult(success=False, error=bills_response.get("error"))
            
            bills_data = bills_response.get("bills", [])
            
            # Optionally fetch interval data if available
            intervals_response = self._fetch_intervals(authorization_uid)
            intervals_data = intervals_response.get("intervals", []) if intervals_response.get("success") else []
            
            # Parse aggregator response
            parsed_data = parse_aggregator_response({
                "bills": bills_data,
                "intervals": intervals_data,
                "source": "utilityapi",
            })
            
            if parsed_data.get("error"):
                connection.status = ConnectionStatus.CONNECTED.value
                connection.last_sync_error = parsed_data["error"]
                db.session.commit()
                return SyncResult(success=False, error=parsed_data["error"])
            
            # Track existing bill UIDs to avoid duplicates
            existing_records = UtilityUsageData.query.filter_by(
                connection_id=connection.id
            ).all()
            existing_bill_uids = set()
            for record in existing_records:
                if record.raw_data and record.raw_data.get('bill_uid'):
                    existing_bill_uids.add(record.raw_data['bill_uid'])
            
            # Persist individual bill records
            records_imported = 0
            for bill_period in parsed_data.get('billing_periods', []):
                bill_uid = bill_period.get('bill_uid')
                
                # Skip if we already have this bill
                if bill_uid and bill_uid in existing_bill_uids:
                    logger.debug(f"Skipping duplicate bill: {bill_uid}")
                    continue
                
                # Parse dates
                try:
                    period_start = datetime.strptime(bill_period['start_date'], '%Y-%m-%d').date()
                    period_end = datetime.strptime(bill_period['end_date'], '%Y-%m-%d').date()
                except Exception as e:
                    logger.warning(f"Failed to parse bill dates: {e}")
                    continue
                
                usage_record = UtilityUsageData(
                    user_id=connection.user_id,
                    audit_id=connection.audit_id,
                    connection_id=connection.id,
                    period_start=period_start,
                    period_end=period_end,
                    usage_amount=bill_period.get('usage', 0),
                    unit=bill_period.get('unit', 'kWh'),
                    cost_usd=bill_period.get('cost_usd'),
                    source="api",
                    raw_data={
                        "format": "utilityapi_bill",
                        "bill_uid": bill_uid,
                        "service_tariff": bill_period.get('service_tariff'),
                        "service_address": bill_period.get('service_address'),
                        "month": bill_period.get('month'),
                    },
                )
                db.session.add(usage_record)
                records_imported += 1
                
                if bill_uid:
                    existing_bill_uids.add(bill_uid)
            
            # Persist interval records (15-minute data) if available
            intervals_imported = 0
            if parsed_data.get('intervals'):
                # Get existing interval UIDs to avoid duplicates
                existing_interval_uids = set()
                existing_intervals = UtilityIntervalData.query.filter_by(
                    connection_id=connection.id
                ).with_entities(UtilityIntervalData.interval_uid).all()
                for (uid,) in existing_intervals:
                    if uid:
                        existing_interval_uids.add(uid)
                
                for interval in parsed_data['intervals']:
                    interval_uid = interval.get('interval_uid')
                    
                    # Skip duplicates
                    if interval_uid and interval_uid in existing_interval_uids:
                        continue
                    
                    # Parse interval timestamps
                    try:
                        start_str = interval.get('start', '').replace('Z', '+00:00')
                        end_str = interval.get('end', '').replace('Z', '+00:00') if interval.get('end') else None
                        
                        if 'T' in start_str:
                            interval_start = datetime.fromisoformat(start_str)
                        else:
                            interval_start = datetime.strptime(start_str[:19], '%Y-%m-%d %H:%M:%S')
                        
                        # If no end time, assume 15-minute interval
                        if end_str:
                            if 'T' in end_str:
                                interval_end = datetime.fromisoformat(end_str)
                            else:
                                interval_end = datetime.strptime(end_str[:19], '%Y-%m-%d %H:%M:%S')
                        else:
                            interval_end = interval_start + timedelta(minutes=15)
                        
                    except Exception as e:
                        logger.warning(f"Failed to parse interval timestamps: {e}")
                        continue
                    
                    interval_record = UtilityIntervalData(
                        user_id=connection.user_id,
                        audit_id=connection.audit_id,
                        connection_id=connection.id,
                        interval_start=interval_start,
                        interval_end=interval_end,
                        usage_kwh=interval.get('value', 0),
                        interval_uid=interval_uid,
                    )
                    db.session.add(interval_record)
                    intervals_imported += 1
                    
                    if interval_uid:
                        existing_interval_uids.add(interval_uid)
                
                logger.info(f"Stored {intervals_imported} interval records for connection {connection_id}")
            
            # Normalize and persist summary
            summary_data = normalize_usage_to_summary(
                parsed_data,
                fuel_type=connection.data_scope or "electric",
                data_format="utilityapi_json"
            )
            
            # Create or update summary
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
            
            months_covered = len(summary_data.get("monthly_breakdown", []))
            
            logger.info(
                f"UtilityAPI sync complete for connection {connection_id}: "
                f"{records_imported} bills, {intervals_imported} intervals, {months_covered} months"
            )
            
            return SyncResult(
                success=True,
                records_imported=records_imported,
                date_range_start=summary_data.get("start_date"),
                date_range_end=summary_data.get("end_date"),
                months_covered=months_covered,
                warnings=summary_data.get("data_quality_flags", []),
            )
            
        except requests.RequestException as e:
            logger.exception(f"Network error during UtilityAPI sync: {e}")
            connection.status = ConnectionStatus.CONNECTED.value
            connection.last_sync_error = f"Network error: {str(e)}"
            db.session.commit()
            return SyncResult(success=False, error=f"Network error: {str(e)}")
            
        except Exception as e:
            logger.exception(f"Unexpected error during UtilityAPI sync: {e}")
            connection.status = ConnectionStatus.CONNECTED.value
            connection.last_sync_error = str(e)
            db.session.commit()
            return SyncResult(success=False, error=f"Sync failed: {str(e)}")
    
    def get_normalized_usage(self, connection_id: int) -> Optional[UsageSummary]:
        """
        Get normalized usage summary for a connection.
        
        Args:
            connection_id: ID of the UtilityConnection
            
        Returns:
            UsageSummary or None if not available
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
            tou_data=None,  # UtilityAPI may not provide TOU data
            data_quality_flags=summary.data_quality_flags or [],
        )
    
    def revoke(self, connection_id: int) -> bool:
        """
        Revoke UtilityAPI authorization.
        
        Args:
            connection_id: ID of the UtilityConnection to revoke
            
        Returns:
            True if revocation successful
        """
        db = _get_db()
        UtilityConnection, _, _ = _get_models()
        
        connection = UtilityConnection.query.get(connection_id)
        if not connection:
            logger.warning(f"Connection {connection_id} not found for revocation")
            return False
        
        try:
            # Call UtilityAPI to revoke authorization
            authorization_uid = connection.provider_metadata.get("authorization_uid")
            if authorization_uid:
                self._revoke_authorization(authorization_uid)
            
            # Update connection status
            connection.status = ConnectionStatus.REVOKED.value
            connection.access_token = None
            connection.refresh_token = None
            connection.provider_metadata = {
                **connection.provider_metadata,
                "revoked_at": datetime.utcnow().isoformat(),
            }
            
            db.session.commit()
            
            logger.info(f"UtilityAPI authorization revoked for connection {connection_id}")
            return True
            
        except Exception as e:
            logger.exception(f"Error revoking UtilityAPI authorization: {e}")
            db.session.rollback()
            return False
    
    # =========================================================================
    # Private API Methods
    # =========================================================================
    
    def _create_authorization_form(
        self,
        utility_name: str,
        reference_id: str,
        data_scope: str,
    ) -> Dict[str, Any]:
        """
        Create a UtilityAPI authorization form.
        
        Args:
            utility_name: Name of the utility
            reference_id: Unique reference for tracking
            data_scope: Type of data to request
            
        Returns:
            Dict with form_url and uid on success, error on failure
        """
        try:
            # Map utility name to UtilityAPI utility code if needed
            utility_code = self._map_utility_name(utility_name)
            
            payload = {
                "referral": reference_id,
                "utility": utility_code,
                "include_bills": True,
                "include_intervals": True,
            }
            
            # Add webhook URL if configured
            if self.webhook_url:
                payload["webhook"] = self.webhook_url
            
            response = requests.post(
                f"{self.base_url}/forms",
                headers=self._get_headers(),
                json=payload,
                timeout=self.timeout,
            )
            
            if response.status_code not in (200, 201):
                logger.error(f"UtilityAPI form creation failed: {response.status_code} - {response.text[:200]}")
                return {
                    "success": False,
                    "error": f"API error: {response.status_code}",
                }
            
            data = response.json()
            return {
                "success": True,
                "form_url": data.get("url"),
                "uid": data.get("uid"),
            }
            
        except requests.RequestException as e:
            logger.exception(f"Network error creating UtilityAPI form: {e}")
            return {"success": False, "error": f"Network error: {str(e)}"}
    
    def _fetch_bills(self, authorization_uid: str) -> Dict[str, Any]:
        """
        Fetch bills for an authorization.
        
        Args:
            authorization_uid: UtilityAPI authorization UID
            
        Returns:
            Dict with bills list on success, error on failure
        """
        try:
            # UtilityAPI uses query params: /bills?authorizations=123
            response = requests.get(
                f"{self.base_url}/bills",
                headers=self._get_headers(),
                params={"authorizations": authorization_uid},
                timeout=self.timeout,
            )
            
            if response.status_code != 200:
                logger.error(f"UtilityAPI bills fetch failed: {response.status_code}")
                return {
                    "success": False,
                    "error": f"API error: {response.status_code}",
                }
            
            data = response.json()
            return {
                "success": True,
                "bills": data.get("bills", []),
            }
            
        except requests.RequestException as e:
            logger.exception(f"Network error fetching UtilityAPI bills: {e}")
            return {"success": False, "error": f"Network error: {str(e)}"}
    
    def _fetch_intervals(self, authorization_uid: str) -> Dict[str, Any]:
        """
        Fetch interval data for an authorization (if available).
        
        Args:
            authorization_uid: UtilityAPI authorization UID
            
        Returns:
            Dict with intervals list on success, error on failure
        """
        try:
            # UtilityAPI uses query params: /intervals?authorizations=123
            response = requests.get(
                f"{self.base_url}/intervals",
                headers=self._get_headers(),
                params={"authorizations": authorization_uid},
                timeout=self.timeout,
            )
            
            if response.status_code == 404:
                # Interval data not available for this authorization
                return {"success": True, "intervals": []}
            
            if response.status_code != 200:
                logger.warning(f"UtilityAPI intervals fetch failed: {response.status_code}")
                return {"success": True, "intervals": []}  # Non-fatal
            
            data = response.json()
            return {
                "success": True,
                "intervals": data.get("intervals", []),
            }
            
        except requests.RequestException as e:
            logger.warning(f"Network error fetching UtilityAPI intervals: {e}")
            return {"success": True, "intervals": []}  # Non-fatal
    
    def get_meters_for_authorization(self, authorization_uid: str) -> Dict[str, Any]:
        """
        Fetch meters associated with an authorization.
        
        Args:
            authorization_uid: UtilityAPI authorization UID
            
        Returns:
            Dict with meters list on success, error on failure
        """
        try:
            # UtilityAPI uses query params: /meters?authorizations=123
            response = requests.get(
                f"{self.base_url}/meters",
                headers=self._get_headers(),
                params={"authorizations": authorization_uid},
                timeout=self.timeout,
            )
            
            if response.status_code != 200:
                logger.error(f"UtilityAPI meters fetch failed: {response.status_code}")
                return {
                    "success": False,
                    "error": f"API error: {response.status_code}",
                }
            
            data = response.json()
            return {
                "success": True,
                "meters": data.get("meters", []),
            }
            
        except requests.RequestException as e:
            logger.exception(f"Network error fetching UtilityAPI meters: {e}")
            return {"success": False, "error": f"Network error: {str(e)}"}
    
    def trigger_historical_collection(
        self, 
        meter_uids: list, 
        collection_duration_months: int = 12
    ) -> Dict[str, Any]:
        """
        Trigger historical data collection for meters.
        
        After authorization is complete, this must be called to actually
        collect the historical billing and interval data.
        
        Args:
            meter_uids: List of meter UIDs to collect data for
            collection_duration_months: Number of months to collect (1-36, default 12)
            
        Returns:
            Dict with success status and meters triggered
        """
        try:
            response = requests.post(
                f"{self.base_url}/meters/historical-collection",
                headers=self._get_headers(),
                json={
                    "meters": meter_uids,
                    "collection_duration": collection_duration_months,
                },
                timeout=self.timeout,
            )
            
            if response.status_code == 200:
                data = response.json()
                logger.info(
                    f"UtilityAPI historical collection triggered for meters: {data.get('meters')}"
                )
                return {
                    "success": True,
                    "meters": data.get("meters", []),
                    "collection_duration": data.get("collection_duration"),
                }
            elif response.status_code == 402:
                logger.warning("UtilityAPI historical collection failed: Payment required")
                return {
                    "success": False,
                    "error": "Payment required - add balance to UtilityAPI account",
                }
            else:
                error_msg = f"API error: {response.status_code}"
                try:
                    error_data = response.json()
                    error_msg = error_data.get("error", {}).get("message", error_msg)
                except:
                    pass
                logger.error(f"UtilityAPI historical collection failed: {error_msg}")
                return {"success": False, "error": error_msg}
                
        except requests.RequestException as e:
            logger.exception(f"Network error triggering UtilityAPI historical collection: {e}")
            return {"success": False, "error": f"Network error: {str(e)}"}
    
    def _revoke_authorization(self, authorization_uid: str) -> bool:
        """
        Revoke an authorization via UtilityAPI.
        
        Args:
            authorization_uid: UtilityAPI authorization UID
            
        Returns:
            True if successful
        """
        try:
            response = requests.delete(
                f"{self.base_url}/authorizations/{authorization_uid}",
                headers=self._get_headers(),
                timeout=self.timeout,
            )
            
            if response.status_code in (200, 204, 404):
                return True
            
            logger.warning(f"UtilityAPI revocation returned: {response.status_code}")
            return False
            
        except requests.RequestException as e:
            logger.exception(f"Network error revoking UtilityAPI authorization: {e}")
            return False
    
    def _get_headers(self) -> Dict[str, str]:
        """Get API request headers."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
    
    def _map_utility_name(self, utility_name: str) -> str:
        """
        Map common utility names to UtilityAPI utility codes.
        
        UtilityAPI uses specific utility codes that are case-sensitive.
        See: https://utilityapi.com/docs/utilities
        
        Args:
            utility_name: Common utility name
            
        Returns:
            UtilityAPI utility code
        """
        # UtilityAPI utility codes (case-sensitive!)
        # Our app uses simplified names, UtilityAPI uses codes with special chars
        mapping = {
            "SDGE": "SDG&E",      # San Diego Gas and Electric
            "SDG&E": "SDG&E",
            "PGE": "PG&E",        # Pacific Gas and Electric
            "PG&E": "PG&E",
            "SCE": "SCE",         # Southern California Edison
            "SOCALGAS": "SoCalGas", # Southern California Gas
            "SOCAL_GAS": "SoCalGas",
            "LADWP": "LADWP",     # Los Angeles DWP (not currently supported by UtilityAPI)
            "SMUD": "SMUD",       # Sacramento Municipal Utility District (not in list)
            "DEMO": "DEMO",       # UtilityAPI demo/test utility
        }
        
        result = mapping.get(utility_name.upper(), utility_name)
        logger.debug(f"Mapped utility name '{utility_name}' to UtilityAPI code '{result}'")
        return result
    
    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """
        Verify webhook signature from UtilityAPI.
        
        Args:
            payload: Raw request body
            signature: Signature header value
            
        Returns:
            True if signature is valid
        """
        if not self.webhook_secret:
            logger.warning("Webhook secret not configured, skipping verification")
            return True
        
        import hmac
        import hashlib
        
        expected = hmac.new(
            self.webhook_secret.encode(),
            payload,
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(expected, signature)
