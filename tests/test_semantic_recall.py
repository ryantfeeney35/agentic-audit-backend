import pytest
from flask import Flask

from models import db, Property, Audit, AuditStep


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)
    with app.app_context():
        db.create_all()
        prop = Property(street="2 Test St", city="Town", state="CA", zip_code="90000", year_built=1995, sqft=1200, property_type="single_family")
        db.session.add(prop); db.session.commit()
        audit = Audit(property_id=prop.id, notes="Owner mentions drafts")
        db.session.add(audit); db.session.commit()
        step = AuditStep(audit_id=audit.id, step_type="hvac", label="Furnace", status="Completed")
        step.summary = "Single-stage furnace from 1998"
        step.ai_summary = {"efficiency": "low"}
        db.session.add(step); db.session.commit()
    yield app


@pytest.fixture
def ctx(app):
    with app.app_context():
        yield


def test_semantic_snippets_appended(monkeypatch, ctx):
    from agents.context_builder import get_audit_memory_context
    # Disable chat memory noise; enable semantic recall
    monkeypatch.setattr("memory.config.memory_enabled", lambda: False)
    monkeypatch.setattr("memory.config.semantic_enabled", lambda: True)
    monkeypatch.setattr("memory.config.semantic_top_k", lambda: 3)
    # Mock retrieval to avoid OpenAI and DB reads
    monkeypatch.setattr("memory.semantic.retrieve_relevant_snippets", lambda audit_id, q, k=None: [
        "hvac efficiency: low",
        "insulation depth: 8 inches",
    ])

    out = get_audit_memory_context(1)
    assert "Relevant prior findings:" in out
    assert "hvac efficiency: low" in out
