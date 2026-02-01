"""Prompt helpers for injecting service catalog into LLM prompts.

Generates concise, LLM-friendly text representations of the service catalog
to guide recommendation generation toward valid services.
"""

from __future__ import annotations

import logging
from typing import Optional

from .catalog import get_catalog

logger = logging.getLogger(__name__)


def get_service_taxonomy_prompt(domain: Optional[str] = None) -> str:
    """Generate a prompt snippet listing available services for LLM guidance.
    
    Args:
        domain: Optional domain to filter services (e.g., 'insulation', 'hvac').
                If None, includes all services.
    
    Returns:
        A formatted string suitable for inclusion in LLM system prompts.
    """
    try:
        catalog = get_catalog()
    except Exception as e:
        logger.warning("Failed to load service catalog for prompt: %s", e)
        return ""
    
    services = catalog.get_services_by_domain(domain) if domain else catalog.services
    
    if not services:
        return ""
    
    # Build a concise list grouped by category
    lines = [
        "ALLOWED SERVICES (you MUST only recommend from this list):",
        "",
    ]
    
    current_category = None
    for svc in sorted(services, key=lambda s: (s.category, s.sub_category, s.action)):
        if svc.category != current_category:
            current_category = svc.category
            lines.append(f"[{svc.category}]")
        
        # Format: "- Insulation (Attic): Install, Replace"
        detail = f" ({svc.sub_category_detail})" if svc.sub_category_detail else ""
        lines.append(f"  • {svc.sub_category}{detail}: {svc.action}")
    
    lines.append("")
    lines.append("If a recommendation does not fit any of the above services, DO NOT include it.")
    
    return "\n".join(lines)


def get_domain_service_list(domain: str) -> list[str]:
    """Get a simple list of service descriptions for a domain.
    
    Useful for validation or display purposes.
    
    Args:
        domain: The agent domain (e.g., 'insulation', 'hvac').
    
    Returns:
        List of human-readable service descriptions.
    """
    try:
        catalog = get_catalog()
    except Exception:
        return []
    
    services = catalog.get_services_by_domain(domain)
    
    result = []
    for svc in services:
        detail = f" ({svc.sub_category_detail})" if svc.sub_category_detail else ""
        result.append(f"{svc.sub_category}{detail}: {svc.action}")
    
    return result


def get_full_taxonomy_prompt() -> str:
    """Generate the complete service taxonomy for all domains.
    
    Use this when the orchestrator needs visibility into all available services.
    
    Returns:
        Complete taxonomy string for all services.
    """
    return get_service_taxonomy_prompt(domain=None)
