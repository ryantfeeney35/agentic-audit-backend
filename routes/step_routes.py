from flask import Blueprint, request, jsonify
from models import AuditMedia, AuditStep, db
import json

bp = Blueprint("steps", __name__)

# --- Get all steps for an audit ---
@bp.route('/audits/<int:audit_id>/steps', methods=['GET'])
def get_audit_steps(audit_id):
    steps = AuditStep.query.filter_by(audit_id=audit_id).all()
    result = []

    for step in steps:
        # Fetch media linked to this step
        media_items = AuditMedia.query.filter_by(step_id=step.id).all()
        media = [{
            "id": m.id,
            "media_url": m.media_url,
            "file_name": m.file_name,
            "media_type": m.media_type,
            "summary": m.summary,
            "created_at": m.created_at.isoformat()
        } for m in media_items]

        # Parse notes JSON if possible
        notes_parsed = None
        if step.notes:
            try:
                notes_parsed = json.loads(step.notes)
            except Exception:
                notes_parsed = step.notes  # fallback to raw string

        result.append({
            "id": step.id,
            "label": step.label,
            "step_type": step.step_type,
            "status": step.status,   # ✅ new unified status field
            "notes": notes_parsed,
            "media": media
        })

    return jsonify(result)


# --- Create or update an audit step ---
@bp.route('/audits/<int:audit_id>/steps', methods=['POST'])
def create_or_update_audit_step(audit_id):
    data = request.get_json()
    step_type = data.get('step_type')
    label = data.get('label')
    status = data.get('status')  # Expect one of: Not Started, Processing, Completed, Error, Not Accessible
    notes = data.get('notes')

    if not step_type or not label:
        return jsonify({'error': 'Missing step_type or label'}), 400

    # Serialize notes if dict
    notes_str = json.dumps(notes) if isinstance(notes, dict) else notes

    # Check if step exists
    existing_step = AuditStep.query.filter_by(
        audit_id=audit_id,
        step_type=step_type,
        label=label
    ).first()

    if existing_step:
        if status:
            existing_step.status = status
        if notes is not None:
            existing_step.notes = notes_str

        db.session.commit()
        return jsonify({"message": "Step updated", "id": existing_step.id}), 200
    else:
        new_step = AuditStep(
            audit_id=audit_id,
            step_type=step_type,
            label=label,
            status=status if status else "Not Started",
            notes=notes_str
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
            "status": step.status  # ✅ return step status along with media
        } for m in media_items
    ])