# backend/utils/providers/registry.py
"""
Provider Registry with Waterfall Fallback Support

Manages utility data providers and implements the waterfall pattern:
- For SDG&E: Try CMD first, then UtilityAPI, then manual entry
- For other utilities: Try UtilityAPI first, then manual entry

The registry provides a single point of access for all provider operations.
"""

from typing import List, Optional, Dict, Any
import logging

from . import (
    UtilityProvider,
    AuthResult,
    SyncResult,
    UsageSummary,
    ProviderType,
)

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """
    Registry for utility providers with waterfall fallback support.
    
    Manages provider instances and implements fallback logic when
    a preferred provider fails or is unavailable.
    """
    
    # Waterfall chains by utility name
    # Order matters: first available provider that succeeds is used
    WATERFALL_CHAINS: Dict[str, List[str]] = {
        "SDGE": ["sdge_cmd", "utilityapi", "manual"],
        "PGE": ["utilityapi", "manual"],
        "SCE": ["utilityapi", "manual"],
        "LADWP": ["utilityapi", "manual"],
        "*": ["utilityapi", "manual"],  # Default for unknown utilities
    }
    
    # Supported utilities for UI display
    SUPPORTED_UTILITIES = [
        {"name": "SDGE", "display_name": "San Diego Gas & Electric", "direct_cmd": True},
        {"name": "PGE", "display_name": "Pacific Gas & Electric", "direct_cmd": False},
        {"name": "SCE", "display_name": "Southern California Edison", "direct_cmd": False},
        {"name": "LADWP", "display_name": "Los Angeles DWP", "direct_cmd": False},
    ]
    
    def __init__(self):
        """Initialize registry with available providers."""
        self._providers: Dict[str, UtilityProvider] = {}
        self._initialize_providers()
    
    def _initialize_providers(self):
        """
        Initialize all available providers.
        
        Providers are imported lazily to avoid circular imports
        and to handle missing dependencies gracefully.
        """
        # SDG&E Connect My Data (direct integration)
        try:
            from .sdge_cmd import SDGECMDProvider
            self._providers["sdge_cmd"] = SDGECMDProvider()
            logger.info("SDG&E CMD provider initialized")
        except ImportError as e:
            logger.warning(f"SDG&E CMD provider not available: {e}")
        except Exception as e:
            logger.error(f"Failed to initialize SDG&E CMD provider: {e}")
        
        # UtilityAPI aggregator (fallback)
        try:
            from .utilityapi import UtilityAPIProvider
            self._providers["utilityapi"] = UtilityAPIProvider()
            logger.info("UtilityAPI provider initialized")
        except ImportError as e:
            logger.warning(f"UtilityAPI provider not available: {e}")
        except Exception as e:
            logger.error(f"Failed to initialize UtilityAPI provider: {e}")
        
        # Manual entry (always available)
        try:
            from .manual import ManualEntryProvider
            self._providers["manual"] = ManualEntryProvider()
            logger.info("Manual entry provider initialized")
        except ImportError as e:
            logger.warning(f"Manual entry provider not available: {e}")
        except Exception as e:
            logger.error(f"Failed to initialize manual entry provider: {e}")
    
    def get_provider(self, provider_name: str) -> Optional[UtilityProvider]:
        """
        Get a specific provider by name.
        
        Args:
            provider_name: Name of the provider (e.g., 'sdge_cmd', 'utilityapi')
            
        Returns:
            UtilityProvider instance or None if not found
        """
        return self._providers.get(provider_name)
    
    def get_fallback_chain(self, utility_name: str) -> List[UtilityProvider]:
        """
        Get ordered list of providers to try for a utility.
        
        Args:
            utility_name: Name of the utility (e.g., 'SDGE')
            
        Returns:
            List of UtilityProvider instances in fallback order
        """
        utility_upper = utility_name.upper()
        chain_names = self.WATERFALL_CHAINS.get(
            utility_upper, 
            self.WATERFALL_CHAINS["*"]
        )
        
        providers = []
        for name in chain_names:
            provider = self._providers.get(name)
            if provider:
                providers.append(provider)
        
        return providers
    
    def connect(
        self, 
        audit_id: int, 
        user_id: str, 
        utility_name: str, 
        data_scope: str = 'electric',
        preferred_provider: Optional[str] = None,
    ) -> AuthResult:
        """
        Connect to a utility, using waterfall fallback if needed.
        
        If preferred_provider is specified, only that provider is tried.
        Otherwise, providers are tried in waterfall order until one succeeds.
        
        Args:
            audit_id: ID of the audit
            user_id: ID of the user
            utility_name: Name of the utility (e.g., 'SDGE')
            data_scope: Type of data ('electric', 'gas', 'both')
            preferred_provider: Optional specific provider to use
            
        Returns:
            AuthResult from the first successful provider
        """
        # If a specific provider is requested, use only that one
        if preferred_provider:
            provider = self.get_provider(preferred_provider)
            if not provider:
                return AuthResult(
                    success=False,
                    error=f"Provider '{preferred_provider}' not found"
                )
            if not provider.is_available():
                return AuthResult(
                    success=False,
                    error=f"Provider '{preferred_provider}' is not available"
                )
            if not provider.supports_utility(utility_name):
                return AuthResult(
                    success=False,
                    error=f"Provider '{preferred_provider}' does not support {utility_name}"
                )
            
            result = provider.connect(audit_id, user_id, utility_name, data_scope)
            result.provider_name = provider.provider_name
            return result
        
        # Try providers in waterfall order
        chain = self.get_fallback_chain(utility_name)
        last_error = None
        
        for provider in chain:
            if not provider.is_available():
                logger.debug(f"Provider {provider.provider_name} not available, skipping")
                continue
            
            if not provider.supports_utility(utility_name):
                logger.debug(f"Provider {provider.provider_name} doesn't support {utility_name}, skipping")
                continue
            
            logger.info(f"Trying provider {provider.provider_name} for {utility_name}")
            
            try:
                result = provider.connect(audit_id, user_id, utility_name, data_scope)
                result.provider_name = provider.provider_name
                
                if result.success:
                    logger.info(f"Connection initiated via {provider.provider_name}")
                    return result
                
                last_error = result.error
                logger.warning(
                    f"Provider {provider.provider_name} failed: {result.error}"
                )
            except Exception as e:
                last_error = str(e)
                logger.exception(f"Provider {provider.provider_name} raised exception")
        
        return AuthResult(
            success=False,
            error=f"All providers failed. Last error: {last_error}"
        )
    
    def handle_callback(self, code: str, state: str) -> AuthResult:
        """
        Route OAuth callback to the appropriate provider.
        
        The state parameter contains the connection_id which can be used
        to look up which provider initiated the flow.
        
        Args:
            code: OAuth authorization code
            state: OAuth state parameter
            
        Returns:
            AuthResult from the provider
        """
        # Import here to avoid circular imports
        from models import UtilityConnection
        
        # Extract connection_id from state
        try:
            # State format: "{random_token}:{connection_id}"
            _, conn_id_str = state.rsplit(":", 1)
            conn_id = int(conn_id_str)
        except (ValueError, AttributeError):
            return AuthResult(
                success=False,
                error="Invalid state parameter format"
            )
        
        # Look up the connection to find the provider
        conn = UtilityConnection.query.get(conn_id)
        if not conn:
            return AuthResult(
                success=False,
                error=f"Connection {conn_id} not found"
            )
        
        provider = self.get_provider(conn.provider_name)
        if not provider:
            return AuthResult(
                success=False,
                error=f"Provider {conn.provider_name} not found"
            )
        
        return provider.handle_callback(code, state)
    
    def sync_usage(self, connection_id: int) -> SyncResult:
        """
        Sync usage data for a connection.
        
        Delegates to the appropriate provider based on the connection record.
        
        Args:
            connection_id: ID of the UtilityConnection
            
        Returns:
            SyncResult with import statistics
        """
        from models import UtilityConnection
        
        conn = UtilityConnection.query.get(connection_id)
        if not conn:
            return SyncResult(
                success=False,
                error=f"Connection {connection_id} not found"
            )
        
        provider = self.get_provider(conn.provider_name)
        if not provider:
            return SyncResult(
                success=False,
                error=f"Provider {conn.provider_name} not found"
            )
        
        return provider.sync_usage(connection_id)
    
    def get_normalized_usage(self, connection_id: int) -> Optional[UsageSummary]:
        """
        Get normalized usage summary for a connection.
        
        Args:
            connection_id: ID of the UtilityConnection
            
        Returns:
            UsageSummary or None
        """
        from models import UtilityConnection
        
        conn = UtilityConnection.query.get(connection_id)
        if not conn:
            return None
        
        provider = self.get_provider(conn.provider_name)
        if not provider:
            return None
        
        return provider.get_normalized_usage(connection_id)
    
    def revoke(self, connection_id: int) -> bool:
        """
        Revoke a utility connection.
        
        Args:
            connection_id: ID of the UtilityConnection
            
        Returns:
            True if revocation successful
        """
        from models import UtilityConnection
        
        conn = UtilityConnection.query.get(connection_id)
        if not conn:
            return False
        
        provider = self.get_provider(conn.provider_name)
        if not provider:
            return False
        
        return provider.revoke(connection_id)
    
    def get_available_providers(self, utility_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get list of available providers.
        
        Args:
            utility_name: Optional filter by utility support
            
        Returns:
            List of provider info dictionaries
        """
        result = []
        for provider in self._providers.values():
            if not provider.is_available():
                continue
            if utility_name and not provider.supports_utility(utility_name):
                continue
            result.append(provider.get_info())
        return result
    
    def get_waterfall_chains(self) -> Dict[str, List[str]]:
        """
        Get waterfall chain configuration.
        
        Returns:
            Dictionary mapping utility names to provider chains
        """
        return self.WATERFALL_CHAINS.copy()
    
    def get_supported_utilities(self) -> List[Dict[str, Any]]:
        """
        Get list of supported utilities for UI display.
        
        Returns:
            List of utility info dictionaries
        """
        return self.SUPPORTED_UTILITIES.copy()


# Global registry instance (lazy initialization)
_registry: Optional[ProviderRegistry] = None


def get_registry() -> ProviderRegistry:
    """
    Get the global provider registry instance.
    
    Creates the registry on first access (lazy initialization).
    
    Returns:
        ProviderRegistry singleton instance
    """
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry


# Export convenience functions
__all__ = [
    "ProviderRegistry",
    "get_registry",
]
