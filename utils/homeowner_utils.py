# backend/utils/homeowner_utils.py
"""
Utility functions for homeowner portal authentication.
These functions are extracted to allow easy testing without Flask dependencies.
"""
import re
import jwt
import os
from datetime import datetime, timedelta, timezone
import logging


# JWT configuration for homeowner tokens
HOMEOWNER_JWT_SECRET = os.environ.get('HOMEOWNER_JWT_SECRET', os.environ.get('SECRET_KEY', 'dev-secret-change-me'))
HOMEOWNER_JWT_ALGORITHM = 'HS256'
HOMEOWNER_JWT_EXPIRY_HOURS = 24


def normalize_phone(phone: str) -> str:
    """
    Normalize phone number to E.164 format (US numbers only for now).
    
    Examples:
        '(555) 123-4567' -> '+15551234567'
        '555.123.4567' -> '+15551234567'
        '+1-555-123-4567' -> '+15551234567'
        '5551234567' -> '+15551234567'
    
    Returns:
        Normalized phone number in E.164 format, or empty string if invalid.
    """
    if not phone:
        return ''
    
    # Remove all non-digit characters
    digits = re.sub(r'\D', '', phone)
    
    # Handle different lengths
    if len(digits) == 10:
        # US number without country code
        return f'+1{digits}'
    elif len(digits) == 11 and digits.startswith('1'):
        # US number with country code
        return f'+{digits}'
    elif len(digits) >= 10 and len(digits) <= 15:
        # International number - assume it's already in proper format
        return f'+{digits}'
    else:
        # Invalid phone number
        return ''


def validate_phone(phone: str) -> bool:
    """
    Validate that a phone number can be normalized to E.164 format.
    """
    return bool(normalize_phone(phone))


def generate_homeowner_token(property_id: int, phone_number: str) -> str:
    """
    Generate a JWT token for homeowner authentication.
    
    The token contains:
    - property_id: The ID of the matched property
    - phone_number: The normalized phone number
    - exp: Expiration timestamp
    - iat: Issued at timestamp
    - type: 'homeowner' to distinguish from auditor tokens
    """
    now = datetime.now(timezone.utc)
    payload = {
        'property_id': property_id,
        'phone_number': phone_number,
        'exp': now + timedelta(hours=HOMEOWNER_JWT_EXPIRY_HOURS),
        'iat': now,
        'type': 'homeowner'
    }
    return jwt.encode(payload, HOMEOWNER_JWT_SECRET, algorithm=HOMEOWNER_JWT_ALGORITHM)


def decode_homeowner_token(token: str) -> dict | None:
    """
    Decode and validate a homeowner JWT token.
    
    Returns:
        Decoded payload dict if valid, None if invalid or expired.
    """
    try:
        payload = jwt.decode(token, HOMEOWNER_JWT_SECRET, algorithms=[HOMEOWNER_JWT_ALGORITHM])
        if payload.get('type') != 'homeowner':
            return None
        return payload
    except jwt.ExpiredSignatureError:
        logging.warning("Homeowner token expired")
        return None
    except jwt.InvalidTokenError as e:
        logging.warning(f"Invalid homeowner token: {e}")
        return None
