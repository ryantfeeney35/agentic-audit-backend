import json
import pytest
from flask import Flask
from models import db, AgentConversation, Audit, Property, AuditStep
from routes.agent_routes import bp as agent_bp

@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)
    with app.app_context():
        db.create_all()
        # Seed property + audit
        prop = Property(street="123 Main", city="SD", state="CA", zip_code="92101", year_built=2000, sqft=1500)
        db.session.add(prop)
        db.session.commit()
        audit = Audit(property_id=prop.id, notes="Owner reports drafts in attic")
        db.session.add(audit)
        db.session.commit()
        # Add one step with status Completed
        step = AuditStep(audit_id=audit.id, step_type="insulation", label="Attic", status="Completed", notes="Thin insulation observed")
        db.session.add(step)
        db.session.commit()

    app.register_blueprint(agent_bp, url_prefix="/api")
    yield app

@pytest.fixture
def client(app):
    return app.test_client()

# --- Monkeypatch OpenAI so tests run offline ---
@pytest.fixture(autouse=True)
def patch_openai(monkeypatch):
    class DummyResponse:
        def __init__(self):
            self.choices = [type("c", (), {"message": type("m", (), {"content": "✅ Dummy reply"})()})]
    def fake_call_llm(messages):
        return "✅ Dummy reply"
    monkeypatch.setattr("routes.agent_routes.OrchestratorAgent.run", lambda self, ctx, bootstrap=False: "✅ Dummy reply")
    monkeypatch.setattr("routes.agent_routes.call_llm", lambda messages: "✅ Dummy reply")
    return

# --- Tests ---
def test_agent_review_bootstrap(client):
    res = client.post("/api/agent-review", json={"auditId": 1, "context": "", "bootstrap": True})
    assert res.status_code == 200
    data = res.get_json()
    assert "response" in data
    assert "✅ Dummy reply" in data["response"]

def test_add_and_get_conversation(client):
    # Add a user message
    res = client.post("/api/agent-conversations", json={"audit_id": 1, "role": "user", "content": "Hello!"})
    assert res.status_code == 201
    msg = res.get_json()
    assert msg["content"] == "Hello!"

    # Fetch merged conversation
    res = client.get("/api/agent-conversations/merged", query_string={"audit_id": 1})
    assert res.status_code == 200
    data = res.get_json()
    assert any(m["content"] == "Hello!" for m in data)