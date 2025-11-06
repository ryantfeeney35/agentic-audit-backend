import os
import json
import tempfile
import pytest
from flask import Flask
from models import db, Property, Audit, AuditStep, AuditMedia
from routes.media_routes import process_media_async


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
    yield app


def test_roof_step_create_and_status_transition(app, monkeypatch):
    # Patch LLM used by base_agent for photo parsing
    fake_reply = {
        "finish_type": "Composition Shingle",
        "color": "dark",
        "visible_vents": [],
        "shading": "Partial",
        "condition_issues": [],
        "summary": "Roof documented",
        "followup_questions": [],
        "recommendation": "Maintain clear intake/exhaust",
        "confidence": 0.8,
    }
    monkeypatch.setattr(
        "agents.base_agent.llm",
        type("DummyLLM", (), {"invoke": staticmethod(lambda msgs: type("R", (), {"content": json.dumps(fake_reply)})())}),
    )

    # Patch OpenAI used for caption/embeddings to a no-op dummy
    class _DummyEmbData:
        def __init__(self):
            self.data = [type("E", (), {"embedding": [0.0, 0.0, 0.0]})()]

    class _DummyChatChoices:
        def __init__(self):
            self.choices = [type("C", (), {"message": type("M", (), {"content": "test caption"})()})]

    class _DummyOpenAI:
        def __init__(self, api_key=None):
            self.embeddings = type("Emb", (), {"create": lambda self2, model, input: _DummyEmbData()})()
            self.chat = type("Chat", (), {"completions": type("Comp", (), {"create": lambda self3, model, messages: _DummyChatChoices()})()})()

    monkeypatch.setattr("routes.media_routes.OpenAI", _DummyOpenAI)
    monkeypatch.setattr("routes.media_routes.requests.get", lambda url, timeout=5: type("R", (), {"status_code": 200})())

    with app.app_context():
        audit = Audit.query.first()

        # Create roof step
        step = AuditStep(audit_id=audit.id, step_type="roof", label="Roof", status="Not Started")
        db.session.add(step)
        db.session.commit()

        # Create a temp image and corresponding media row
        fd, tmp_path = tempfile.mkstemp(suffix=".jpg"); os.close(fd)
        with open(tmp_path, "wb") as f:
            f.write(b"fakeimagebytes")

        media = AuditMedia(
            audit_id=audit.id,
            step_id=step.id,
            media_url="https://example.com/roof.jpg",
            file_name="roof.jpg",
            media_type="photo",
        )
        db.session.add(media)
        db.session.commit()

        # Process media
        process_media_async(app, media.id, tmp_path, media.media_url, media_type="photo")

        step2 = AuditStep.query.get(step.id)
        assert step2.status == "Completed"
        assert isinstance(step2.ai_summary, dict)
