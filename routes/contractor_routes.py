from flask import Blueprint, jsonify, request
from models import Contractor, db

bp = Blueprint("contractors", __name__)

# Get contractors by step_type
@bp.route("/contractors/<string:step_type>", methods=["GET"])
def get_contractors(step_type):
    contractors = Contractor.query.filter_by(step_type=step_type).all()
    return jsonify([
        {"id": c.id, "name": c.name, "contact": c.contact, "step_type": c.step_type}
        for c in contractors
    ])

# Add a contractor (optional admin route)
@bp.route("/contractors", methods=["POST"])
def add_contractor():
    data = request.get_json()
    name = data.get("name")
    contact = data.get("contact")
    step_type = data.get("step_type")

    if not (name and contact and step_type):
        return jsonify({"error": "Missing required fields"}), 400

    contractor = Contractor(name=name, contact=contact, step_type=step_type)
    db.session.add(contractor)
    db.session.commit()
    return jsonify({"id": contractor.id}), 201