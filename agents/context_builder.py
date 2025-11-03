# agents/context_builder.py
from models import Audit, AuditStep
from memory.config import memory_enabled, semantic_enabled, semantic_top_k, context_char_cap
from memory.semantic import retrieve_relevant_snippets
from memory.chat_memory import get_recent_messages


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