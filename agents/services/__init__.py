"""Service catalog module for recommendation alignment.

This module provides functionality to:
- Load and validate the company's service catalog
- Match AI-generated recommendations to catalog services
- Filter recommendations to only include offered services
"""

from .catalog import ServiceEntry, ServiceCatalog, load_catalog, get_catalog, reload_catalog
from .matcher import match_recommendation_to_service, find_best_service_match
from .filter import filter_and_enrich_recommendations, enrich_recommendation_with_service
from .prompt_helpers import get_service_taxonomy_prompt, get_domain_service_list

__all__ = [
    "ServiceEntry",
    "ServiceCatalog",
    "load_catalog",
    "get_catalog",
    "reload_catalog",
    "match_recommendation_to_service",
    "find_best_service_match",
    "filter_and_enrich_recommendations",
    "enrich_recommendation_with_service",
    "get_service_taxonomy_prompt",
    "get_domain_service_list",
]
