from flask import Blueprint, request, jsonify
from sqlalchemy import text
from models import Property, db

bp = Blueprint("properties", __name__)

@bp.route("/properties", methods=["GET", "POST"])
def handle_properties():
    if request.method == "GET":
        with db.engine.connect() as conn:
            result = conn.execute(text("""
                SELECT id, street, city, state, zip_code, year_built, sqft FROM properties
            """))
            properties = [dict(row) for row in result]
            return jsonify(properties)

    elif request.method == "POST":
        data = request.get_json()
        new_property = Property(
            street=data.get("street"),
            city=data.get("city"),
            state=data.get("state"),
            zip_code=data.get("zip_code"),
            year_built=data.get("year_built"),
            sqft=data.get("sqft"),
        )
        db.session.add(new_property)
        db.session.commit()
        return jsonify({"id": new_property.id}), 201