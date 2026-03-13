"""
Enphase API Client

Client for interacting with the Enphase Developer API v4.
Handles OAuth token exchange, system discovery, and telemetry data retrieval.

Reference: https://developer-v4.enphase.com/docs.html
"""

import os
import time
import logging
import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Enphase API v4 endpoints
ENPHASE_AUTH_URL = "https://api.enphaseenergy.com/oauth/authorize"
ENPHASE_TOKEN_URL = "https://api.enphaseenergy.com/oauth/token"
ENPHASE_API_BASE = "https://api.enphaseenergy.com/api/v4"


@dataclass
class EnphaseTokenResponse:
    """Response from Enphase token exchange or refresh."""
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "Bearer"
    
    @property
    def expires_at(self) -> datetime:
        """Calculate token expiration datetime."""
        return datetime.utcnow() + timedelta(seconds=self.expires_in)


@dataclass
class EnphaseSystem:
    """Enphase system information."""
    system_id: str
    name: str
    public_name: str
    status: str
    timezone: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None


@dataclass
class EnphaseTelemetryPoint:
    """Single telemetry data point."""
    timestamp: datetime
    production_wh: Optional[float] = None
    consumption_wh: Optional[float] = None
    grid_import_wh: Optional[float] = None
    grid_export_wh: Optional[float] = None
    battery_charge_wh: Optional[float] = None
    battery_discharge_wh: Optional[float] = None


