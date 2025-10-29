import pytest
from flask import Flask
from models import db, Audit, Property, AuditRecommendation
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

    yield app


def test_two_pass_generation_tags_sources(app, monkeypatch):
    with app.app_context():
        audit = Audit.query.first()

        # Fake run_agent in orchestrator module to return different recommendations depending on context
        import agents.orchestrator as orchestrator_module

        def fake_run_agent(domain, context, audit_id=None, mode=None, **kwargs):
            # Audio pass will pass a dict with type == 'audio_pass'
            if isinstance(context, dict) and context.get("type") == "audio_pass":
                return {"recommendations": [{"summary": f"Audio {domain} rec", "step_type": domain}]}
            # Contextual AI pass returns ai-tagged recommendations
            return {"recommendations": [{"summary": f"AI {domain} rec", "step_type": domain}]}

        monkeypatch.setattr(orchestrator_module, "run_agent", fake_run_agent)

        agent = OrchestratorAgent(audit.id)
        saved = agent.generate_recommendations()

        # Fetch persisted recommendations
        persisted = AuditRecommendation.query.filter_by(audit_id=audit.id).all()
        summaries = [r.summary for r in persisted]
        sources = [r.source for r in persisted]

        # Expect both audio and AI recommendations for each domain (3 domains -> 6 recs)
        assert len(persisted) == 6
        # Check that audio entries are present
        assert any(s.startswith("Audio ") for s in summaries)
        assert any(s.startswith("AI ") for s in summaries)
        # Source column should contain 'audio' and 'ai'
        assert "audio" in sources
        assert "ai" in sources
