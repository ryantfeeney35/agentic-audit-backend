# backend/utils/encryption.py
"""
Encryption Utilities for Secure Token Storage

Provides AES-256-GCM encryption for storing OAuth tokens and other
sensitive data in the database.

Uses Fernet from the cryptography library, which provides:
- AES-256-CBC encryption
- HMAC-SHA256 authentication
- Automatic key derivation with PBKDF2

Environment variable required:
- TOKEN_ENCRYPTION_KEY: Base64-encoded 32-byte key (use Fernet.generate_key())
"""

import os
import base64
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy-loaded encryption key
_fernet = None


def _get_fernet():
    """
    Get the Fernet instance for encryption/decryption.
    
    Lazy-loads the encryption key from environment variable.
    
    Returns:
        Fernet instance or None if key not configured
    """
    global _fernet
    
    if _fernet is not None:
        return _fernet
    
    key = os.environ.get("TOKEN_ENCRYPTION_KEY")
    
    if not key:
        logger.warning(
            "TOKEN_ENCRYPTION_KEY not set. Token encryption disabled. "
            "Generate a key with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
        return None
    
    try:
        from cryptography.fernet import Fernet
        _fernet = Fernet(key.encode())
        logger.info("Token encryption initialized")
        return _fernet
    except ImportError:
        logger.warning("cryptography library not installed. Token encryption disabled.")
        return None
    except Exception as e:
        logger.error(f"Failed to initialize encryption: {e}")
        return None


def encrypt_token(token: str) -> str:
    """
    Encrypt a token for secure database storage.
    
    If encryption is not available (no key or missing library),
    returns the token as-is with a warning prefix.
    
    Args:
        token: Plain text token to encrypt
        
    Returns:
        Encrypted token string (base64-encoded)
    """
    if not token:
        return token
    
    fernet = _get_fernet()
    
    if fernet is None:
        # Return with prefix so we know it's unencrypted
        logger.warning("Storing token without encryption")
        return f"UNENC:{token}"
    
    try:
        encrypted = fernet.encrypt(token.encode())
        return encrypted.decode()
    except Exception as e:
        logger.error(f"Token encryption failed: {e}")
        return f"UNENC:{token}"


def decrypt_token(encrypted_token: str) -> Optional[str]:
    """
    Decrypt a token from database storage.
    
    Handles both encrypted tokens and legacy unencrypted tokens
    (prefixed with 'UNENC:').
    
    Args:
        encrypted_token: Encrypted token string from database
        
    Returns:
        Decrypted plain text token, or None if decryption fails
    """
    if not encrypted_token:
        return None
    
    # Handle unencrypted legacy tokens
    if encrypted_token.startswith("UNENC:"):
        logger.warning("Reading unencrypted token from database")
        return encrypted_token[6:]  # Strip 'UNENC:' prefix
    
    fernet = _get_fernet()
    
    if fernet is None:
        logger.error("Cannot decrypt token: encryption not configured")
        return None
    
    try:
        decrypted = fernet.decrypt(encrypted_token.encode())
        return decrypted.decode()
    except Exception as e:
        logger.error(f"Token decryption failed: {e}")
        return None


def generate_encryption_key() -> str:
    """
    Generate a new encryption key for TOKEN_ENCRYPTION_KEY.
    
    Utility function for setup/deployment.
    
    Returns:
        Base64-encoded 32-byte key string
    """
    try:
        from cryptography.fernet import Fernet
        return Fernet.generate_key().decode()
    except ImportError:
        raise RuntimeError(
            "cryptography library required. Install with: pip install cryptography"
        )


def is_encryption_enabled() -> bool:
    """
    Check if token encryption is properly configured.
    
    Returns:
        True if encryption is available
    """
    return _get_fernet() is not None


def rotate_token(old_encrypted: str, new_key: Optional[str] = None) -> str:
    """
    Re-encrypt a token, optionally with a new key.
    
    Useful for key rotation scenarios.
    
    Args:
        old_encrypted: Currently encrypted token
        new_key: Optional new encryption key (if rotating keys)
        
    Returns:
        Newly encrypted token
    """
    # Decrypt with current key
    plain_token = decrypt_token(old_encrypted)
    if plain_token is None:
        raise ValueError("Failed to decrypt token for rotation")
    
    if new_key:
        # Use the new key for encryption
        try:
            from cryptography.fernet import Fernet
            new_fernet = Fernet(new_key.encode())
            encrypted = new_fernet.encrypt(plain_token.encode())
            return encrypted.decode()
        except Exception as e:
            raise ValueError(f"Failed to encrypt with new key: {e}")
    else:
        # Re-encrypt with current key (refreshes timestamp)
        return encrypt_token(plain_token)


# Export
__all__ = [
    "encrypt_token",
    "decrypt_token",
    "generate_encryption_key",
    "is_encryption_enabled",
    "rotate_token",
]
