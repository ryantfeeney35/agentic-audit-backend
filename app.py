import os
import logging
from typing import Optional, Dict

import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
from flask import Flask, jsonify
from flask_cors import CORS
from flask_migrate import Migrate
from dotenv import load_dotenv

from models import db
from routes import register_blueprints

load_dotenv()
migrate = Migrate()

# Initialize Sentry before Flask app creation
sentry_dsn = os.getenv("SENTRY_DSN")
if sentry_dsn:
    sentry_sdk.init(
        dsn=sentry_dsn,
        integrations=[FlaskIntegration()],
        traces_sample_rate=0.1,  # 10% of transactions for performance monitoring
        send_default_pii=False,  # Don't send PII by default
        before_send=lambda event, hint: _scrub_pii(event),
    )


def _scrub_pii(event: dict) -> dict:
    """Scrub PII from Sentry events before sending."""
    # Scrub sensitive headers
    if "request" in event and "headers" in event["request"]:
        sensitive_headers = ["authorization", "cookie", "x-auth-token"]
        for header in sensitive_headers:
            if header in event["request"]["headers"]:
                event["request"]["headers"][header] = "[Filtered]"
    
    # Scrub email patterns from exception values
    if "exception" in event and "values" in event["exception"]:
        import re
        email_pattern = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
        for exc in event["exception"]["values"]:
            if "value" in exc and exc["value"]:
                exc["value"] = email_pattern.sub("[email]", str(exc["value"]))
    
    return event


def _normalize_database_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return url
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def _ensure_sqlite_directory(sqlite_path: str) -> None:
    directory = os.path.dirname(sqlite_path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)


def create_app(test_config: Optional[Dict] = None) -> Flask:
    """Application factory so tests can configure isolated instances."""
    app = Flask(__name__, instance_relative_config=True)

    default_sqlite_path = os.path.join(app.instance_path, "dev.db")
    _ensure_sqlite_directory(default_sqlite_path)
    default_sqlite_uri = f"sqlite:///{default_sqlite_path}"

    app.config.from_mapping(
        SQLALCHEMY_DATABASE_URI=_normalize_database_url(os.getenv("DATABASE_URL")),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        UTILITYAPI_WEBHOOK_SECRET=os.getenv("UTILITYAPI_WEBHOOK_SECRET"),
    )

    if test_config:
        app.config.update(test_config)

    configured_db_uri = app.config.get("SQLALCHEMY_DATABASE_URI") or app.config.get("DATABASE_URL")
    configured_db_uri = _normalize_database_url(configured_db_uri)
    if not configured_db_uri:
        configured_db_uri = default_sqlite_uri
    app.config["SQLALCHEMY_DATABASE_URI"] = configured_db_uri
    os.environ["SQLALCHEMY_DATABASE_URI"] = configured_db_uri
    os.environ["DATABASE_URL"] = configured_db_uri

    db.init_app(app)
    migrate.init_app(app, db)
    CORS(app)
    register_blueprints(app)

    if app.config.get("PRINT_ROUTES_ON_START") and not app.config.get("TESTING"):
        print("✅ Registered routes:")
        with app.app_context():
            for rule in app.url_map.iter_rules():
                print(f"{rule.endpoint:30s} -> {rule}")

    @app.before_request
    def reset_failed_transaction_state():
        """Rollback failed transactions before each request to avoid aborted sessions."""
        try:
            db.session.rollback()
        except Exception:
            pass

    @app.teardown_request
    def remove_session_on_teardown(exception=None):
        """Ensure scoped sessions are cleaned up after each request."""
        try:
            if exception:
                db.session.rollback()
            db.session.remove()
        except Exception:
            pass

    @app.errorhandler(Exception)
    def handle_exception(e):
        """Global exception handler - logs and reports all unhandled exceptions."""
        # Log the full traceback
        logging.exception("Unhandled exception occurred")
        
        # Sentry will automatically capture this via Flask integration
        # but we can also explicitly capture if needed
        if sentry_dsn:
            sentry_sdk.capture_exception(e)
        
        # Return JSON error response
        return jsonify({"error": "Internal server error"}), 500

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
