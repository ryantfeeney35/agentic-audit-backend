from flask import Flask, jsonify
import os
port = int(os.environ.get("PORT", 5000))

app = Flask(__name__)
app.run(host='0.0.0.0', port=port)

@app.route('/api/properties', methods=['GET'])
def get_properties():
    return jsonify([
        {"id": 1, "address": "123 Main St", "sqft": 1500},
        {"id": 2, "address": "456 Oak Ave", "sqft": 1800}
    ])

if __name__ == '__main__':
    app.run(debug=True, port=5000)