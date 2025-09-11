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
            SELECT id, street, city, state, zip_code, year_built FROM properties
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

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=True)