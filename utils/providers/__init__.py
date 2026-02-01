# backend/utils/providers/__init__.py
"""
Utility Provider Abstraction Layer

This module defines the abstract interface for utility data providers,
enabling a waterfall pattern: SDG&E CMD → UtilityAPI → Manual entry.

Each provider implements the same interface, allowing the system to
transparently fall back between providers when one fails or is unavailable.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum


class ProviderType(str, Enum):
    """Type of utility data provider."""
    DIRECT_CMD = "direct_cmd"      # Direct Green Button Connect My Data (e.g., SDG&E)
    AGGREGATOR = "aggregator"       # Third-party aggregator (UtilityAPI, Arcadia)
    MANUAL = "manual"               # Manual entry fallback


class ConnectionStatus(str, Enum):
    """Status of a utility connection."""
    NOT_CONNECTED = "not_connected"
    PENDING_AUTHORIZATION = "pending_authorization"
    CONNECTED = "connected"
    SYNC_IN_PROGRESS = "sync_in_progress"
    FAILED = "failed"
    REVOKED = "revoked"


class DataScope(str, Enum):
    """Scope of utility data to retrieve."""
    ELECTRIC = "electric"
    GAS = "gas"
    BOTH = "both"


@dataclass
class AuthResult:
    """Result of a connection/authorization attempt."""
    success: bool
    auth_url: Optional[str] = None       # URL to redirect user for OAuth
    state: Optional[str] = None          # OAuth state parameter
    connection_id: Optional[int] = None  # Created connection record ID
    provider_name: Optional[str] = None  # Name of the provider used
    error: Optional[str] = None
    requires_redirect: bool = True       # False for manual entry (no OAuth)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "success": self.success,
            "auth_url": self.auth_url,
            "state": self.state,
            "connection_id": self.connection_id,
            "provider_name": self.provider_name,
            "error": self.error,
            "requires_redirect": self.requires_redirect,
        }


@dataclass
class SyncResult:
    """Result of a data sync operation."""
    success: bool
    records_imported: int = 0
    date_range_start: Optional[str] = None
    date_range_end: Optional[str] = None
    months_covered: int = 0
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "success": self.success,
            "records_imported": self.records_imported,
            "date_range_start": self.date_range_start,
            "date_range_end": self.date_range_end,
            "months_covered": self.months_covered,
            "error": self.error,
            "warnings": self.warnings,
        }


@dataclass
class UsageSummary:
    """Normalized utility usage summary."""
    fuel_type: str
    start_date: str
    end_date: str
    annual_usage: float  # kWh for electric, therms for gas
    annual_cost_usd: Optional[float] = None
    monthly_breakdown: List[Dict[str, Any]] = field(default_factory=list)
    seasonal_pattern: Optional[Dict[str, float]] = None
    tou_data: Optional[Dict[str, Any]] = None
    data_quality_flags: List[str] = field(default_factory=list)
    unit: str = "kWh"  # kWh or therms

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "fuel_type": self.fuel_type,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "annual_usage": self.annual_usage,
            "annual_usage_display": f"{self.annual_usage:,.0f} {self.unit}",
            "annual_cost_usd": self.annual_cost_usd,
            "monthly_breakdown": self.monthly_breakdown,
            "seasonal_pattern": self.seasonal_pattern,
            "tou_data": self.tou_data,
            "data_quality_flags": self.data_quality_flags,
            "unit": self.unit,
        }


class UtilityProvider(ABC):
    """
    Abstract base class for utility data providers.
    
    All providers (SDG&E CMD, UtilityAPI, Manual) implement this interface,
    enabling transparent fallback between providers.
    """
    
    # Class attributes to be defined by subclasses
    provider_name: str = ""
    provider_type: ProviderType = ProviderType.MANUAL
    display_name: str = ""
    supported_utilities: List[str] = []  # e.g., ['SDGE'] for CMD, ['*'] for aggregator
    
    @abstractmethod
    def connect(
        self, 
        audit_id: int, 
        user_id: str, 
        utility_name: str,
        data_scope: str = 'electric'
    ) -> AuthResult:
        """
        Initiate connection/authorization flow.
        
        For OAuth providers, returns auth_url for user redirect.
        For manual entry, creates connection record directly.
        
        Args:
            audit_id: ID of the audit to associate with this connection
            user_id: ID of the user initiating the connection
            utility_name: Name of the utility (e.g., 'SDGE', 'PGE')
            data_scope: Type of data to retrieve ('electric', 'gas', 'both')
            
        Returns:
            AuthResult with auth_url for OAuth redirect or connection details
        """
        pass
    
    @abstractmethod
    def handle_callback(self, code: str, state: str) -> AuthResult:
        """
        Handle OAuth callback after user authorization.
        
        Exchanges authorization code for access/refresh tokens,
        creates or updates the connection record.
        
        Args:
            code: OAuth authorization code
            state: OAuth state parameter (contains connection_id)
            
        Returns:
            AuthResult indicating success/failure of token exchange
        """
        pass
    
    @abstractmethod
    def sync_usage(self, connection_id: int) -> SyncResult:
        """
        Fetch and persist usage data from the connected utility.
        
        Retrieves historical usage data, parses it, and persists
        both raw data and normalized summary.
        
        Args:
            connection_id: ID of the UtilityConnection record
            
        Returns:
            SyncResult with import statistics
        """
        pass
    
    @abstractmethod
    def get_normalized_usage(self, connection_id: int) -> Optional[UsageSummary]:
        """
        Return normalized usage summary for the connection.
        
        Args:
            connection_id: ID of the UtilityConnection record
            
        Returns:
            UsageSummary or None if no data available
        """
        pass
    
    @abstractmethod
    def revoke(self, connection_id: int) -> bool:
        """
        Revoke authorization and clean up.
        
        Calls provider revocation endpoint (if available),
        marks connection as revoked, clears tokens.
        
        Args:
            connection_id: ID of the UtilityConnection record
            
        Returns:
            True if revocation successful
        """
        pass
    
    def is_available(self) -> bool:
        """
        Check if provider is currently available/configured.
        
        Override in subclasses to check for required credentials.
        
        Returns:
            True if provider can accept connections
        """
        return True
    
    def supports_utility(self, utility_name: str) -> bool:
        """
        Check if this provider supports the given utility.
        
        Args:
            utility_name: Name of the utility (e.g., 'SDGE')
            
        Returns:
            True if provider supports this utility
        """
        if '*' in self.supported_utilities:
            return True
        return utility_name.upper() in [u.upper() for u in self.supported_utilities]
    
    def get_info(self) -> Dict[str, Any]:
        """
        Get provider information for API responses.
        
        Returns:
            Dictionary with provider metadata
        """
        return {
            "name": self.provider_name,
            "type": self.provider_type.value,
            "display_name": self.display_name,
            "utilities": self.supported_utilities,
            "available": self.is_available(),
        }


# Export all public classes
__all__ = [
    "ProviderType",
    "ConnectionStatus",
    "DataScope",
    "AuthResult",
    "SyncResult",
    "UsageSummary",
    "UtilityProvider",
]
