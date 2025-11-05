import json
import base64
from agents.base_agent import run_agent


def _fake_image_b64():
    return base64.b64encode(b"fakebytes").decode("utf-8")


def test_exterior_media_schema_parsing(monkeypatch):
    # Arrange: monkeypatch LLM to return valid ExteriorMediaSchema JSON
    fake_reply = {
        "orientation": "North",
        "siding_type": "Stucco",
        "shading": "Partial",
        "glass_wall_ratio": "Medium",
        "ea_analysis": "Moderate exposure; shading reduces solar gain on this side.",
        "summary": "North side with stucco; adequate shading; vents appear balanced.",
        "vent_assessment": {
            "detected_vents": [
                {
                    "type": "soffit",
                    "function": "intake",
                    "location": "eave/soffit",
                    "condition": "good",
                    "notes": "Unpainted, unobstructed",
                    "is_obstructed": False,
                    "confidence": 0.9,
                }
            ],
            "balance": "Intake and exhaust appear reasonably balanced for the visible area.",
            "moisture_signs": [],
            "issues": [],
            "confidence": 0.88,
            "followup_questions": [],
            "recommendation": "Maintain clear soffit intakes; verify ridge exhaust continuity across attic."
        },
    }

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

    # Act
    ctx = {"type": "image_batch", "images": [{"b64": _fake_image_b64(), "file_name": "a.jpg"}]}
    parsed = run_agent(domain="exterior", mode="media", context=ctx)

    # Assert
    assert parsed["orientation"] == "North"
    assert parsed["siding_type"] == "Stucco"
    assert "vent_assessment" in parsed
    va = parsed["vent_assessment"]
    assert isinstance(va.get("detected_vents"), list)
    assert va.get("confidence") >= 0 and va.get("confidence") <= 1
    assert isinstance(va.get("recommendation"), str)
