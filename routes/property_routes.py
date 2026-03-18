from flask import Blueprint, request, jsonify, g
from sqlalchemy import text
from models import Property, db
from auth import require_auth
from utils.homeowner_utils import normalize_phone
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
@require_auth
def handle_properties():
    if request.method == 'GET':
        # Filter properties by current user
        with db.engine.connect() as conn:
            result = conn.execute(text("""
                SELECT id, street, city, state, zip_code, year_built, sqft, property_type, phone_number, google_place_id 
                FROM properties WHERE user_id = :user_id
            """), {"user_id": g.current_user['id']})
            properties = [
                {
                    "id": row.id,
                    "street": row.street,
                    "city": row.city,
                    "state": row.state,
                    "zip_code": row.zip_code,
                    "year_built": row.year_built,
                    "sqft": row.sqft,
                    "property_type": row.property_type,
                    "phone_number": row.phone_number,
                    "google_place_id": row.google_place_id,
                }
                for row in result
            ]
            return jsonify(properties)

    elif request.method == 'POST':
        data = request.get_json()
        # Normalize phone number to E.164 format for homeowner auth compatibility
        raw_phone = data.get('phone_number')
        normalized_phone = normalize_phone(raw_phone) if raw_phone else None
        
        new_property = Property(
            user_id=g.current_user['id'],  # Automatically set user_id
            street=data.get('street'),
            city=data.get('city'),
            state=data.get('state'),
            zip_code=data.get('zip_code'),
            year_built=data.get('year_built'),
            sqft=data.get('sqft'),
            property_type=data.get('property_type'),
            phone_number=normalized_phone,
            google_place_id=data.get('google_place_id')
        )
        db.session.add(new_property)
        db.session.commit()
        return jsonify({
            'id': new_property.id,
            "street": new_property.street,
            "city": new_property.city,
            "state": new_property.state,
            "zip_code": new_property.zip_code,
            "year_built": new_property.year_built,
            "sqft": new_property.sqft,
            "property_type": new_property.property_type,
            "phone_number": new_property.phone_number,
            "google_place_id": new_property.google_place_id,
        }), 201

@bp.route('/properties/<int:property_id>', methods=['GET'])
@require_auth
def get_property(property_id):
    # Check ownership before allowing access
    property_obj = Property.query.filter_by(id=property_id, user_id=g.current_user['id']).first()
    if not property_obj:
        return jsonify({'error': 'Property not found or access denied'}), 403
    
    with db.engine.connect() as conn:
        result = conn.execute(text("""
            SELECT id, street, city, state, zip_code, year_built, sqft, property_type, phone_number, google_place_id
            FROM properties
            WHERE id = :id AND user_id = :user_id
        """), {"id": property_id, "user_id": g.current_user['id']}).fetchone()

        if result:
            return jsonify({
                "id": result.id,
                "street": result.street,
                "city": result.city,
                "state": result.state,
                "zip_code": result.zip_code,
                "year_built": result.year_built,
                "sqft": result.sqft,
                "property_type": result.property_type,
                "phone_number": result.phone_number,
                "google_place_id": result.google_place_id,
            })
        else:
            return jsonify({"error": "Property not found"}), 404

@bp.route('/properties/<int:id>', methods=['PUT'])
@require_auth
def update_property(id):
    # Check ownership before allowing update
    property_obj = Property.query.filter_by(id=id, user_id=g.current_user['id']).first()
    if not property_obj:
        return jsonify({'error': 'Property not found or access denied'}), 403
        
    data = request.get_json()
    # Normalize phone number to E.164 format for homeowner auth compatibility
    if 'phone_number' in data and data['phone_number']:
        data['phone_number'] = normalize_phone(data['phone_number'])
    
    stmt = text("""
        UPDATE properties
        SET street=:street,
            city=:city,
            state=:state,
            zip_code=:zip_code,
            year_built=:year_built,
            sqft=:sqft,
            property_type=:property_type,
            phone_number=:phone_number,
            google_place_id=:google_place_id
        WHERE id=:id AND user_id=:user_id
    """)
    with db.engine.begin() as conn:
        conn.execute(stmt, {**data, "id": id, "user_id": g.current_user['id']})
    return jsonify({"message": "Property updated"})

@bp.route('/properties/<int:property_id>', methods=['DELETE'])
@require_auth
def delete_property(property_id):
    # Check ownership before allowing delete
    property_obj = Property.query.filter_by(id=property_id, user_id=g.current_user['id']).first()
    if not property_obj:
        return jsonify({'error': 'Property not found or access denied'}), 403
        
    with db.engine.begin() as conn:
        result = conn.execute(
            text("DELETE FROM properties WHERE id = :id AND user_id = :user_id RETURNING id"),
            {"id": property_id, "user_id": g.current_user['id']}
        )
        deleted = result.fetchone()
        if deleted:
            return jsonify({"message": "Property deleted", "id": deleted.id}), 200
        else:
            return jsonify({"error": "Property not found"}), 404