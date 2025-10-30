import pytest
from flask import Flask
from models import db, AuditRecommendation, Audit, Property
from routes.recommendations_routes import bp as recs_bp


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)
    with app.app_context():
        db.create_all()
        prop = Property(street="123 Main", city="SD", state="CA", zip_code="92101", year_built=2000, sqft=1500, property_type="house")
        db.session.add(prop)
        db.session.commit()
        audit = Audit(property_id=prop.id)
        db.session.add(audit)
        db.session.commit()

    app.register_blueprint(recs_bp, url_prefix="/api")
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


def seed_recs(audit_id):
    a = AuditRecommendation(audit_id=audit_id, step_type="insulation", summary="Audio rec", source="audio")
    b = AuditRecommendation(audit_id=audit_id, step_type="hvac", summary="AI rec", source="ai")
    db.session.add_all([a, b])
    db.session.commit()


def test_get_all_by_default(client, app):
    with app.app_context():
        audit = Audit.query.first()
        seed_recs(audit.id)

    res = client.get(f"/api/audits/1/recommendations")
    assert res.status_code == 200
    data = res.get_json()
    assert any(r.get("source") == "audio" for r in data)
    assert any(r.get("source") == "ai" for r in data)


def test_filter_audio_only(client, app):
    with app.app_context():
        audit = Audit.query.first()
        seed_recs(audit.id)

    res = client.get(f"/api/audits/1/recommendations?source=audio")
    assert res.status_code == 200
    data = res.get_json()
    assert all(r.get("source") == "audio" for r in data)


def test_filter_ai_only(client, app):
    with app.app_context():
        audit = Audit.query.first()
        seed_recs(audit.id)

    res = client.get(f"/api/audits/1/recommendations?source=ai")
    assert res.status_code == 200
    data = res.get_json()
    assert all(r.get("source") == "ai" for r in data)
