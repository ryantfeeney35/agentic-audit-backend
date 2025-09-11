from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from dotenv import load_dotenv
import os
from sqlalchemy import text
from models import db
from supabase import create_client, Client

# Load environment variables first
load_dotenv()

# Create Flask app
app = Flask(__name__)

# Configure DB from environment
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv("DATABASE_URL")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Setup extensions
db.init_app(app)
migrate = Migrate(app, db)
CORS(app)

from models import Property

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_BUCKET_NAME = os.getenv("SUPABASE_BUCKET_NAME")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# Sample route: Get properties
@app.route('/api/properties', methods=['GET', 'POST'])
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

@app.route('/api/properties/<int:property_id>', methods=['GET'])
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

# PUT /api/properties/<id>
@app.route('/api/properties/<int:id>', methods=['PUT'])
def update_property(id):
    data = request.get_json()
    stmt = text("""
        UPDATE properties
        SET street=:street, city=:city, state=:state, zip_code=:zip_code, year_built=:year_built, sqft=:sqft
        WHERE id=:id
    """)
    with db.engine.connect() as conn:
        conn.execute(stmt, {**data, "id": id})
        conn.commit()
    return jsonify({"message": "Property updated"})

@app.route('/api/properties/<int:property_id>', methods=['DELETE'])
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

@app.route('/api/properties/<int:property_id>/upload-utility-bill', methods=['POST'])
def upload_utility_bill(property_id):
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    filename = f'property_{property_id}_{file.filename}'
    file_content = file.read()

    try:
        supabase.storage.from_(SUPABASE_BUCKET_NAME).upload(
            path=filename,
            file=file_content,
            file_options={"content-type": file.mimetype}
        )
        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET_NAME}/{filename}"

        # Save the public_url to the DB
        property_obj = Property.query.get(property_id)
        if not property_obj:
            return jsonify({"error": "Property not found"}), 404

        property_obj.utility_bill_url = public_url
        db.session.commit()

        return jsonify({'message': 'Uploaded and saved successfully', 'url': public_url}), 200

    except Exception as e:
        print(e)
        return jsonify({'error': 'Upload failed'}), 500
    
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=True)