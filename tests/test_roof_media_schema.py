import json
import base64
from agents.base_agent import run_agent


def _fake_image_b64():
    return base64.b64encode(b"fakebytes").decode("utf-8")


def test_roof_media_schema_parsing(monkeypatch):
    # Arrange: LLM returns valid RoofMediaSchema JSON
    fake_reply = {
        "finish_type": "Composition Shingle",
        "color": "dark",
        "visible_vents": [
            {
                "type": "ridge",
                "function": "exhaust",
                "location": "ridge",
                "condition": "good",
                "notes": None,
                "is_obstructed": False,
                "confidence": 0.88,
            }
        ],
        "shading": "Partial shading from trees",
        "condition_issues": [],
        "summary": "Asphalt shingle roof, dark color, ridge exhaust visible.",
        "followup_questions": [],
        "recommendation": "Maintain clear ridge vent; inspect soffit intakes at eaves.",
        "confidence": 0.9,
    }

    monkeypatch.setattr(
        "agents.base_agent.llm",
        type(
            "DummyLLM",
            (),
            {"invoke": staticmethod(lambda msgs: type("R", (), {"content": json.dumps(fake_reply)})())},
        ),
    )

    # Act
    ctx = {"type": "image_batch", "images": [{"b64": _fake_image_b64(), "file_name": "roof.jpg"}]}
    parsed = run_agent(domain="roof", mode="media", context=ctx)

    # Assert
    assert parsed["finish_type"].lower().startswith("composition") or parsed["finish_type"]; assert isinstance(parsed["color"], str)
    assert isinstance(parsed.get("visible_vents"), list)
    assert parsed.get("confidence") >= 0 and parsed.get("confidence") <= 1
    assert isinstance(parsed.get("recommendation"), str)
