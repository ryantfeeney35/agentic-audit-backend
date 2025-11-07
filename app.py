from flask import Flask
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from dotenv import load_dotenv
import os

from models import db
from routes import register_blueprints

# Load environment variables
load_dotenv()

# Create Flask app
app = Flask(__name__)

# Configure DB
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv("DATABASE_URL")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Setup extensions
db.init_app(app)
migrate = Migrate(app, db)
CORS(app)

# Register all blueprints
register_blueprints(app)
# 🔍 Debug: print all registered routes
print("✅ Registered routes:")
with app.app_context():
    for rule in app.url_map.iter_rules():
        print(f"{rule.endpoint:30s} -> {rule}")

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=True)