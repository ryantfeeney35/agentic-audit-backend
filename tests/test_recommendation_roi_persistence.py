import pytest
from flask import Flask
from models import db, AuditRecommendation, Audit, Property, AuditStep
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


def test_patch_roi_inputs_persists(client, app):
    with app.app_context():
        audit = Audit.query.first()
        step = AuditStep(audit_id=audit.id, step_type='insulation', label='Attic')
        db.session.add(step)
        db.session.commit()

        rec = AuditRecommendation(audit_id=audit.id, step_type='Insulation', summary='Test rec')
        db.session.add(rec)
        db.session.commit()

    # send ROI inputs (numeric strings should be coerced)
    payload = {
        "attic_area_sqft": "1200",
        "attic_current_r": "10",
        "attic_target_r": "38",
        "net_upgrade_cost_usd": "2500",
        "some_text": "keep-me",
        "remove_me": ""  # empty string should remove the key
    }

    res = client.patch(f"/api/audits/{audit.id}/recommendations/{rec.id}/roi-inputs", json=payload)
    assert res.status_code == 200
    data = res.get_json()
    # roi_inputs should be present and numbers coerced
    roi = data.get('roi_inputs')
    assert isinstance(roi, dict)
    assert roi.get('attic_area_sqft') == 1200
    assert roi.get('attic_current_r') == 10
    assert roi.get('net_upgrade_cost_usd') == 2500
    assert roi.get('some_text') == 'keep-me'
    assert 'remove_me' not in roi

    # verify DB persisted
    with app.app_context():
        r = AuditRecommendation.query.get(rec.id)
        assert isinstance(r.roi_inputs, dict)
        assert r.roi_inputs.get('attic_area_sqft') == 1200
