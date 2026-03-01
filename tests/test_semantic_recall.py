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
        # Create a test user first
        from models import User
        user = User(id="test-user-1", email="test@example.com")
        db.session.add(user)
        db.session.commit()
        
        prop = Property(user_id=user.id, street="2 Test St", city="Town", state="CA", zip_code="90000", year_built=1995, sqft=1200, property_type="single_family")
        db.session.add(prop); db.session.commit()
        audit = Audit(property_id=prop.id, user_id=user.id, notes="Owner mentions drafts")
        db.session.add(audit); db.session.commit()
        step = AuditStep(audit_id=audit.id, user_id=user.id, step_type="hvac", label="Furnace", status="Completed")
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
    # Enable memory (required to reach semantic recall section) but mock chat messages
    # Patch where functions are used, not where they're defined
    monkeypatch.setattr("agents.context_builder.memory_enabled", lambda: True)
    monkeypatch.setattr("agents.context_builder.get_recent_messages", lambda audit_id, limit=12: [])
    monkeypatch.setattr("agents.context_builder.semantic_enabled", lambda: True)
    monkeypatch.setattr("agents.context_builder.semantic_top_k", lambda: 3)
    # Mock retrieval to avoid OpenAI and DB reads
    monkeypatch.setattr("agents.context_builder.retrieve_relevant_snippets", lambda audit_id, q, k=None: [
        "hvac efficiency: low",
        "insulation depth: 8 inches",
    ])

    out = get_audit_memory_context(1)
    assert "Relevant prior findings:" in out
    assert "hvac efficiency: low" in out
