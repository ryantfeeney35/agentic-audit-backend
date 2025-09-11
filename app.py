from flask import Flask, jsonify
from flask_cors import CORS
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os

# Load env
load_dotenv()

# Flask config
app = Flask(__name__)
CORS(app)

# DB setup
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)

@app.route('/api/properties', methods=['GET'])
def get_properties():
    with engine.connect() as conn:
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
                "year_built": row.year_built
            }
            for row in result
        ]
        return jsonify(properties)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=True)