"""Deterministic constants for ROI calculators.

These values are intentionally simplified for predictable, testable outputs.
"""

from __future__ import annotations

# Default analysis horizon for upgrades when caller does not specify.
LIFESPAN_YEARS_DEFAULT: int = 25

# Climate multipliers used to scale savings for different regions.
CLIMATE_MULTIPLIER: dict[str, float] = {
    "mild": 1.00,
    "moderate": 1.20,
    "cold": 1.40,
    "very_cold": 1.60,
    "hot": 1.15,
}

# Baseline energy saved per sqft per point of R-value improvement.
# Example parity: RΔ=25 -> 25 * 0.018 = 0.45 kWh/sqft/year.
KWH_SAVED_PER_SQFT_PER_R_DELTA_BASE: float = 0.018
