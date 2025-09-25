from flask import Blueprint, request, jsonify
from sqlalchemy import text
from models import Property, db
import os
from supabase import create_client, Client

bp = Blueprint("properties", __name__)

# --- Supabase setup ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_BUCKET_NAME = os.getenv("SUPABASE_BUCKET_NAME")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# --- Routes ---
@bp.route('/properties', methods=['GET', 'POST'])
def handle_properties():
    if request.method == 'GET':
        with db.engine.connect() as conn:
            result = conn.execute(text("""
                SELECT id, street, city, state, zip_code, year_built, sqft FROM properties
            """))
            properties = [
                {
                    "id": row.id,
                    "street": row.street,
                    "city": row.city,
                    "state": row.state,
                    "zip_code": row.zip_code,
                    "year_built": row.year_built,
                    "sqft": row.sqft
                }
                for row in result
            ]
            return jsonify(properties)

    elif request.method == 'POST':
        data = request.get_json()
        new_property = Property(
            street=data.get('street'),
            city=data.get('city'),
            state=data.get('state'),
            zip_code=data.get('zip_code'),
            year_built=data.get('year_built'),
            sqft=data.get('sqft')
        )
        db.session.add(new_property)
        db.session.commit()
        return jsonify({'id': new_property.id}), 201

@bp.route('/properties/<int:property_id>', methods=['GET'])
def get_property(property_id):
    with db.engine.connect() as conn:
        result = conn.execute(text("""
            SELECT id, street, city, state, zip_code, year_built, sqft
            FROM properties
            WHERE id = :id
        """), {"id": property_id}).fetchone()

        if result:
            return jsonify({
                "id": result.id,
                "street": result.street,
                "city": result.city,
                "state": result.state,
                "zip_code": result.zip_code,
                "year_built": result.year_built,
                "sqft": result.sqft
            })
        else:
            return jsonify({"error": "Property not found"}), 404

@bp.route('/properties/<int:id>', methods=['PUT'])
def update_property(id):
    data = request.get_json()
    stmt = text("""
        UPDATE properties
        SET street=:street, city=:city, state=:state, zip_code=:zip_code, year_built=:year_built, sqft=:sqft
        WHERE id=:id
    """)
    with db.engine.begin() as conn:
        conn.execute(stmt, {**data, "id": id})
    return jsonify({"message": "Property updated"})

@bp.route('/properties/<int:property_id>', methods=['DELETE'])
def delete_property(property_id):
    with db.engine.begin() as conn:
        result = conn.execute(
            text("DELETE FROM properties WHERE id = :id RETURNING id"),
            {"id": property_id}
        )
        deleted = result.fetchone()
        if deleted:
            return jsonify({"message": "Property deleted", "id": deleted.id}), 200
        else:
            return jsonify({"error": "Property not found"}), 404