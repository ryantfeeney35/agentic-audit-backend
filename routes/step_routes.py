from flask import Blueprint, request, jsonify
from models import AuditMedia, AuditStep, db
import json

bp = Blueprint("steps", __name__)

# --- Helper to parse meta safely ---
def parse_meta(meta):
    if not meta:
        return {}
    if isinstance(meta, dict):
        return meta
    try:
        return json.loads(meta)
    except Exception:
        return {"raw": meta}

def serialize_step(step):
    notes_data = parse_meta(step.meta)

    return {
        "id": step.id,
        "label": step.label,
        "step_type": step.step_type,
        "status": step.status,
        "orientation": notes_data.get("orientation") if step.step_type == "exterior" else None,
        "siding_material": notes_data.get("siding_material") if step.step_type == "exterior" else None,
        "rooms": notes_data.get("rooms") if step.step_type == "exterior" else None,
        "meta": notes_data,
        "summary": step.summary,
        "ai_summary": step.ai_summary,
        "media": [
            {
                "id": m.id,
                "media_url": m.media_url,
                "file_name": m.file_name,
                "media_type": m.media_type,
                "notes": m.notes,
                "created_at": m.created_at.isoformat(),
            }
            for m in AuditMedia.query.filter_by(step_id=step.id).all()
        ],
    }

# --- Create or update a step ---
@bp.route("/audits/<int:audit_id>/steps", methods=["POST"])
def create_or_update_audit_step(audit_id):
    data = request.get_json()
    step_id = data.get("id")
    step_type = data.get("step_type")
    label = data.get("label")
    status = data.get("status")

    if not step_id and (not step_type or not label):
        return jsonify({"error": "Missing step_type or label"}), 400

    # --- UPDATE existing step ---
    if step_id:
        step = AuditStep.query.filter_by(id=step_id, audit_id=audit_id).first()
        if not step:
            return jsonify({"error": "Step not found"}), 404

        # Load current meta safely
        notes_data = step.meta.copy() if isinstance(step.meta, dict) else {}

        # ✅ Allow meta updates for ALL step types
        if "meta" in data and isinstance(data["meta"], dict):
            notes_data.update(data["meta"])

        # Preserve exterior-specific field support
        if step.step_type == "exterior":
            if "orientation" in data:
                notes_data["orientation"] = data["orientation"]
                step.label = data["orientation"]
            if "siding_material" in data:
                notes_data["siding_material"] = data["siding_material"]
            if "rooms" in data:
                notes_data["rooms"] = data["rooms"]

        if "notes" in data and isinstance(data["notes"], dict):
            notes_data.update(data["notes"])

        if "label" in data and not data.get("orientation"):
            step.label = data["label"]
        if "status" in data:
            step.status = status
        if "summary" in data:
            step.summary = data["summary"]
        if "ai_summary" in data:
            step.ai_summary = data["ai_summary"]

        step.meta = notes_data
        db.session.commit()
        return jsonify({"message": "Step updated", "id": step.id, "meta": step.meta}), 200

    # --- CREATE new step ---
    step = AuditStep.query.filter_by(audit_id=audit_id, step_type=step_type, label=label).first()
    notes_data = step.meta.copy() if step and isinstance(step.meta, dict) else {}

    # ✅ Also merge incoming meta for creation
    if "meta" in data and isinstance(data["meta"], dict):
        notes_data.update(data["meta"])

    if step_type == "exterior":
        if "orientation" in data:
            notes_data["orientation"] = data["orientation"]
        if "siding_material" in data:
            notes_data["siding_material"] = data["siding_material"]
        if "rooms" in data:
            notes_data["rooms"] = data["rooms"]

    if "notes" in data and isinstance(data["notes"], dict):
        notes_data.update(data["notes"])

    if step:
        step.status = status or step.status
        step.meta = notes_data
        step.summary = data.get("summary", step.summary)
        step.ai_summary = data.get("ai_summary", step.ai_summary)
        db.session.commit()
        return jsonify(serialize_step(step)), 200

    new_step = AuditStep(
        audit_id=audit_id,
        step_type=step_type,
        label=label,
        status=status or "Not Started",
        meta=notes_data,
        summary=data.get("summary"),
        ai_summary=data.get("ai_summary"),
    )
    db.session.add(new_step)
    db.session.commit()
    return jsonify(serialize_step(new_step)), 201

# --- Get all steps for an audit ---
@bp.route("/audits/<int:audit_id>/steps", methods=["GET"])
def get_audit_steps(audit_id):
    steps = AuditStep.query.filter_by(audit_id=audit_id).all()
    return jsonify([serialize_step(s) for s in steps])

# --- Delete a step ---
@bp.route("/audits/<int:audit_id>/steps/<int:step_id>", methods=["DELETE"])
def delete_audit_step(audit_id, step_id):
    step = AuditStep.query.filter_by(id=step_id, audit_id=audit_id).first()
    if not step:
        return jsonify({"error": "Step not found"}), 404
    try:
        db.session.delete(step)
        db.session.commit()
        return jsonify({"message": f"Step {step_id} deleted successfully"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Failed to delete step: {e}"}), 500