import pytest
from flask import Flask
from models import db, AuditRecommendation, Audit, Property, AuditMedia, AuditStep
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


def test_patch_recommended_media(client, app):
    with app.app_context():
        audit = Audit.query.first()
        # create a step
        step = AuditStep(audit_id=audit.id, step_type='insulation', label='Attic')
        db.session.add(step)
        db.session.commit()

        media = AuditMedia(audit_id=audit.id, step_id=step.id, media_url='https://example.com/photo.jpg', file_name='photo.jpg', media_type='photo')
        db.session.add(media)
        db.session.commit()

        rec = AuditRecommendation(audit_id=audit.id, step_type='Insulation', summary='Test rec')
        db.session.add(rec)
        db.session.commit()

    # patch to associate media
    res = client.patch(f"/api/audits/{audit.id}/recommendations/{rec.id}/recommended_media", json={"recommended_media_id": media.id, "source": "auditor"})
    assert res.status_code == 200
    data = res.get_json()
    assert data.get('recommended_media_id') == media.id
    assert data.get('recommended_media_source') == 'auditor'

    # clear
    res2 = client.patch(f"/api/audits/{audit.id}/recommendations/{rec.id}/recommended_media", json={"recommended_media_id": None})
    assert res2.status_code == 200
    data2 = res2.get_json()
    assert data2.get('recommended_media_id') is None
