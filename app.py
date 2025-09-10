from flask import Flask, jsonify
from flask_cors import CORS
import os

app = Flask(__name__)
CORS(app)  # Apply CORS middleware BEFORE running the app

# Sample GET route
@app.route('/api/properties', methods=['GET'])
def get_properties():
    return jsonify([
        {"id": 1, "address": "123 Main St", "sqft": 1500},
        {"id": 2, "address": "456 Oak Ave", "sqft": 1800}
    ])

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(debug=True, host='0.0.0.0', port=port)