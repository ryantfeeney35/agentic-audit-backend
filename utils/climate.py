import json
import os
from functools import lru_cache


@lru_cache(maxsize=1)
def _load_mapping():
    """Load the ZIP-to-Climate-Zone mapping from the data file once and cache it."""
    here = os.path.dirname(os.path.abspath(__file__))
    data_path = os.path.join(here, '..', 'data', 'climate_us_zip_zones.json')
    data_path = os.path.normpath(data_path)
    try:
        with open(data_path, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def get_climate_zone(zip_code: str | int | None) -> str:
    """
    Return a short ASHRAE-style climate zone code like "3C" for a 5-digit ZIP, or "Unknown" if not found.
    Deterministic and offline—no network calls.
    """
    if not zip_code:
        return "Unknown"
    z = str(zip_code).strip()
    digits = ''.join(ch for ch in z if ch.isdigit())[:5]
    if len(digits) != 5:
        return "Unknown"
    mapping = _load_mapping()
    return mapping.get(digits, "Unknown")
