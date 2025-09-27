from flask import Blueprint, jsonify
from models import Audit, AuditStep, AuditMedia, AuditRecommendation, db
from openai import OpenAI
import os, json

bp = Blueprint("recommendations", __name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def generate_recommendations(audit_id: int):
    audit = Audit.query.get(audit_id)
    if not audit:
        return None, {"error": "Audit not found"}, 404

    steps = AuditStep.query.filter_by(audit_id=audit_id).all()
    step_contexts = []
    for step in steps:
        if step.status not in ("Completed", "Error"):  # ✅ only include completed/error
            continue
        media_summaries = [m.summary for m in step.media if m.summary]
        step_contexts.append({
            "label": step.label,
            "step_type": step.step_type,
            "notes": step.notes,
            "media": media_summaries
        })

    system_prompt = (
        "You are an energy auditor following CREIA protocol.\n"
        "Generate upgrade recommendations with numeric ROI.\n"
        "Return ONLY valid JSON of the form:\n\n"
        "{\n"
        "  \"recommendations\": [\n"
        "    {\n"
        "      \"step_type\": \"insulation | hvac | exterior | general\",\n"
        "      \"summary\": \"string\",\n"
        "      \"annual_savings_usd\": number,\n"
        "      \"upgrade_cost_usd\": number,\n"
        "      \"payback_years\": number\n"
        "    }, ...\n"
        "  ]\n"
        "}"
    )

    resp = client.chat.completions.create(
        model="gpt-4.1",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(step_contexts)},
        ],
        temperature=0.3,
        response_format={"type": "json_object"},  # ✅ enforce JSON
    )

    text = resp.choices[0].message.content.strip()
    try:
        parsed = json.loads(text)
        recommendations = parsed.get("recommendations", [])
    except Exception as e:
        print(f"⚠️ Failed to parse recommendations JSON: {e}")
        recommendations = []

    # Fallback: always have at least one rec
    if not recommendations:
        recommendations = [{
            "step_type": "general",
            "summary": "No recommendations could be generated.",
            "annual_savings_usd": 0,
            "upgrade_cost_usd": 0,
            "payback_years": None
        }]

    # Save to DB
    AuditRecommendation.query.filter_by(audit_id=audit_id).delete()
    saved = []
    for rec in recommendations:
        r = AuditRecommendation(
            audit_id=audit_id,
            step_type=rec.get("step_type", "general"),
            summary=rec.get("summary", ""),
            annual_savings_usd=rec.get("annual_savings_usd"),
            upgrade_cost_usd=rec.get("upgrade_cost_usd"),
            payback_years=rec.get("payback_years"),
        )
        db.session.add(r)
        saved.append(r)
    db.session.commit()

    return saved, None, 200

# --- Routes ---
@bp.route("/audits/<int:audit_id>/recommendations", methods=["GET"])
def get_recommendations(audit_id):
    existing = AuditRecommendation.query.filter_by(audit_id=audit_id).all()
    if existing:
        return jsonify([
            {
                "step_type": r.step_type,
                "summary": r.summary,
                "annual_savings_usd": r.annual_savings_usd,
                "upgrade_cost_usd": r.upgrade_cost_usd,
                "payback_years": r.payback_years,
                "created_at": r.created_at.isoformat()
            }
            for r in existing
        ])

    saved, error, code = generate_recommendations(audit_id)
    if error:
        return jsonify(error), code
    return jsonify([
        {
            "step_type": r.step_type,
            "summary": r.summary,
            "annual_savings_usd": r.annual_savings_usd,
            "upgrade_cost_usd": r.upgrade_cost_usd,
            "payback_years": r.payback_years,
            "created_at": r.created_at.isoformat()
        }
        for r in saved
    ])

@bp.route("/audits/<int:audit_id>/recommendations/regenerate", methods=["POST"])
def regenerate_recommendations(audit_id):
    saved, error, code = generate_recommendations(audit_id)
    if error:
        return jsonify(error), code
    return jsonify([
        {
            "step_type": r.step_type,
            "summary": r.summary,
            "annual_savings_usd": r.annual_savings_usd,
            "upgrade_cost_usd": r.upgrade_cost_usd,
            "payback_years": r.payback_years,
            "created_at": r.created_at.isoformat()
        }
        for r in saved
    ])