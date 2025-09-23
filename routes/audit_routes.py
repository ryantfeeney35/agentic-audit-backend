from flask import Blueprint, request, jsonify
from models import Audit, db

bp = Blueprint("audits", __name__)

@bp.route("/audits", methods=["POST"])
def create_audit():
    data = request.get_json()
    property_id = data.get("property_id")
    if not property_id:
        return jsonify({"error": "Missing property_id"}), 400

    new_audit = Audit(property_id=property_id)
    db.session.add(new_audit)
    db.session.commit()
    return jsonify({
        "id": new_audit.id,
        "property_id": new_audit.property_id,
        "date": new_audit.date.isoformat()
    }), 201