"""Post-processing filter for recommendation-to-catalog alignment.

Validates and enriches AI-generated recommendations with service catalog metadata.
Filters out recommendations that don't map to offered services.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from .catalog import get_catalog, ServiceEntry
from .matcher import match_recommendation_to_service, MatchResult

logger = logging.getLogger(__name__)

# Minimum confidence threshold to accept a match
DEFAULT_CONFIDENCE_THRESHOLD = 0.5


def filter_and_enrich_recommendations(
    recommendations: List[dict],
    domain: Optional[str] = None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    strict: bool = False,
) -> Tuple[List[dict], List[dict]]:
    """Filter recommendations to only include those matching the service catalog.
    
    Args:
        recommendations: List of recommendation dicts from agent output.
        domain: Optional domain for filtering (e.g., 'insulation', 'hvac').
        confidence_threshold: Minimum match confidence to accept (0.0-1.0).
        strict: If True, only include exact/keyword matches; reject fuzzy/heuristic.
    
    Returns:
        Tuple of (accepted_recommendations, rejected_recommendations).
        Accepted recommendations are enriched with service catalog metadata:
        - service_id: The matched service ID
        - order_of_completion: Priority for work sequencing
        - rebate_eligible: Whether the service qualifies for rebates
        - match_confidence: The confidence score of the match
    """
    if not recommendations:
        return [], []
    
    accepted = []
    rejected = []
    
    for rec in recommendations:
        summary = rec.get("summary", "")
        if not summary:
            logger.debug("Skipping recommendation with empty summary")
            rejected.append({**rec, "_rejection_reason": "empty_summary"})
            continue
        
        # Try to match the recommendation to a service
        result = match_recommendation_to_service(summary, domain=domain)
        
        # Apply filtering rules
        if result.service is None:
            logger.info("Rejecting unmatched recommendation: %s...", summary[:80])
            rejected.append({**rec, "_rejection_reason": "no_match"})
            continue
        
        if result.confidence < confidence_threshold:
            logger.info(
                "Rejecting low-confidence match (%.2f < %.2f): %s...",
                result.confidence,
                confidence_threshold,
                summary[:80],
            )
            rejected.append({
                **rec,
                "_rejection_reason": "low_confidence",
                "_match_confidence": result.confidence,
                "_matched_service": result.service.id,
            })
            continue
        
        if strict and result.match_type not in ("exact", "keyword"):
            logger.info(
                "Rejecting non-exact match in strict mode (%s): %s...",
                result.match_type,
                summary[:80],
            )
            rejected.append({
                **rec,
                "_rejection_reason": "non_exact_match",
                "_match_type": result.match_type,
                "_matched_service": result.service.id,
            })
            continue
        
        # Accept and enrich the recommendation
        enriched = {
            **rec,
            "service_id": result.service.id,
            "order_of_completion": result.service.order_of_completion,
            "rebate_eligible": result.service.rebate_eligible,
            "match_confidence": result.confidence,
            "_match_type": result.match_type,
        }
        
        logger.debug(
            "Accepted recommendation: %s -> %s (confidence=%.2f, type=%s)",
            summary[:50],
            result.service.id,
            result.confidence,
            result.match_type,
        )
        
        accepted.append(enriched)
    
    logger.info(
        "Filter results: %d accepted, %d rejected out of %d total",
        len(accepted),
        len(rejected),
        len(recommendations),
    )
    
    return accepted, rejected


def enrich_recommendation_with_service(
    rec: dict,
    domain: Optional[str] = None,
) -> dict:
    """Enrich a single recommendation with service catalog metadata.
    
    Unlike filter_and_enrich_recommendations, this function always returns
    the recommendation (possibly with no service match).
    
    Args:
        rec: A recommendation dict with at least a 'summary' field.
        domain: Optional domain for filtering.
    
    Returns:
        The recommendation dict enriched with service metadata (if matched).
    """
    summary = rec.get("summary", "")
    if not summary:
        return rec
    
    result = match_recommendation_to_service(summary, domain=domain)
    
    if result.service:
        return {
            **rec,
            "service_id": result.service.id,
            "order_of_completion": result.service.order_of_completion,
            "rebate_eligible": result.service.rebate_eligible,
            "match_confidence": result.confidence,
            "_match_type": result.match_type,
        }
    
    return rec


def get_unmatched_recommendation_report(rejected: List[dict]) -> str:
    """Generate a human-readable report of rejected recommendations.
    
    Useful for logging or admin review of recommendations that didn't
    match the service catalog.
    
    Args:
        rejected: List of rejected recommendation dicts from filter_and_enrich_recommendations.
    
    Returns:
        Formatted report string.
    """
    if not rejected:
        return "No rejected recommendations."
    
    lines = [f"Rejected Recommendations Report ({len(rejected)} items):", ""]
    
    for i, rec in enumerate(rejected, 1):
        summary = rec.get("summary", "N/A")[:100]
        reason = rec.get("_rejection_reason", "unknown")
        confidence = rec.get("_match_confidence", "N/A")
        matched_service = rec.get("_matched_service", "N/A")
        
        lines.append(f"{i}. {summary}...")
        lines.append(f"   Reason: {reason}")
        if confidence != "N/A":
            lines.append(f"   Confidence: {confidence:.2f}")
        if matched_service != "N/A":
            lines.append(f"   Nearest service: {matched_service}")
        lines.append("")
    
    return "\n".join(lines)
