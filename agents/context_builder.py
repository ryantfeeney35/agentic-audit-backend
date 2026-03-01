# agents/context_builder.py
from typing import Optional, List, Dict, Any
from datetime import datetime
from collections import defaultdict
from models import Audit, AuditStep, UtilityConnection, UtilityUsageSummary, UtilityIntervalData, db
from memory.config import memory_enabled, semantic_enabled, semantic_top_k, context_char_cap
from memory.semantic import retrieve_relevant_snippets
from memory.chat_memory import get_recent_messages
from agents.schemas import (
    EnergyUsageAnalysisInput,
    UtilityUsageSummarySchema,
    OccupancyInfo,
    ApplianceItem,
    SolarInfo,
)
import logging

logger = logging.getLogger(__name__)


def get_structured_context(audit_id: int, exclude_audio: bool = False) -> str:
    """Build the structured portion of the context: property facts, interview notes,
    step summaries, and AI structured findings.
    """
    audit = Audit.query.get(audit_id)
    if not audit:
        return ""

    context_summary = []

    # --- Property details ---
    if audit.property:
        address = f"{audit.property.street}, {audit.property.city}, {audit.property.state} {audit.property.zip_code}"
        sqft = f"{audit.property.sqft} sqft" if audit.property.sqft else "sqft unknown"
        year = f"Year built: {audit.property.year_built}" if audit.property.year_built else "Year built unknown"
        context_summary.append(f"🏠 Property: {address}, {sqft}, {year}")

    # --- Interview notes (still stored at audit level) ---
    if audit.notes:
        context_summary.append(f"🗣️ Interview summary: {audit.notes}")

    # --- Steps + structured AI summaries ---
    steps = AuditStep.query.filter_by(audit_id=audit_id).all()
    for step in steps:
        if step.status == "Not Accessible":
            continue

        # Step summary (human-written or AI-summarized audio)
        if not exclude_audio and step.summary:
            context_summary.append(f"📋 {step.label} ({step.step_type}) — {step.summary}")

        # AI structured output (parsed JSONB)
        if step.ai_summary and isinstance(step.ai_summary, dict):
            ai_parts = []
            for key, val in step.ai_summary.items():
                if isinstance(val, (str, int, float)):
                    ai_parts.append(f"{key.replace('_', ' ').title()}: {val}")
            if ai_parts:
                context_summary.append(f"🤖 {step.step_type.title()} findings: " + ", ".join(ai_parts))

    return "\n".join(context_summary)

def build_audit_context(audit_id: int, exclude_audio: bool = False) -> str:
    """Legacy structured context builder (delegates to get_structured_context)."""
    return get_structured_context(audit_id, exclude_audio=exclude_audio)


def get_audit_memory_context(
    audit_id: int,
    domain: str | None = None,
    exclude_audio: bool = False,
    chat_limit: int = 12,
) -> str:
    """Return structured context plus a tail of recent conversation from persistent memory.

    - Excludes orchestrator assistant messages from the conversational tail.
    - Safe to call when memory is disabled; returns only structured context.
    """
    structured = get_structured_context(audit_id, exclude_audio=exclude_audio)
    if not memory_enabled():
        return structured

    msgs = get_recent_messages(audit_id, limit=chat_limit) or []
    # Filter out orchestrator assistant messages; our stored format is like "[ai] [domain] content"
    filtered: list[str] = []
    for m in msgs:
        # Normalize role marker
        is_assistant = m.startswith("[ai]") or m.startswith("[assistant]")
        # Keep if not an orchestrator assistant line
        if is_assistant and "[orchestrator]" in m:
            continue
        filtered.append(m)

    sections: list[str] = [structured]
    if filtered:
        sections.append("---\n\nRecent discussion:\n" + "\n".join(filtered))

    # Optional semantic recall (Phase 3)
    if semantic_enabled():
        try:
            # Use the structured context as the query seed
            snippets = retrieve_relevant_snippets(audit_id, structured, k=semantic_top_k())
            if snippets:
                sections.append("---\nRelevant prior findings:\n- " + "\n- ".join(snippets))
        except Exception:
            pass

    final = "\n\n".join([s for s in sections if s])

    # Safe truncation (Phase 4)
    cap = context_char_cap()
    if len(final) > cap:
        final = final[:cap] + "\n... [truncated]"
    return final


def build_audio_context(audit_id: int) -> str:
    """Build a minimal context string derived only from audio artifacts and
    any auditor refinements to those audio items (e.g. recommendation summary_override).

    This intentionally avoids pulling full property/context data.
    """
    from models import AuditMedia, AuditRecommendation

    lines = []

    # Include any recommendation-level human/audio refinements (summary_override)
    recs = AuditRecommendation.query.filter_by(audit_id=audit_id).all()
    for r in recs:
        if r.summary_override:
            lines.append(f"[Recommendation Override] {r.summary_override}")

    # Include metadata about audio media files attached to the audit (filenames/urls)
    medias = AuditMedia.query.filter_by(audit_id=audit_id).all()
    for m in medias:
        if m.media_type and m.media_type.lower().startswith("audio"):
            lines.append(f"[Audio File] {m.file_name} - {m.media_url}")

    return "\n".join(lines)


