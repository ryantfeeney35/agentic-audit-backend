"""Service catalog loading and Pydantic models.

Provides structured access to the company's service catalog with validation.
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Default path to catalog file; can be overridden via SERVICE_CATALOG_PATH env var
DEFAULT_CATALOG_PATH = Path(__file__).parent.parent.parent / "data" / "service_catalog.json"


class ServiceEntry(BaseModel):
    """A single service offered by the company."""

    id: str = Field(..., description="Unique identifier for the service (e.g., 'hvac-unit-repair')")
    category: str = Field(..., description="Top-level category (e.g., 'Home Efficiency', 'HVAC', 'Electrical')")
    sub_category: str = Field(..., description="Sub-category (e.g., 'Insulation', 'Unit', 'Solar')")
    sub_category_detail: Optional[str] = Field(
        default=None, description="Specific detail (e.g., 'Attic', 'Water Heater')"
    )
    action: str = Field(..., description="Action type (e.g., 'Install', 'Replace', 'Repair', 'Optimization')")
    order_of_completion: int = Field(
        ..., ge=1, le=10, description="Priority order for work sequencing (lower = do first)"
    )
    estimated_cost_usd: Optional[float] = Field(default=None, description="Estimated cost in USD")
    estimated_kwh_savings: Optional[float] = Field(default=None, description="Estimated annual kWh savings")
    rebate_eligible: bool = Field(default=False, description="Whether this service qualifies for rebates")
    keywords: List[str] = Field(
        default_factory=list,
        description="Keywords for matching recommendation text to this service",
    )

    @property
    def display_name(self) -> str:
        """Human-readable service name."""
        parts = [self.sub_category]
        if self.sub_category_detail:
            parts.append(self.sub_category_detail)
        parts.append(self.action)
        return " - ".join(parts)

    @property
    def full_path(self) -> str:
        """Full hierarchical path for the service."""
        parts = [self.category, self.sub_category]
        if self.sub_category_detail:
            parts.append(self.sub_category_detail)
        parts.append(self.action)
        return " > ".join(parts)


class ServiceCatalog(BaseModel):
    """Container for all services in the catalog."""

    version: str = Field(default="1.0", description="Catalog schema version")
    last_updated: Optional[str] = Field(default=None, description="ISO date of last catalog update")
    services: List[ServiceEntry] = Field(default_factory=list, description="List of all offered services")

    def get_service_by_id(self, service_id: str) -> Optional[ServiceEntry]:
        """Look up a service by its unique ID."""
        for svc in self.services:
            if svc.id == service_id:
                return svc
        return None

    def get_services_by_category(self, category: str) -> List[ServiceEntry]:
        """Get all services in a top-level category (case-insensitive)."""
        cat_lower = category.lower()
        return [s for s in self.services if s.category.lower() == cat_lower]

    def get_services_by_domain(self, domain: str) -> List[ServiceEntry]:
        """Get services relevant to an agent domain.
        
        Maps agent domains (insulation, hvac, interior, etc.) to catalog categories/sub-categories.
        """
        domain_lower = domain.lower()
        
        # Domain to category/sub-category mapping
        domain_mappings = {
            "insulation": lambda s: s.sub_category.lower() == "insulation" or s.sub_category.lower() == "air sealing",
            "hvac": lambda s: s.category.lower() == "hvac",
            "siding": lambda s: s.sub_category.lower() == "alignment",  # exterior doors
            "exterior": lambda s: s.sub_category.lower() in ("alignment", "air sealing"),
            "interior": lambda s: s.sub_category.lower() in ("lighting", "appliances"),
            "electrical": lambda s: s.category.lower() == "electrical",
            "roof": lambda s: s.sub_category.lower() == "solar",  # solar is roof-related
        }
        
        filter_fn = domain_mappings.get(domain_lower)
        if filter_fn:
            return [s for s in self.services if filter_fn(s)]
        
        # Fallback: return all services if domain not mapped
        return list(self.services)

    def get_all_keywords(self) -> List[str]:
        """Get a flat list of all keywords across all services."""
        keywords = []
        for svc in self.services:
            keywords.extend(svc.keywords)
        return keywords

    def to_taxonomy_text(self, domain: Optional[str] = None) -> str:
        """Generate a concise text representation for LLM prompts.
        
        Args:
            domain: Optional domain to filter services (e.g., 'insulation', 'hvac')
        
        Returns:
            A formatted string listing available services.
        """
        services = self.get_services_by_domain(domain) if domain else self.services
        
        if not services:
            return "No services available for this domain."
        
        lines = []
        current_category = None
        
        # Sort by category, then sub_category, then action
        sorted_services = sorted(
            services,
            key=lambda s: (s.category, s.sub_category, s.sub_category_detail or "", s.action),
        )
        
        for svc in sorted_services:
            if svc.category != current_category:
                if current_category is not None:
                    lines.append("")  # blank line between categories
                current_category = svc.category
                lines.append(f"**{svc.category}**")
            
            detail = f" ({svc.sub_category_detail})" if svc.sub_category_detail else ""
            lines.append(f"  - {svc.sub_category}{detail}: {svc.action}")
        
        return "\n".join(lines)


def load_catalog(path: Optional[str] = None) -> ServiceCatalog:
    """Load the service catalog from JSON file.
    
    Args:
        path: Optional path to catalog file. If not provided, uses SERVICE_CATALOG_PATH
              env var or default location.
    
    Returns:
        Validated ServiceCatalog instance.
    
    Raises:
        FileNotFoundError: If catalog file doesn't exist.
        ValidationError: If catalog JSON doesn't match schema.
    """
    if path is None:
        path = os.environ.get("SERVICE_CATALOG_PATH", str(DEFAULT_CATALOG_PATH))
    
    catalog_path = Path(path)
    
    if not catalog_path.exists():
        logger.error("Service catalog not found at %s", catalog_path)
        raise FileNotFoundError(f"Service catalog not found: {catalog_path}")
    
    logger.debug("Loading service catalog from %s", catalog_path)
    
    with open(catalog_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    catalog = ServiceCatalog.model_validate(data)
    logger.info("Loaded service catalog: %d services", len(catalog.services))
    
    return catalog


@lru_cache(maxsize=1)
def get_catalog() -> ServiceCatalog:
    """Get the cached service catalog singleton.
    
    Uses LRU cache to avoid reloading on every call. Call get_catalog.cache_clear()
    to force reload after catalog file changes.
    """
    return load_catalog()


def reload_catalog() -> ServiceCatalog:
    """Force reload the service catalog, clearing the cache."""
    get_catalog.cache_clear()
    return get_catalog()
