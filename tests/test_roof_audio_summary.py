import os
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
        step = AuditStep(audit_id=audit.id, step_type="roof", label="Roof")
        db.session.add(step)
        db.session.commit()
    yield app


def test_roof_audio_transcription_and_summary(app, monkeypatch):
    # Patch OpenAI transcription and chat completion
    class _DummyTranscription:
        def __init__(self):
            self.text = "Roof notes: visible ridge vent, some tree debris"

    class _DummyAudio:
        def __init__(self):
            self.transcriptions = type("Tr", (), {"create": lambda self2, model, file: _DummyTranscription()})()

    class _DummyChatChoice:
        def __init__(self):
            self.choices = [type("C", (), {"message": type("M", (), {"content": "Roof summary: ridge vent present; recommend clearing debris"})()})]

    class _DummyChat:
        def __init__(self):
            self.completions = type("Comp", (), {"create": lambda self3, model, messages: _DummyChatChoice()})()

    class _DummyOpenAI:
        def __init__(self, api_key=None):
            self.audio = _DummyAudio()
            self.chat = _DummyChat()

    monkeypatch.setattr("routes.media_routes.OpenAI", _DummyOpenAI)

    with app.app_context():
        audit = Audit.query.first()
        step = AuditStep.query.filter_by(audit_id=audit.id, step_type="roof").first()

        # Make a temp audio file and media row
        fd, tmp_path = tempfile.mkstemp(suffix=".m4a"); os.close(fd)
        with open(tmp_path, "wb") as f:
            f.write(b"fakeaudio")

        media = AuditMedia(
            audit_id=audit.id,
            step_id=step.id,
            media_url="https://example.com/audio.m4a",
            file_name="audio.m4a",
            media_type="audio",
        )
        db.session.add(media)
        db.session.commit()

        process_media_async(app, media.id, tmp_path, media.media_url, media_type="audio")

        step2 = AuditStep.query.get(step.id)
        assert step2.status == "Completed"
        assert isinstance(step2.summary, str) and len(step2.summary) > 0