def _summarize_interval_data(connection_id: int) -> tuple[Optional[Dict[str, Any]], Optional[List[Dict[str, Any]]]]:
    """
    Summarize interval data for the Energy Usage Agent.
    
    Processes raw 15-minute interval readings into:
    1. An interval_summary with aggregated statistics
    2. Daily load profiles (weekday vs weekend averages by hour)
    
    Args:
        connection_id: The utility connection ID
        
    Returns:
        Tuple of (interval_summary dict, daily_profiles list) or (None, None) if no data
    """
    intervals = UtilityIntervalData.query.filter_by(
        connection_id=connection_id
    ).order_by(UtilityIntervalData.interval_start).all()
    
    if not intervals:
        return None, None
    
    logger.info(f"Summarizing {len(intervals)} intervals for connection {connection_id}")
    
    # Calculate hourly averages across all data
    hourly_totals = defaultdict(list)  # hour -> list of kWh values
    weekday_hourly = defaultdict(list)  # hour -> list of kWh values for weekdays
    weekend_hourly = defaultdict(list)  # hour -> list of kWh values for weekends
    
    total_kwh = 0
    min_date = None
    max_date = None
    
    for interval in intervals:
        hour = interval.interval_start.hour
        kwh = interval.usage_kwh
        day_of_week = interval.interval_start.weekday()  # 0=Monday, 6=Sunday
        
        hourly_totals[hour].append(kwh)
        total_kwh += kwh
        
        if day_of_week < 5:  # Weekday
            weekday_hourly[hour].append(kwh)
        else:  # Weekend
            weekend_hourly[hour].append(kwh)
        
        if min_date is None or interval.interval_start < min_date:
            min_date = interval.interval_start
        if max_date is None or interval.interval_end > max_date:
            max_date = interval.interval_end
    
    # Calculate hourly averages
    hourly_averages = {}
    for hour in range(24):
        if hourly_totals[hour]:
            hourly_averages[hour] = round(sum(hourly_totals[hour]) / len(hourly_totals[hour]), 3)
        else:
            hourly_averages[hour] = 0
    
    # Identify peak hours (top 3 hours by average usage)
    sorted_hours = sorted(hourly_averages.items(), key=lambda x: x[1], reverse=True)
    peak_hours = [h for h, _ in sorted_hours[:3]]
    
    # Calculate baseload (minimum average across all hours)
    baseload_kw = min(hourly_averages.values()) * 4 if hourly_averages else 0  # Convert 15-min kWh to kW
    
    # Build interval summary
    interval_summary = {
        "total_intervals": len(intervals),
        "total_kwh": round(total_kwh, 2),
        "date_range": {
            "start": min_date.isoformat() if min_date else None,
            "end": max_date.isoformat() if max_date else None,
        },
        "hourly_averages_kwh": hourly_averages,
        "peak_hours": peak_hours,
        "baseload_kw": round(baseload_kw, 2),
    }
    
    # Build daily profiles
    weekday_profile = [0] * 24
    weekend_profile = [0] * 24
    
    for hour in range(24):
        if weekday_hourly[hour]:
            weekday_profile[hour] = round(sum(weekday_hourly[hour]) / len(weekday_hourly[hour]), 3)
        if weekend_hourly[hour]:
            weekend_profile[hour] = round(sum(weekend_hourly[hour]) / len(weekend_hourly[hour]), 3)
    
    daily_profiles = [
        {"day_type": "weekday", "hourly_kwh": weekday_profile},
        {"day_type": "weekend", "hourly_kwh": weekend_profile},
    ]
    
    return interval_summary, daily_profiles


