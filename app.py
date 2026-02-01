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

# Configure UtilityAPI webhook secret (for webhook signature verification)
app.config['UTILITYAPI_WEBHOOK_SECRET'] = os.getenv('UTILITYAPI_WEBHOOK_SECRET')

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

# ---------------------------------
# SQLAlchemy session hygiene hooks
# ---------------------------------
@app.before_request
def reset_failed_transaction_state():
    """Ensure any failed transaction from a previous operation doesn't poison the new request.

    In Postgres, once a statement fails, the transaction enters an aborted state and further
    commands will error until a ROLLBACK is issued. This hook proactively rolls back the
    session at the start of each request to guarantee a clean slate.
    """
    try:
        db.session.rollback()
    except Exception:
        # If the session isn't started yet or rollback isn't applicable, ignore.
        pass


@app.teardown_request
def remove_session_on_teardown(exception=None):
    """Remove the scoped session after each request to prevent session leakage across requests."""
    try:
        if exception:
            # If an exception occurred, rollback to clear aborted state.
            db.session.rollback()
        # Always remove the session at the end of the request lifecycle.
        db.session.remove()
    except Exception:
        # Avoid raising during teardown
        pass

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=True)