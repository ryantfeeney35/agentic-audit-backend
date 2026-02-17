from .auth_routes import bp as auth_bp
from .agent_routes import bp as agent_bp
from .property_routes import bp as property_bp
from .audit_routes import bp as audit_bp
from .step_routes import bp as step_bp
from .media_routes import bp as media_bp
from .recommendations_routes import bp as recommendations_bp
from .contractor_routes import bp as contractor_bp
from .report_routes import bp as report_bp
from .utility_routes import utility_bp, audit_utility_bp
from .webhook_routes import webhook_bp
from .measurement_routes import bp as measurement_bp

def register_blueprints(app):
    app.register_blueprint(auth_bp, url_prefix="/api")
    app.register_blueprint(agent_bp, url_prefix="/api")
    app.register_blueprint(property_bp, url_prefix="/api")
    app.register_blueprint(audit_bp, url_prefix="/api")
    app.register_blueprint(step_bp, url_prefix="/api")
    app.register_blueprint(media_bp, url_prefix="/api")
    app.register_blueprint(recommendations_bp, url_prefix="/api")
    app.register_blueprint(contractor_bp, url_prefix="/api")
    app.register_blueprint(report_bp, url_prefix="/api")
    app.register_blueprint(measurement_bp, url_prefix="/api")
    # Utility routes - note: utility_bp has its own prefix /api/utility
    app.register_blueprint(utility_bp)
    app.register_blueprint(audit_utility_bp)
    # Webhook routes - publicly accessible, no /api prefix
    app.register_blueprint(webhook_bp)
