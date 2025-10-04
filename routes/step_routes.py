from flask import Blueprint, request, jsonify
from models import AuditMedia, AuditStep, db
import json

bp = Blueprint("steps", __name__)

# --- Helper to parse notes safely ---
def parse_notes(notes):
    if not notes:
        return {}
    if isinstance(notes, dict):
        return notes
    try:
        return json.loads(notes)
    except Exception:
        return {"raw": notes}  # fallback

def serialize_step(step):
    notes_data = parse_notes(step.notes)

    # Only expose orientation/siding/rooms for exterior steps
    return {
        "id": step.id,
        "label": step.label,
        "step_type": step.step_type,
        "status": step.status,
        "orientation": notes_data.get("orientation") if step.step_type == "exterior" else None,
        "siding_material": notes_data.get("siding_material") if step.step_type == "exterior" else None,
        "rooms": notes_data.get("rooms") if step.step_type == "exterior" else None,
        "notes": notes_data,  # still return all notes for flexibility
        "media": [
            {
                "id": m.id,
                "media_url": m.media_url,
                "file_name": m.file_name,
                "media_type": m.media_type,
                "summary": m.summary,
                "created_at": m.created_at.isoformat()
            }
            for m in AuditMedia.query.filter_by(step_id=step.id).all()
        ],
    }


# --- Create or update an audit step ---
@bp.route('/audits/<int:audit_id>/steps', methods=['POST'])
def create_or_update_audit_step(audit_id):
    data = request.get_json()
    step_id = data.get("id")  # ✅ explicit step id
    step_type = data.get("step_type")
    label = data.get("label")
    status = data.get("status")  # Not Started, Processing, Completed, Error, Not Accessible

    # Require step_type for new steps, but allow updating by id
    if not step_id and (not step_type or not label):
        return jsonify({'error': 'Missing step_type or label'}), 400

    # --- Update existing step if id provided ---
    if step_id:
        step = AuditStep.query.filter_by(id=step_id, audit_id=audit_id).first()
        if not step:
            return jsonify({'error': 'Step not found'}), 404

        # Work with JSONB meta dict
        notes_data = step.meta or {}

        if step.step_type == "exterior":
            if "orientation" in data:
                notes_data["orientation"] = data.get("orientation")
                # also update label to match orientation if provided
                step.label = data.get("orientation")
            if "siding_material" in data:
                notes_data["siding_material"] = data.get("siding_material")
            if "rooms" in data:
                notes_data["rooms"] = data.get("rooms", [])

        if "notes" in data and isinstance(data["notes"], dict):
            notes_data.update(data["notes"])

        if "label" in data and not data.get("orientation"):
            # Allow manual label override if no orientation is passed
            step.label = data["label"]

        if "status" in data:
            step.status = status

        step.meta = notes_data
        if "summary" in data:
            step.summary = data.get("summary")
        if "ai_summary" in data:
            step.ai_summary = data.get("ai_summary")

        db.session.commit()
        return jsonify({"message": "Step updated", "id": step.id}), 200

    # --- Otherwise: create or update based on audit_id + step_type + label ---
    step = AuditStep.query.filter_by(
        audit_id=audit_id,
        step_type=step_type,
        label=label
    ).first()

    notes_data = step.meta if step else {}

    if step_type == "exterior":
        if "orientation" in data:
            notes_data["orientation"] = data.get("orientation")
        if "siding_material" in data:
            notes_data["siding_material"] = data.get("siding_material")
        if "rooms" in data:
            notes_data["rooms"] = data.get("rooms", [])

    if "notes" in data and isinstance(data["notes"], dict):
        notes_data.update(data["notes"])

    if step:
        if status:
            step.status = status
        step.meta = notes_data
        if "summary" in data:
            step.summary = data.get("summary")
        if "ai_summary" in data:
            step.ai_summary = data.get("ai_summary")

        db.session.commit()
        return jsonify({"message": "Step updated", "id": step.id}), 200
    else:
        new_step = AuditStep(
            audit_id=audit_id,
            step_type=step_type,
            label=label,
            status=status if status else "Not Started",
            meta=notes_data,
            summary=data.get("summary"),
            ai_summary=data.get("ai_summary"),
        )
        db.session.add(new_step)
        db.session.commit()
        return jsonify({"message": "Step created", "id": new_step.id}), 201


# --- Get media for a specific step label ---
@bp.route('/audits/<int:audit_id>/steps/<string:step_label>/media', methods=['GET'])
def get_media_by_step_label(audit_id, step_label):
    step = AuditStep.query.filter_by(audit_id=audit_id, label=step_label).first()
    if not step:
        return jsonify([])

    media_items = AuditMedia.query.filter_by(step_id=step.id).all()
    return jsonify([
        {
            "id": m.id,
            "media_url": m.media_url,
            "file_name": m.file_name,
            "media_type": m.media_type,
            "summary": m.summary,
            "created_at": m.created_at.isoformat(),
            "status": step.status,
        }
        for m in media_items
    ])

@bp.route("/audits/<int:audit_id>/steps", methods=["GET"])
def get_audit_steps(audit_id):
    steps = AuditStep.query.filter_by(audit_id=audit_id).all()
    return jsonify([
        {
            "id": s.id,
            "label": s.label,
            "step_type": s.step_type,
            "status": s.status,
            "summary": s.summary,
            "ai_summary": s.ai_summary,
        }
        for s in steps
    ])

@bp.route("/audits/<int:audit_id>/steps/<int:step_id>", methods=["DELETE"])
def delete_audit_step(audit_id, step_id):
    """Delete a step (and any associated media) from an audit"""
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