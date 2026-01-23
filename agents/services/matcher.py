"""Service matching logic for recommendation-to-catalog alignment.

Matches AI-generated recommendation text to services in the catalog using:
1. Exact keyword matching
2. Fuzzy string matching (via rapidfuzz)
3. Category/action heuristics
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .catalog import ServiceEntry, get_catalog

logger = logging.getLogger(__name__)

# Minimum fuzzy match score (0-100) to consider a match valid
DEFAULT_FUZZY_THRESHOLD = 75

# Try to import rapidfuzz; fall back to basic matching if unavailable
try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    logger.warning("rapidfuzz not installed; fuzzy matching will be limited")
    RAPIDFUZZ_AVAILABLE = False
    fuzz = None
    process = None


@dataclass
class MatchResult:
    """Result of attempting to match a recommendation to a service."""
    
    service: Optional[ServiceEntry]
    confidence: float  # 0.0 to 1.0
    match_type: str  # "exact", "keyword", "fuzzy", "none"
    matched_text: Optional[str] = None  # The text that triggered the match


def normalize_text(text: str) -> str:
    """Normalize text for matching: lowercase, remove extra whitespace, strip punctuation."""
    text = text.lower()
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def extract_key_phrases(text: str) -> List[str]:
    """Extract key phrases from recommendation text for matching."""
    normalized = normalize_text(text)
    
    # Common recommendation patterns to extract
    patterns = [
        r'install\s+[\w\s]+',
        r'replace\s+[\w\s]+',
        r'repair\s+[\w\s]+',
        r'upgrade\s+[\w\s]+',
        r'optimize\s+[\w\s]+',
        r'add\s+[\w\s]+insulation',
        r'seal\s+[\w\s]+',
        r'[\w\s]+replacement',
        r'[\w\s]+installation',
        r'[\w\s]+repair',
    ]
    
    phrases = []
    for pattern in patterns:
        matches = re.findall(pattern, normalized)
        phrases.extend(matches)
    
    # Also include the full normalized text as a phrase
    phrases.append(normalized)
    
    return [p.strip() for p in phrases if p.strip()]


def match_by_keywords(text: str, services: List[ServiceEntry]) -> Optional[MatchResult]:
    """Attempt to match recommendation text using service keywords (exact substring match)."""
    normalized = normalize_text(text)
    
    best_match: Optional[ServiceEntry] = None
    best_keyword: Optional[str] = None
    best_keyword_len = 0
    
    for service in services:
        for keyword in service.keywords:
            keyword_norm = normalize_text(keyword)
            if keyword_norm in normalized:
                # Prefer longer keyword matches (more specific)
                if len(keyword_norm) > best_keyword_len:
                    best_match = service
                    best_keyword = keyword
                    best_keyword_len = len(keyword_norm)
    
    if best_match:
        logger.debug("Keyword match: '%s' -> %s", best_keyword, best_match.id)
        return MatchResult(
            service=best_match,
            confidence=0.9,
            match_type="keyword",
            matched_text=best_keyword,
        )
    
    return None


def match_by_fuzzy(
    text: str,
    services: List[ServiceEntry],
    threshold: int = DEFAULT_FUZZY_THRESHOLD,
) -> Optional[MatchResult]:
    """Attempt to match recommendation text using fuzzy string matching."""
    if not RAPIDFUZZ_AVAILABLE:
        return None
    
    normalized = normalize_text(text)
    key_phrases = extract_key_phrases(text)
    
    # Build a mapping from keywords/display names to services
    choices_to_service: dict[str, ServiceEntry] = {}
    
    for service in services:
        # Add all keywords
        for kw in service.keywords:
            choices_to_service[normalize_text(kw)] = service
        
        # Add display name and full path
        choices_to_service[normalize_text(service.display_name)] = service
        choices_to_service[normalize_text(service.full_path)] = service
        
        # Add action + sub_category combinations
        choices_to_service[normalize_text(f"{service.action} {service.sub_category}")] = service
        if service.sub_category_detail:
            choices_to_service[normalize_text(f"{service.action} {service.sub_category_detail}")] = service
    
    choices = list(choices_to_service.keys())
    
    best_score = 0
    best_choice = None
    
    # Try matching each key phrase
    for phrase in key_phrases:
        result = process.extractOne(phrase, choices, scorer=fuzz.token_set_ratio)
        if result and result[1] > best_score:
            best_score = result[1]
            best_choice = result[0]
    
    if best_choice and best_score >= threshold:
        service = choices_to_service[best_choice]
        logger.debug("Fuzzy match (score=%d): '%s' -> %s", best_score, best_choice, service.id)
        return MatchResult(
            service=service,
            confidence=best_score / 100.0,
            match_type="fuzzy",
            matched_text=best_choice,
        )
    
    return None


def match_by_category_heuristics(text: str, services: List[ServiceEntry]) -> Optional[MatchResult]:
    """Fallback matching using category and action heuristics."""
    normalized = normalize_text(text)
    
    # Detect action type
    action_keywords = {
        "install": ["install", "add", "put in"],
        "replace": ["replace", "upgrade", "swap", "new"],
        "repair": ["repair", "fix", "seal", "mend"],
        "optimization": ["optimize", "tune", "adjust", "maintenance"],
    }
    
    detected_action = None
    for action, keywords in action_keywords.items():
        for kw in keywords:
            if kw in normalized:
                detected_action = action
                break
        if detected_action:
            break
    
    # Detect category/sub-category
    category_keywords = {
        "insulation": ["insulation", "insulate", "r-value", "fiberglass", "blown-in", "batt"],
        "attic": ["attic"],
        "wall": ["wall insulation", "wall cavity"],
        "crawlspace": ["crawlspace", "crawl space", "underfloor"],
        "air sealing": ["air seal", "weather strip", "caulk", "draft", "leak"],
        "hvac": ["hvac", "furnace", "air conditioner", "heat pump", "ac unit", "heating", "cooling"],
        "ducting": ["duct", "ductwork", "air duct"],
        "solar": ["solar", "pv", "photovoltaic"],
        "lighting": ["light", "led", "bulb", "lamp", "fixture"],
        "water heater": ["water heater", "hot water"],
        "appliance": ["refrigerator", "fridge", "oven", "stove", "dishwasher"],
    }
    
    detected_categories = []
    for cat, keywords in category_keywords.items():
        for kw in keywords:
            if kw in normalized:
                detected_categories.append(cat)
                break
    
    if not detected_categories:
        return None
    
    # Find services that match detected categories and action
    candidates = []
    for service in services:
        service_text = normalize_text(f"{service.category} {service.sub_category} {service.sub_category_detail or ''} {service.action}")
        
        matches_category = any(cat in service_text for cat in detected_categories)
        matches_action = (
            detected_action is None
            or detected_action.lower() in service.action.lower()
            or (detected_action == "install" and "install" in service.action.lower())
            or (detected_action == "replace" and "replace" in service.action.lower())
        )
        
        if matches_category and matches_action:
            candidates.append(service)
    
    if candidates:
        # Return the first match (could be improved with scoring)
        service = candidates[0]
        logger.debug("Heuristic match: categories=%s, action=%s -> %s", detected_categories, detected_action, service.id)
        return MatchResult(
            service=service,
            confidence=0.6,
            match_type="heuristic",
            matched_text=f"{detected_categories}, {detected_action}",
        )
    
    return None


def match_recommendation_to_service(
    summary: str,
    domain: Optional[str] = None,
    fuzzy_threshold: int = DEFAULT_FUZZY_THRESHOLD,
) -> MatchResult:
    """Match a recommendation summary to the best service from the catalog.
    
    Tries matching strategies in order of reliability:
    1. Exact keyword match
    2. Fuzzy string match
    3. Category/action heuristics
    
    Args:
        summary: The recommendation summary text to match.
        domain: Optional domain to filter services (e.g., 'insulation', 'hvac').
        fuzzy_threshold: Minimum fuzzy match score (0-100) to accept.
    
    Returns:
        MatchResult with the matched service (or None if no match).
    """
    if not summary or not summary.strip():
        return MatchResult(service=None, confidence=0.0, match_type="none")
    
    catalog = get_catalog()
    services = catalog.get_services_by_domain(domain) if domain else catalog.services
    
    if not services:
        logger.warning("No services available for domain: %s", domain)
        return MatchResult(service=None, confidence=0.0, match_type="none")
    
    # Try each matching strategy in order
    result = match_by_keywords(summary, services)
    if result:
        return result
    
    result = match_by_fuzzy(summary, services, threshold=fuzzy_threshold)
    if result:
        return result
    
    result = match_by_category_heuristics(summary, services)
    if result:
        return result
    
    # No match found
    logger.info("No service match for recommendation: %s...", summary[:100])
    return MatchResult(service=None, confidence=0.0, match_type="none")


def find_best_service_match(
    summary: str,
    step_type: Optional[str] = None,
) -> Tuple[Optional[str], Optional[int], Optional[bool], float]:
    """Convenience function returning (service_id, order_of_completion, rebate_eligible, confidence).
    
    Args:
        summary: Recommendation summary text.
        step_type: Optional step type (maps to domain for filtering).
    
    Returns:
        Tuple of (service_id, order_of_completion, rebate_eligible, confidence).
        Returns (None, None, None, 0.0) if no match found.
    """
    # Map step_type to domain
    domain_map = {
        "exterior": "exterior",
        "interior": "interior",
        "hvac": "hvac",
        "insulation": "insulation",
    }
    domain = domain_map.get((step_type or "").lower())
    
    result = match_recommendation_to_service(summary, domain=domain)
    
    if result.service:
        return (
            result.service.id,
            result.service.order_of_completion,
            result.service.rebate_eligible,
            result.confidence,
        )
    
    return (None, None, None, 0.0)
