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
        redirect_uri: Optional[str] = None
    ):
        """
        Initialize the Enphase client.
        
        Args:
            client_id: Enphase OAuth client ID (defaults to env var)
            client_secret: Enphase OAuth client secret (defaults to env var)
            redirect_uri: OAuth redirect URI (defaults to env var)
        """
        self.client_id = client_id or os.environ.get("ENPHASE_CLIENT_ID")
        self.client_secret = client_secret or os.environ.get("ENPHASE_CLIENT_SECRET")
        self.redirect_uri = redirect_uri or os.environ.get("ENPHASE_REDIRECT_URI")
        
        if not all([self.client_id, self.client_secret]):
            raise ValueError("ENPHASE_CLIENT_ID and ENPHASE_CLIENT_SECRET must be set")
        
        self._session = requests.Session()
        self._max_retries = 3
        self._base_delay = 1.0  # seconds
    
    def get_authorization_url(self, state: str) -> str:
        """
        Generate the OAuth authorization URL for user redirect.
        
        Args:
            state: CSRF protection state parameter
            
        Returns:
            Full authorization URL to redirect user to
        """
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "state": state,
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{ENPHASE_AUTH_URL}?{query}"
    
    def exchange_code_for_tokens(self, code: str) -> EnphaseTokenResponse:
        """
        Exchange authorization code for access and refresh tokens.
        
        Args:
            code: Authorization code from OAuth callback
            
        Returns:
            EnphaseTokenResponse with tokens and expiration
            
        Raises:
            EnphaseAuthError: If token exchange fails
        """
        logger.info("ENPHASE_TOKEN_EXCHANGE | Exchanging authorization code for tokens")
        
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
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
        Refresh an expired access token.
        
        Args:
            refresh_token: Valid refresh token
            
        Returns:
            EnphaseTokenResponse with new tokens
            
        Raises:
            EnphaseAuthError: If refresh fails (token may be revoked)
        """
        logger.info("ENPHASE_TOKEN_REFRESH | Refreshing access token")
        
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
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
        """
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        
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
