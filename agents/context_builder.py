# agents/context_builder.py
from models import Audit, AuditStep

def build_audit_context(audit_id: int) -> str:
    """Collect property, summaries, and AI insights into a single text context string."""
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
        if step.summary:
            context_summary.append(f"📋 {step.label} ({step.step_type}) — {step.summary}")

        # AI structured output (parsed JSONB)
        if step.ai_summary and isinstance(step.ai_summary, dict):
            ai_parts = []
            for key, val in step.ai_summary.items():
                # Flatten any nested simple fields
                if isinstance(val, (str, int, float)):
                    ai_parts.append(f"{key.replace('_', ' ').title()}: {val}")
            if ai_parts:
                context_summary.append(f"🤖 {step.step_type.title()} findings: " + ", ".join(ai_parts))

    # --- Final compiled context string ---
    return "\n".join(context_summary)