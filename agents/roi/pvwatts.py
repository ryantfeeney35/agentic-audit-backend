"""
PVWatts API Integration for Location-Specific Solar Production Profiles.

Uses NREL PVWatts v8 API to fetch hourly AC production estimates based on
TMY3 weather data. Profiles are cached by zip code to minimize API calls.

API Documentation: https://developer.nrel.gov/docs/solar/pvwatts/v8/
Rate Limit: 1000 requests/hour per API key
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import requests

logger = logging.getLogger(__name__)

# PVWatts API endpoint
PVWATTS_API_URL = "https://developer.nrel.gov/api/pvwatts/v8.json"

# Default system assumptions (San Diego residential)
DEFAULT_TILT = 20  # degrees (typical roof pitch)
DEFAULT_AZIMUTH = 180  # south-facing
DEFAULT_MODULE_TYPE = 0  # standard module
DEFAULT_LOSSES = 14  # typical system losses (%)
DEFAULT_ARRAY_TYPE = 1  # fixed roof mount

# Cache expiration (30 days)
CACHE_EXPIRATION_DAYS = 30

# Valid production range for continental US (kWh/kW/year)
MIN_ANNUAL_PRODUCTION = 1400
MAX_ANNUAL_PRODUCTION = 2000


def _get_params_hash(
    zip_code: str,
    tilt: float,
    azimuth: float,
    module_type: int = DEFAULT_MODULE_TYPE,
    losses: float = DEFAULT_LOSSES,
) -> str:
    """Generate a unique hash for the parameter set."""
    params_str = f"{zip_code}|{tilt}|{azimuth}|{module_type}|{losses}"
    return hashlib.md5(params_str.encode()).hexdigest()


def get_hourly_profile(
    zip_code: str,
    tilt: float = DEFAULT_TILT,
    azimuth: float = DEFAULT_AZIMUTH,
    api_key: Optional[str] = None,
    db_session=None,
) -> Tuple[Optional[np.ndarray], Optional[str]]:
    """
    Fetch hourly AC production profile from PVWatts API.
    
    Returns normalized profile (kWh per kW installed) for 8760 hours.
    Uses caching to minimize API calls.
    
    Args:
        zip_code: 5-digit US zip code
        tilt: Panel tilt angle in degrees (default: 20)
        azimuth: Panel azimuth in degrees (180 = south)
        api_key: NREL API key (or from PVWATTS_API_KEY env var)
        db_session: Optional SQLAlchemy session for caching
        
    Returns:
        Tuple of (hourly_profile as np.array, error_message)
        On success: (8760-element array, None)
        On failure: (None, error description)
    """
    # Try cache first
    cached = _get_cached_profile(zip_code, tilt, azimuth, db_session)
    if cached is not None:
        logger.info(f"PVWatts cache hit for zip {zip_code}")
        return cached, None
    
    # Get API key
    api_key = api_key or os.environ.get("PVWATTS_API_KEY", "DEMO_KEY")
    
    # Build request parameters
    params = {
        "api_key": api_key,
        "system_capacity": 1,  # 1 kW for normalized profile
        "module_type": DEFAULT_MODULE_TYPE,
        "losses": DEFAULT_LOSSES,
        "array_type": DEFAULT_ARRAY_TYPE,
        "tilt": tilt,
        "azimuth": azimuth,
        "address": zip_code,
        "timeframe": "hourly",
    }
    
    try:
        logger.info(f"Fetching PVWatts profile for zip {zip_code}")
        response = requests.get(PVWATTS_API_URL, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        # Check for API errors
        if "errors" in data and data["errors"]:
            error_msg = "; ".join(data["errors"])
            logger.error(f"PVWatts API error: {error_msg}")
            # Try fallback to cached
            fallback = _get_any_cached_profile(db_session)
            if fallback is not None:
                logger.warning(f"Using fallback cached profile due to API error")
                return fallback, None
            return None, f"PVWatts API error: {error_msg}"
        
        # Extract hourly AC output
        outputs = data.get("outputs", {})
        hourly_ac = outputs.get("ac", [])
        
        if len(hourly_ac) != 8760:
            logger.error(f"PVWatts returned {len(hourly_ac)} hours, expected 8760")
            return None, f"Invalid response: expected 8760 hours, got {len(hourly_ac)}"
        
        # Convert Wh to kWh (PVWatts returns Wh for 1 kW system)
        hourly_profile = np.array(hourly_ac) / 1000.0
        
        # Validate annual production
        annual_total = float(np.sum(hourly_profile))
        if annual_total < MIN_ANNUAL_PRODUCTION or annual_total > MAX_ANNUAL_PRODUCTION:
            logger.warning(
                f"Unusual annual production {annual_total:.0f} kWh/kW for zip {zip_code}"
            )
        
        # Cache the result
        _store_cached_profile(zip_code, tilt, azimuth, hourly_profile, db_session)
        
        logger.info(
            f"PVWatts profile fetched for zip {zip_code}: "
            f"{annual_total:.0f} kWh/kW/year"
        )
        return hourly_profile, None
        
    except requests.exceptions.Timeout:
        logger.error(f"PVWatts API timeout for zip {zip_code}")
        fallback = _get_any_cached_profile(db_session)
        if fallback is not None:
            return fallback, None
        return None, "PVWatts API timeout"
        
    except requests.exceptions.RequestException as e:
        logger.error(f"PVWatts API request failed: {e}")
        fallback = _get_any_cached_profile(db_session)
        if fallback is not None:
            return fallback, None
        return None, f"Unable to fetch solar production data: {e}"


def interpolate_to_15min(hourly_profile: np.ndarray) -> np.ndarray:
    """
    Interpolate 8760 hourly values to 35040 fifteen-minute intervals.
    
    Uses step interpolation (each hourly value repeated 4 times) to
    preserve total energy. This is more appropriate for solar production
    than linear interpolation since we're dealing with energy bins.
    
    Args:
        hourly_profile: Array of 8760 hourly kWh values
        
    Returns:
        Array of 35040 fifteen-minute kWh values (each = hourly/4)
    """
    if len(hourly_profile) != 8760:
        raise ValueError(f"Expected 8760 hourly values, got {len(hourly_profile)}")
    
    # Repeat each hourly value 4 times, dividing energy by 4
    # This preserves total energy: sum(15min) = sum(hourly)
    fifteen_min = np.repeat(hourly_profile / 4.0, 4)
    
    return fifteen_min


def scale_profile_to_system_size(
    profile: np.ndarray, system_size_kw: float
) -> np.ndarray:
    """
    Scale normalized profile to actual system size.
    
    Args:
        profile: Normalized production profile (per kW)
        system_size_kw: System size in kW
        
    Returns:
        Scaled profile in kWh
    """
    return profile * system_size_kw


def _get_cached_profile(
    zip_code: str,
    tilt: float,
    azimuth: float, 
    db_session,
) -> Optional[np.ndarray]:
    """Retrieve cached profile from database if available and not expired."""
    if db_session is None:
        return None
    
    try:
        from models import PVWattsCache
        
        params_hash = _get_params_hash(zip_code, tilt, azimuth)
        expiry_date = datetime.utcnow() - timedelta(days=CACHE_EXPIRATION_DAYS)
        
        cached = db_session.query(PVWattsCache).filter(
            PVWattsCache.zip_code == zip_code,
            PVWattsCache.params_hash == params_hash,
            PVWattsCache.fetched_at > expiry_date,
        ).first()
        
        if cached:
            return np.array(json.loads(cached.hourly_profile))
        return None
        
    except Exception as e:
        logger.warning(f"Cache lookup failed: {e}")
        return None


def _get_any_cached_profile(db_session) -> Optional[np.ndarray]:
    """Get any cached profile as fallback (for same region)."""
    if db_session is None:
        return None
    
    try:
        from models import PVWattsCache
        
        # Get most recent cached profile
        cached = db_session.query(PVWattsCache).order_by(
            PVWattsCache.fetched_at.desc()
        ).first()
        
        if cached:
            logger.info(f"Using fallback profile from zip {cached.zip_code}")
            return np.array(json.loads(cached.hourly_profile))
        return None
        
    except Exception as e:
        logger.warning(f"Fallback cache lookup failed: {e}")
        return None


def _store_cached_profile(
    zip_code: str,
    tilt: float,
    azimuth: float,
    hourly_profile: np.ndarray,
    db_session,
) -> None:
    """Store profile in database cache."""
    if db_session is None:
        return
    
    try:
        from models import PVWattsCache
        
        params_hash = _get_params_hash(zip_code, tilt, azimuth)
        
        # Upsert logic
        existing = db_session.query(PVWattsCache).filter(
            PVWattsCache.zip_code == zip_code,
            PVWattsCache.params_hash == params_hash,
        ).first()
        
        profile_json = json.dumps(hourly_profile.tolist())
        
        if existing:
            existing.hourly_profile = profile_json
            existing.fetched_at = datetime.utcnow()
        else:
            cache_entry = PVWattsCache(
                zip_code=zip_code,
                params_hash=params_hash,
                hourly_profile=profile_json,
                fetched_at=datetime.utcnow(),
            )
            db_session.add(cache_entry)
        
        db_session.commit()
        logger.info(f"Cached PVWatts profile for zip {zip_code}")
        
    except Exception as e:
        logger.warning(f"Failed to cache profile: {e}")
        db_session.rollback()
