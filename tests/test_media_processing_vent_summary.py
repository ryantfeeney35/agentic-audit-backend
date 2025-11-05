import os
import json
import tempfile
import base64
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
        step = AuditStep(audit_id=audit.id, step_type="exterior", label="North")
        db.session.add(step)
        db.session.commit()
    yield app


def test_media_processing_vent_summary(app, monkeypatch):
    fake_reply = {
        "orientation": "North",
        "siding_type": "Stucco",
        "shading": "Partial",
        "glass_wall_ratio": "Low",
        "ea_analysis": "OK",
        "summary": "North side assessed.",
        "vent_assessment": {
            "detected_vents": [
                {
                    "type": "gable",
                    "function": "exhaust",
                    "location": "gable",
                    "condition": "good",
                    "notes": "Visible screen, unobstructed",
                    "is_obstructed": False,
                    "confidence": 0.76,
                }
            ],
            "balance": "Appears balanced for visible sections.",
            "moisture_signs": [],
            "issues": [],
            "confidence": 0.8,
            "followup_questions": [],
            "recommendation": "Maintain clear vents; confirm soffit intake continuity."
        },
    }

    # Patch LLM used by agents.base_agent
    monkeypatch.setattr(
        "agents.base_agent.llm",
        type(
            "DummyLLM",
            (),
            {
                "invoke": staticmethod(lambda msgs: type("R", (), {"content": json.dumps(fake_reply)})())
            },
        ),
    )

    # Patch OpenAI client used inside media_routes to avoid network calls during caption/embedding
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

    # Patch requests.get reachability check to a 200
    monkeypatch.setattr("routes.media_routes.requests.get", lambda url, timeout=5: type("R", (), {"status_code": 200})())

    with app.app_context():
        audit = Audit.query.first()
        step = AuditStep.query.filter_by(audit_id=audit.id).first()

        # Create a temp image file and corresponding AuditMedia row
        fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
        os.close(fd)
        with open(tmp_path, "wb") as f:
            f.write(b"fakeimagebytes")

        media = AuditMedia(
            audit_id=audit.id,
            step_id=step.id,
            media_url="https://example.com/fake.jpg",
            file_name="fake.jpg",
            media_type="photo",
        )
        db.session.add(media)
        db.session.commit()

        # Act: run processor synchronously
        process_media_async(app, media.id, tmp_path, media.media_url, media_type="photo")

        # Reload step and assert
        step2 = AuditStep.query.get(step.id)
        assert step2.status == "Completed"
        assert step2.ai_summary is not None
        # ai_summary stored as JSON (dict); ensure vent_assessment exists
        va = step2.ai_summary.get("vent_assessment") if isinstance(step2.ai_summary, dict) else None
        assert va is not None
        assert isinstance(va.get("detected_vents"), list)
