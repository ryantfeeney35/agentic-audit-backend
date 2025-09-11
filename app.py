from flask import Flask, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from dotenv import load_dotenv
import os
from sqlalchemy import text
from models import db

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

# Sample route: Get properties
@app.route('/api/properties', methods=['GET'])
def get_properties():
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
    with engine.connect() as conn:
        conn.execute(stmt, {**data, "id": id})
        conn.commit()
    return jsonify({"message": "Property updated"})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=True)