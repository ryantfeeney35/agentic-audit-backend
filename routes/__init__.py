from .agent_routes import bp as agent_bp
from .property_routes import bp as property_bp
from .audit_routes import bp as audit_bp
from .step_routes import bp as step_bp
from .media_routes import bp as media_bp

def register_blueprints(app):
    app.register_blueprint(agent_bp, url_prefix="/api")
    app.register_blueprint(property_bp, url_prefix="/api")
    app.register_blueprint(audit_bp, url_prefix="/api")
    app.register_blueprint(step_bp, url_prefix="/api")
    app.register_blueprint(media_bp, url_prefix="/api")