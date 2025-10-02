# agents/context_builder.py
from models import Audit, AuditStep, AuditMedia

def build_audit_context(audit_id: int) -> str:
    """Collect property, notes, steps, and media into a single text context string."""

    audit = Audit.query.get(audit_id)
    if not audit:
        return ""

    context_summary = []

    # --- Property details ---
    if audit.property:
        address = f"{audit.property.street}, {audit.property.city}, {audit.property.state} {audit.property.zip_code}"
        sqft = f"{audit.property.sqft} sqft" if audit.property.sqft else "sqft unknown"
        year = f"Year built: {audit.property.year_built}" if audit.property.year_built else "Year built unknown"
        context_summary.append(f"Property: {address}, {sqft}, {year}")

    # --- Interview notes ---
    if audit.notes:
        context_summary.append(f"Interview summary: {audit.notes}")

    # --- Steps + media ---
    steps = AuditStep.query.filter_by(audit_id=audit_id).all()
    for step in steps:
        if step.status == "Not Accessible":
            continue

        if step.notes:
            context_summary.append(f"{step.label} ({step.step_type}) - Notes: {step.notes}")

        for media in step.media:
            if media.summary:
                if step.step_type == "interview":
                    context_summary.append(f"Interview media summary: {media.summary}")
                elif step.step_type == "utility_bill":
                    context_summary.append(f"Utility bill summary: {media.summary}")
                else:
                    context_summary.append(f"{step.label} media summary: {media.summary}")

    return "\n".join(context_summary)