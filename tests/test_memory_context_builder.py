import pytest
from flask import Flask

from models import db, Property, Audit, AuditStep


@pytest.fixture
def app(monkeypatch):
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)
    with app.app_context():
        db.create_all()
        prop = Property(street="1 Test St", city="Town", state="CA", zip_code="90000", year_built=1990, sqft=1000, property_type="single_family")
        db.session.add(prop); db.session.commit()
        audit = Audit(property_id=prop.id, notes="Interview: prefers cooler bedrooms")
        db.session.add(audit); db.session.commit()
        step = AuditStep(audit_id=audit.id, step_type="insulation", label="Attic", status="Completed")
        step.summary = "Loose-fill insulation observed"
        step.ai_summary = {"depth_inches": 8, "condition": "thin"}
        db.session.add(step); db.session.commit()

    yield app


@pytest.fixture
def ctx(app):
    with app.app_context():
        yield


def test_get_audit_memory_context_filters_orchestrator_assistant(monkeypatch, ctx):
    # Enable memory and inject recent messages (no real PG dependency)
    from agents.context_builder import get_audit_memory_context
    monkeypatch.setattr("memory.config.memory_enabled", lambda: True)
    monkeypatch.setattr("memory.config.semantic_enabled", lambda: False)
    # Emulate 3 messages including an orchestrator assistant one that should be filtered
    msgs = [
        "[assistant] [orchestrator] This is a system summary (should be filtered)",
        "[user] [insulation] What R-value?",
        "[ai] [insulation] Please check attic hatches",
    ]
    monkeypatch.setattr("memory.chat_memory.get_recent_messages", lambda audit_id, limit=12: msgs)

    out = get_audit_memory_context(1)
    assert "Property:" in out
    assert "Recent discussion:" in out
    assert "orchestrator" not in out  # filtered
    assert "What R-value?" in out


def test_get_audit_memory_context_truncation(monkeypatch, ctx):
    from agents.context_builder import get_audit_memory_context
    monkeypatch.setattr("memory.config.memory_enabled", lambda: True)
    monkeypatch.setattr("memory.config.semantic_enabled", lambda: False)
    # Make a huge message list to force truncation
    huge = [f"[user] [insulation] line {i}: " + ("x" * 1000) for i in range(50)]
    monkeypatch.setattr("memory.chat_memory.get_recent_messages", lambda aid, limit=12: huge)
    monkeypatch.setattr("memory.config.context_char_cap", lambda: 2000)

    out = get_audit_memory_context(1)
    assert len(out) <= 2015  # includes truncation marker
    assert out.endswith("... [truncated]")