class EnphaseAPIError(Exception):
    """Base exception for Enphase API errors."""
    def __init__(self, message: str, status_code: Optional[int] = None, response: Optional[Dict] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class EnphaseRateLimitError(EnphaseAPIError):
    """Raised when API rate limit is exceeded."""
    def __init__(self, retry_after: Optional[int] = None):
        super().__init__("Rate limit exceeded", status_code=429)
        self.retry_after = retry_after


class EnphaseAuthError(EnphaseAPIError):
    """Raised when authentication fails."""
    pass


class EnphaseClient:
    """
    Client for Enphase Developer API v4.
    
    Handles:
    - OAuth 2.0 token exchange and refresh
    - System discovery
    - Telemetry data retrieval (production, consumption, battery, grid)
    - Rate limit handling with exponential backoff
    """
    
    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        redirect_uri: Optional[str] = None,
        api_key: Optional[str] = None
    ):
        """
        Initialize the Enphase client.
        
        Args:
            client_id: Enphase OAuth client ID (defaults to env var)
            client_secret: Enphase OAuth client secret (defaults to env var)
            redirect_uri: OAuth redirect URI (defaults to env var)
            api_key: Enphase API key for v4 API requests (defaults to env var)
        """
        self.client_id = client_id or os.environ.get("ENPHASE_CLIENT_ID")
        self.client_secret = client_secret or os.environ.get("ENPHASE_CLIENT_SECRET")
        self.redirect_uri = redirect_uri or os.environ.get("ENPHASE_REDIRECT_URI")
        self._api_key = api_key or os.environ.get("ENPHASE_API_KEY")
        
        if not all([self.client_id, self.client_secret, self._api_key]):
            raise ValueError("ENPHASE_CLIENT_ID, ENPHASE_CLIENT_SECRET, and ENPHASE_API_KEY must be set")
        
        self._session = requests.Session()
        self._max_retries = 3
        self._base_delay = 1.0  # seconds
    
    def get_authorization_url(self, state: str) -> str:
        """
        Generate the OAuth authorization URL for user redirect.
        
        This is Step 1 of the OAuth 2.0 Authorization Code flow:
        1. Generate auth URL with state parameter → redirect user to Enphase
        2. User authorizes access on Enphase website
        3. Enphase redirects back to our callback with authorization code
        4. We exchange code for access/refresh tokens (see exchange_code_for_tokens)
        
        Args:
            state: CSRF protection state parameter. This value is stored in the
                   EnphaseConnection.oauth_state field when creating the pending
                   connection. When Enphase redirects back, we verify that the
                   returned state matches to prevent CSRF attacks.
            
        Returns:
            Full authorization URL to redirect user to. The URL opens Enphase's
            OAuth consent screen where the user can authorize our app to access
            their system data.
        
        Security Notes:
            - State parameter must be cryptographically random (use secrets.token_urlsafe)
            - State is single-use and should be cleared after successful token exchange
            - Redirect URI must exactly match what's registered in Enphase portal
        """
        # Build OAuth authorization request parameters per RFC 6749 Section 4.1.1
        params = {
            "response_type": "code",       # Request authorization code (not implicit token)
            "client_id": self.client_id,   # Our registered application ID
            "redirect_uri": self.redirect_uri,  # Must match Enphase portal registration
            "state": state,                # CSRF protection - stored in DB, validated on callback
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{ENPHASE_AUTH_URL}?{query}"
    
    def exchange_code_for_tokens(self, code: str) -> EnphaseTokenResponse:
        """
        Exchange authorization code for access and refresh tokens.
        
        This is Step 4 of the OAuth 2.0 Authorization Code flow:
        After the user authorizes on Enphase and is redirected back to our
        callback URL with an authorization code, we exchange that short-lived
        code for long-lived access and refresh tokens.
        
        Token Lifecycle:
            - Access token: Short-lived (typically 24 hours), used for API calls
            - Refresh token: Long-lived, used to obtain new access tokens
            - Both tokens must be stored encrypted (see EnphaseConnection model)
        
        Args:
            code: Authorization code from OAuth callback query parameter.
                  This code is single-use and expires quickly (usually 10 min).
            
        Returns:
            EnphaseTokenResponse containing:
                - access_token: Bearer token for API authentication
                - refresh_token: Token for obtaining new access tokens
                - expires_in: Access token lifetime in seconds
            
        Raises:
            EnphaseAuthError: If token exchange fails. Common causes:
                - Code already used or expired
                - Redirect URI mismatch
                - Invalid client credentials
        
        Security Notes:
            - This request uses HTTP Basic Auth with client_id:client_secret
            - Code should only be exchanged once; discard after success or failure
            - Store tokens encrypted at rest using TOKEN_ENCRYPTION_KEY
        """
        logger.info("ENPHASE_TOKEN_EXCHANGE | Exchanging authorization code for tokens")
        
        # Build token request per OAuth 2.0 spec (RFC 6749 Section 4.1.3)
        data = {
            "grant_type": "authorization_code",  # Identifies this as code exchange
            "code": code,                         # The authorization code from callback
            "redirect_uri": self.redirect_uri,   # Must match the original auth request
        }
        
        try:
            response = self._session.post(
                ENPHASE_TOKEN_URL,
                data=data,
                auth=(self.client_id, self.client_secret),
                timeout=30
            )
            
            if response.status_code != 200:
                logger.error(f"ENPHASE_TOKEN_EXCHANGE_FAILED | status={response.status_code}")
                raise EnphaseAuthError(
                    f"Token exchange failed: {response.text}",
                    status_code=response.status_code,
                    response=response.json() if response.text else None
                )
            
            data = response.json()
            logger.info("ENPHASE_TOKEN_EXCHANGE_SUCCESS | Tokens received")
            
            return EnphaseTokenResponse(
                access_token=data["access_token"],
                refresh_token=data["refresh_token"],
                expires_in=data.get("expires_in", 86400),
                token_type=data.get("token_type", "Bearer")
            )
            
        except requests.RequestException as e:
            logger.error(f"ENPHASE_TOKEN_EXCHANGE_ERROR | error={e}")
            raise EnphaseAuthError(f"Token exchange request failed: {e}")
    
    def refresh_access_token(self, refresh_token: str) -> EnphaseTokenResponse:
        """
        Refresh an expired access token using the refresh token.
        
        Token Refresh Strategy:
            Access tokens expire after ~24 hours. Before making API calls, check
            if the token is expired or will expire soon (within 5 minutes). If so,
            call this method to obtain a fresh access token.
        
        When to Refresh:
            - Before API calls when token_expires_at < now + 5 minutes
            - After receiving 401 Unauthorized from an API call
            - Proactively during sync operations to avoid mid-sync expiration
        
        Args:
            refresh_token: The refresh token from the original OAuth exchange or
                           previous refresh. Stored encrypted in EnphaseConnection.
            
        Returns:
            EnphaseTokenResponse containing:
                - access_token: New bearer token for API calls
                - refresh_token: New refresh token (may be rotated)
                - expires_in: New token lifetime in seconds
        
        Raises:
            EnphaseAuthError: If refresh fails. Causes include:
                - User revoked access from Enphase portal
                - Refresh token expired (very long inactivity)
                - Application credentials changed
        
        Important:
            - On 401, mark connection as 'requires_reauthorization' status
            - User must re-authorize through OAuth flow to restore access
            - Always store the new refresh_token (it may be rotated)
        """
        logger.info("ENPHASE_TOKEN_REFRESH | Refreshing access token")
        
        # Build refresh request per OAuth 2.0 spec (RFC 6749 Section 6)
        data = {
            "grant_type": "refresh_token",   # Identifies this as a refresh request
            "refresh_token": refresh_token,  # The stored refresh token
        }
        
        try:
            response = self._session.post(
                ENPHASE_TOKEN_URL,
                data=data,
                auth=(self.client_id, self.client_secret),
                timeout=30
            )
            
            if response.status_code == 401:
                logger.warning("ENPHASE_TOKEN_REFRESH_REVOKED | Refresh token may be revoked")
                raise EnphaseAuthError(
                    "Refresh token is invalid or revoked",
                    status_code=401
                )
            
            if response.status_code != 200:
                logger.error(f"ENPHASE_TOKEN_REFRESH_FAILED | status={response.status_code}")
                raise EnphaseAuthError(
                    f"Token refresh failed: {response.text}",
                    status_code=response.status_code
                )
            
            data = response.json()
            logger.info("ENPHASE_TOKEN_REFRESH_SUCCESS | New tokens received")
            
            return EnphaseTokenResponse(
                access_token=data["access_token"],
                refresh_token=data["refresh_token"],
                expires_in=data.get("expires_in", 86400),
                token_type=data.get("token_type", "Bearer")
            )
            
        except requests.RequestException as e:
            logger.error(f"ENPHASE_TOKEN_REFRESH_ERROR | error={e}")
            raise EnphaseAuthError(f"Token refresh request failed: {e}")
    
    def get_systems(self, access_token: str) -> List[EnphaseSystem]:
        """
        Discover all Enphase systems for the authenticated user.
        
        Args:
            access_token: Valid OAuth access token
            
        Returns:
            List of EnphaseSystem objects
            
        Raises:
            EnphaseAPIError: If API call fails
        """
        logger.info("ENPHASE_GET_SYSTEMS | Fetching user systems")
        
        response = self._api_request(
            "GET",
            f"{ENPHASE_API_BASE}/systems",
            access_token=access_token
        )
        
        systems = []
        for sys_data in response.get("systems", []):
            systems.append(EnphaseSystem(
                system_id=str(sys_data["system_id"]),
                name=sys_data.get("name", ""),
                public_name=sys_data.get("public_name", ""),
                status=sys_data.get("status", "unknown"),
                timezone=sys_data.get("timezone"),
                meta=sys_data
            ))
        
        logger.info(f"ENPHASE_GET_SYSTEMS_SUCCESS | Found {len(systems)} systems")
        return systems
    
    def get_telemetry(
        self,
        access_token: str,
        system_id: str,
        telemetry_type: str,
        start_at: Optional[datetime] = None,
        end_at: Optional[datetime] = None,
        granularity: str = "15mins"
    ) -> List[EnphaseTelemetryPoint]:
        """
        Fetch telemetry data for a system.
        
        Args:
            access_token: Valid OAuth access token
            system_id: Enphase system ID
            telemetry_type: One of 'production', 'consumption', 'battery', 'grid'
            start_at: Start of time range (defaults to 24 hours ago)
            end_at: End of time range (defaults to now)
            granularity: Data resolution ('5mins', '15mins', 'day', 'week', 'month')
            
        Returns:
            List of EnphaseTelemetryPoint objects
            
        Raises:
            EnphaseAPIError: If API call fails
        """
        # Default time range: last 24 hours
        if end_at is None:
            end_at = datetime.utcnow()
        if start_at is None:
            start_at = end_at - timedelta(days=1)
        
        logger.info(
            f"ENPHASE_GET_TELEMETRY | system={system_id} type={telemetry_type} "
            f"start={start_at.isoformat()} end={end_at.isoformat()} granularity={granularity}"
        )
        
        # Map telemetry type to API endpoint
        endpoint_map = {
            "production": "energy_lifetime",  # Solar production
            "consumption": "consumption_stats",  # Home consumption
            "battery": "battery",  # Battery state
            "grid": "rgm_stats",  # Revenue grade meter (grid)
        }
        
        # Use the telemetry endpoint for granular data
        endpoint = f"{ENPHASE_API_BASE}/systems/{system_id}/telemetry/{telemetry_type}"
        
        params = {
            "start_at": int(start_at.timestamp()),
            "end_at": int(end_at.timestamp()),
            "granularity": granularity,
        }
        
        try:
            response = self._api_request(
                "GET",
                endpoint,
                access_token=access_token,
                params=params
            )
        except EnphaseAPIError as e:
            # If endpoint returns 404, equipment may not be present
            if e.status_code == 404:
                logger.warning(f"ENPHASE_TELEMETRY_NOT_AVAILABLE | type={telemetry_type}")
                return []
            raise
        
        points = self._parse_telemetry_response(response, telemetry_type)
        logger.info(f"ENPHASE_GET_TELEMETRY_SUCCESS | {len(points)} data points retrieved")
        
        return points
    
    def _parse_telemetry_response(
        self,
        response: Dict[str, Any],
        telemetry_type: str
    ) -> List[EnphaseTelemetryPoint]:
        """Parse API response into telemetry points."""
        points = []
        
        # Response format varies by telemetry type
        intervals = response.get("intervals", response.get("data", []))
        
        for interval in intervals:
            timestamp = datetime.fromtimestamp(interval.get("end_at", interval.get("timestamp", 0)))
            
            point = EnphaseTelemetryPoint(timestamp=timestamp)
            
            # Map fields based on telemetry type
            if telemetry_type == "production":
                point.production_wh = interval.get("wh_del", interval.get("enwh"))
            elif telemetry_type == "consumption":
                point.consumption_wh = interval.get("wh_del", interval.get("enwh"))
            elif telemetry_type == "battery":
                point.battery_charge_wh = interval.get("charge_wh", 0)
                point.battery_discharge_wh = interval.get("discharge_wh", 0)
            elif telemetry_type == "grid":
                point.grid_import_wh = interval.get("import_wh", interval.get("wh_del", 0))
                point.grid_export_wh = interval.get("export_wh", interval.get("wh_rec", 0))
            
            points.append(point)
        
        return points
    
    def _api_request(
        self,
        method: str,
        url: str,
        access_token: str,
        params: Optional[Dict] = None,
        data: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Make an authenticated API request with retry and rate limit handling.
        
        Implements exponential backoff for rate limits and transient errors.
        
        Note: Enphase API v4 requires BOTH:
        - Authorization header with Bearer token (OAuth)
        - API key as query parameter (key=client_id)
        """
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        
        # Enphase API v4 requires API key in query params alongside OAuth token
        if params is None:
            params = {}
        params["key"] = self._api_key
        
        last_exception = None
        
        for attempt in range(self._max_retries):
            try:
                response = self._session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    json=data,
                    timeout=60
                )
                
                # Handle rate limiting
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", self._base_delay * (2 ** attempt)))
                    logger.warning(f"ENPHASE_RATE_LIMITED | retry_after={retry_after}s attempt={attempt + 1}")
                    
                    if attempt < self._max_retries - 1:
                        time.sleep(retry_after)
                        continue
                    raise EnphaseRateLimitError(retry_after=retry_after)
                
                # Handle auth errors (don't retry)
                if response.status_code == 401:
                    raise EnphaseAuthError("Access token is invalid or expired", status_code=401)
                
                # Handle other errors
                if response.status_code >= 400:
                    raise EnphaseAPIError(
                        f"API request failed: {response.text}",
                        status_code=response.status_code,
                        response=response.json() if response.text else None
                    )
                
                return response.json()
                
            except requests.RequestException as e:
                last_exception = e
                delay = self._base_delay * (2 ** attempt)
                logger.warning(f"ENPHASE_REQUEST_ERROR | error={e} attempt={attempt + 1} retry_in={delay}s")
                
                if attempt < self._max_retries - 1:
                    time.sleep(delay)
                    continue
        
        raise EnphaseAPIError(f"API request failed after {self._max_retries} attempts: {last_exception}")
