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


def seed_recommendations(audit_id, items):
    created = []
    for it in items:
        r = AuditRecommendation(audit_id=audit_id, step_type=it.get("step_type", "general"), summary=it.get("summary", ""), payback_years=it.get("payback_years"))
        db.session.add(r)
        created.append(r)
    db.session.commit()
    return created


def test_default_excludes_hidden(client, app):
    with app.app_context():
        audit = Audit.query.first()
        recs = seed_recommendations(audit.id, [
            {"summary": "Visible", "payback_years": 1},
            {"summary": "Hidden", "payback_years": 2},
        ])
        # Mark one hidden
        recs[1].is_hidden = True
        db.session.commit()

    res = client.get(f"/api/audits/1/recommendations")
    assert res.status_code == 200
    data = res.get_json()
    summaries = [r["summary"] for r in data]
    assert "Hidden" not in summaries
    assert "Visible" in summaries


def test_include_hidden_param_shows_hidden(client, app):
    with app.app_context():
        audit = Audit.query.first()
        recs = seed_recommendations(audit.id, [
            {"summary": "Visible2", "payback_years": 1},
            {"summary": "Hidden2", "payback_years": 2},
        ])
        recs[1].is_hidden = True
        db.session.commit()

    res = client.get(f"/api/audits/1/recommendations?include_hidden=true")
    assert res.status_code == 200
    data = res.get_json()
    summaries = [r["summary"] for r in data]
    assert "Hidden2" in summaries
    assert "Visible2" in summaries


def test_patch_toggle_is_hidden(client, app):
    with app.app_context():
        audit = Audit.query.first()
        recs = seed_recommendations(audit.id, [{"summary": "ToggleMe", "payback_years": 1}])
        rec_id = recs[0].id

    # Hide it
    res = client.patch(f"/api/audits/1/recommendations/{rec_id}", json={"is_hidden": True})
    assert res.status_code == 200
    data = res.get_json()
    assert data.get("is_hidden") is True

    # Default list should now exclude it
    res2 = client.get(f"/api/audits/1/recommendations")
    data2 = res2.get_json()
    summaries = [r["summary"] for r in data2]
    assert "ToggleMe" not in summaries