def get_energy_usage_context(audit_id: int) -> Optional[EnergyUsageAnalysisInput]:
    """Build EnergyUsageAnalysisInput context for the Energy Usage Agent.
    
    Queries UtilityUsageSummary for the audit and assembles context from:
    - Utility usage summary data
    - HVAC findings from AuditStep ai_summary
    - Appliance inventory from interview/steps
    - Solar info from property or steps
    - Occupancy info from interview
    - Climate zone from property zip code
    - Home square footage
    
    Returns:
        EnergyUsageAnalysisInput if utility data is connected, None otherwise.
    """
    # Ensure clean transaction state before DB queries
    try:
        db.session.rollback()
    except Exception:
        pass
        
    # Check for connected utility data
    try:
        connection = UtilityConnection.query.filter_by(
            audit_id=audit_id,
            status='connected'
        ).first()
    except Exception as e:
        logger.warning("Failed to query utility connection: %s", e)
        try:
            db.session.rollback()
        except Exception:
            pass
        return None
    
    if not connection:
        return None
    
    # Get the usage summary
    usage_summary = UtilityUsageSummary.query.filter_by(
        connection_id=connection.id
    ).first()
    
    if not usage_summary:
        return None
    
    # Get interval data summary if available
    interval_summary, daily_profiles = _summarize_interval_data(connection.id)
    
    if interval_summary:
        logger.info(f"Including {interval_summary.get('total_intervals', 0)} intervals in energy usage context")
    
    # Build utility summary schema
    utility_schema = UtilityUsageSummarySchema(
        fuel_type=usage_summary.fuel_type or 'electric',
        start_date=usage_summary.start_date.isoformat() if usage_summary.start_date else None,
        end_date=usage_summary.end_date.isoformat() if usage_summary.end_date else None,
        annual_usage_kwh=usage_summary.annual_usage,
        annual_cost_usd=usage_summary.annual_cost_usd,
        monthly_breakdown=usage_summary.monthly_breakdown,
        seasonal_pattern=usage_summary.seasonal_pattern,
        tou_data=usage_summary.tou_data,
        data_quality_flags=usage_summary.data_quality_flags,
        interval_summary=interval_summary,
        daily_profiles=daily_profiles,
    )
    
    # Get audit for property and interview data
    audit = Audit.query.get(audit_id)
    if not audit:
        return None
    
    # Extract HVAC summary from steps
    hvac_summary = None
    hvac_step = AuditStep.query.filter_by(
        audit_id=audit_id,
        step_type='HVAC'
    ).first()
    if hvac_step and hvac_step.ai_summary:
        hvac_summary = hvac_step.ai_summary
    
    # Extract appliances from steps (look for inventory in ai_summary)
    appliance_inventory = []
    for step in AuditStep.query.filter_by(audit_id=audit_id).all():
        if step.ai_summary and isinstance(step.ai_summary, dict):
            # Look for appliances in various fields
            appliances = step.ai_summary.get('appliances', [])
            if isinstance(appliances, list):
                for app in appliances:
                    if isinstance(app, dict):
                        appliance_inventory.append(ApplianceItem(
                            name=app.get('name', 'Unknown'),
                            location=app.get('location'),
                            estimated_age_years=app.get('age_years'),
                            condition=app.get('condition'),
                            usage_pattern=app.get('usage_pattern'),
                        ))
                    elif isinstance(app, str):
                        appliance_inventory.append(ApplianceItem(name=app))
    
    # Extract solar info from property or steps
    solar_info = None
    if audit.property:
        # Check property metadata for solar
        prop = audit.property
        if hasattr(prop, 'has_solar') and prop.has_solar:
            solar_info = SolarInfo(
                has_solar=True,
                system_size_kw=getattr(prop, 'solar_kw', None),
            )
    # Also check steps for solar observations
    if not solar_info:
        for step in AuditStep.query.filter_by(audit_id=audit_id).all():
            if step.ai_summary and isinstance(step.ai_summary, dict):
                solar_data = step.ai_summary.get('solar')
                if solar_data and isinstance(solar_data, dict):
                    solar_info = SolarInfo(
                        has_solar=solar_data.get('has_solar', False),
                        system_size_kw=solar_data.get('system_size_kw'),
                        annual_production_kwh=solar_data.get('annual_production_kwh'),
                        has_battery=solar_data.get('has_battery'),
                    )
                    break
    
    # Extract occupancy info from interview notes
    occupancy_info = None
    if audit.notes:
        # Parse occupancy hints from interview notes
        notes_lower = audit.notes.lower()
        occupancy_data = {}
        
        # Try to extract occupant count
        import re
        occupant_match = re.search(r'(\d+)\s*(?:people|occupants|residents)', notes_lower)
        if occupant_match:
            occupancy_data['num_occupants'] = int(occupant_match.group(1))
        
        # Check for work-from-home mentions
        if 'work from home' in notes_lower or 'wfh' in notes_lower or 'remote work' in notes_lower:
            occupancy_data['work_from_home'] = True
        
        if occupancy_data:
            occupancy_info = OccupancyInfo(**occupancy_data)
    
    # Extract comfort issues from interview/steps
    comfort_issues = []
    if audit.notes:
        # Look for common comfort complaints
        notes_lower = audit.notes.lower()
        comfort_keywords = [
            ('hot', 'hot spots or rooms'),
            ('cold', 'cold spots or rooms'),
            ('draft', 'drafty areas'),
            ('humid', 'humidity issues'),
            ('dry', 'dry air issues'),
        ]
        for keyword, description in comfort_keywords:
            if keyword in notes_lower:
                comfort_issues.append(description)
    
    # Get climate zone from property zip code
    climate_zone = None
    if audit.property and audit.property.zip_code:
        try:
            from utils.climate import get_climate_zone
            climate_zone = get_climate_zone(audit.property.zip_code)
            if climate_zone == "Unknown":
                climate_zone = None
        except (ImportError, Exception):
            pass
    
    # Get home square footage
    home_sqft = None
    if audit.property and audit.property.sqft:
        home_sqft = audit.property.sqft
    
    # Build the complete input context
    return EnergyUsageAnalysisInput(
        utility_summary=utility_schema,
        appliance_inventory=appliance_inventory,
        hvac_summary=hvac_summary,
        solar_info=solar_info,
        occupancy_info=occupancy_info,
        comfort_issues=comfort_issues,
        climate_zone=climate_zone,
        home_sqft=home_sqft,
    )