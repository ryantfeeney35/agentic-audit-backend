from flask import Blueprint, request, jsonify
from models import AuditStep, db
import json

bp = Blueprint("steps", __name__)

@bp.route("/audits/<int:audit_id>/steps", methods=["GET"])
def get_audit_steps(audit_id):
    steps = AuditStep.query.filter_by(audit_id=audit_id).all()
    result = []
    for step in steps:
        notes_parsed = None
        if step.notes:
            try:
                notes_parsed = json.loads(step.notes)
            except Exception:
                notes_parsed = step.notes
        result.append({
            "id": step.id,
            "label": step.label,
            "step_type": step.step_type,
            "is_completed": step.is_completed,
            "not_accessible": step.not_accessible,
            "notes": notes_parsed,
        })
    return jsonify(result)