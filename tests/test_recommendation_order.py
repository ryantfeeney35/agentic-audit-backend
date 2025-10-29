import json
import pytest
from flask import Flask
from models import db, AuditRecommendation, Audit, Property
from routes.recommendations_routes import bp as recs_bp
from agents.orchestrator import OrchestratorAgent


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
    """Helper to create AuditRecommendation rows.

    items: list of dicts with keys: summary, payback_years
    Returns list of created AuditRecommendation objects.
    """
    created = []
    for it in items:
        r = AuditRecommendation(audit_id=audit_id, step_type=it.get("step_type", "general"), summary=it.get("summary", ""), payback_years=it.get("payback_years"))
        db.session.add(r)
        created.append(r)
    db.session.commit()
    return created


def test_get_defaults_to_payback_when_no_display_order(client, app):
    with app.app_context():
        audit = Audit.query.first()
        seed_recommendations(audit.id, [
            {"summary": "A", "payback_years": 5},
            {"summary": "B", "payback_years": 2},
            {"summary": "C", "payback_years": 10},
        ])

    res = client.get(f"/api/audits/1/recommendations")
    assert res.status_code == 200
    data = res.get_json()
    # Expect order by payback_years ascending: B (2), A (5), C (10)
    summaries = [r["summary"] for r in data]
    assert summaries == ["B", "A", "C"]


def test_patch_order_persists_and_get_reflects_order(client, app):
    with app.app_context():
        audit = Audit.query.first()
        recs = seed_recommendations(audit.id, [
            {"summary": "One", "payback_years": 3},
            {"summary": "Two", "payback_years": 1},
            {"summary": "Three", "payback_years": 2},
        ])
        ids = [r.id for r in recs]

    # Reverse order
    new_order = list(reversed(ids))
    res = client.patch(f"/api/audits/1/recommendations/order", json={"order": new_order})
    assert res.status_code == 200
    data = res.get_json()
    returned_ids = [r.get("id") if r.get("id") is not None else None for r in data]
    # Our serialize_rec doesn't include id currently, but order should match summaries
    summaries = [r["summary"] for r in data]
    # Should be Three, Two, One
    assert summaries == ["Three", "Two", "One"]

    # GET should reflect same order
    res2 = client.get(f"/api/audits/1/recommendations")
    data2 = res2.get_json()
    summaries2 = [r["summary"] for r in data2]
    assert summaries2 == ["Three", "Two", "One"]


def test_patch_order_invalid_ids_returns_400(client, app):
    with app.app_context():
        audit = Audit.query.first()
        seed_recommendations(audit.id, [{"summary": "Only", "payback_years": 1}])

    # Pass an id that does not belong to audit (e.g., 999)
    res = client.patch(f"/api/audits/1/recommendations/order", json={"order": [999]})
    assert res.status_code == 400


def test_regenerate_clears_display_order(client, app, monkeypatch):
    with app.app_context():
        audit = Audit.query.first()
        recs = seed_recommendations(audit.id, [
            {"summary": "X", "payback_years": 4},
            {"summary": "Y", "payback_years": 6},
        ])
        # manually set display_order
        for idx, r in enumerate(recs):
            r.display_order = idx
        db.session.commit()

    # Patch OrchestratorAgent.generate_recommendations to create new recommendations (simulate regeneration)
    def fake_generate(self):
        # Clear existing and write new ones (mimic real behavior)
        AuditRecommendation.query.filter_by(audit_id=self.audit_id).delete()
        new = [AuditRecommendation(audit_id=self.audit_id, step_type="general", summary="New1", payback_years=2), AuditRecommendation(audit_id=self.audit_id, step_type="general", summary="New2", payback_years=3)]
        for r in new:
            db.session.add(r)
        db.session.commit()
        return new

    monkeypatch.setattr(OrchestratorAgent, "generate_recommendations", fake_generate)

    res = client.post(f"/api/audits/1/recommendations/regenerate")
    assert res.status_code == 200
    data = res.get_json()
    summaries = [r["summary"] for r in data]
    assert summaries == ["New1", "New2"]
    # Ensure none have display_order set
    with app.app_context():
        all_recs = AuditRecommendation.query.filter_by(audit_id=1).all()
        assert all(r.display_order is None for r in all_recs)


def test_patch_order_transactional_on_partial_failure(client, app, monkeypatch):
    with app.app_context():
        audit = Audit.query.first()
        recs = seed_recommendations(audit.id, [
            {"summary": "A", "payback_years": 1},
            {"summary": "B", "payback_years": 2},
        ])
        # set initial display order
        for idx, r in enumerate(recs):
            r.display_order = idx
        db.session.commit()

    # Cause commit to fail
    original_commit = db.session.commit

    def fake_commit():
        raise Exception("simulated DB failure")

    monkeypatch.setattr(db.session, "commit", fake_commit)

    # Attempt to reorder
    ids = [r.id for r in recs]
    res = client.patch(f"/api/audits/1/recommendations/order", json={"order": list(reversed(ids))})
    assert res.status_code == 500

    # Restore commit and ensure original order still present (transaction rolled back)
    monkeypatch.setattr(db.session, "commit", original_commit)
    with app.app_context():
        db.session.expire_all()
        all_recs = AuditRecommendation.query.filter_by(audit_id=1).order_by(AuditRecommendation.id.asc()).all()
        # display_order should be original indices 0 and 1
        assert [r.display_order for r in all_recs] == [0, 1]
