import pytest
from flask import Flask

from models import db, Property, Audit


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    with app.app_context():
        db.create_all()
        prop = Property(street="3 Test St", city="Town", state="CA", zip_code="90000", year_built=2000, sqft=1400, property_type="single_family")
        db.session.add(prop); db.session.commit()
        audit = Audit(property_id=prop.id, notes="Notes")
        db.session.add(audit); db.session.commit()
    yield app


@pytest.fixture
def ctx(app):
    with app.app_context():
        yield


def test_base_agent_persists_to_memory_when_enabled(monkeypatch, ctx):
    calls = []
    # Enable memory, intercept save_message
    monkeypatch.setattr("memory.config.memory_enabled", lambda: True)
    monkeypatch.setattr("memory.chat_memory.save_message", lambda audit_id, domain, role, content: calls.append((audit_id, domain, role, content)))
    # Monkeypatch LLM to return a deterministic JSON that matches parser format
    from agents import base_agent
    monkeypatch.setattr(base_agent, "run_orchestrator_chat", lambda messages, domain: '{"summary": "ok", "followup_questions": [], "recommendations": []}')

    # Call run_agent in an interactive mode with audit_id
    base_agent.run_agent("insulation", "context", bootstrap=True, audit_id=1)

    # Should have 3 memory writes: system, user, assistant
    roles = [r for (_, _, r, _) in calls]
    assert roles.count("system") == 1
    assert roles.count("user") == 1
    assert roles.count("assistant") == 1
