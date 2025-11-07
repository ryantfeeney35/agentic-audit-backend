import json
import base64
from agents.base_agent import run_agent


def _fake_image_b64():
    return base64.b64encode(b"fakebytes").decode("utf-8")


def test_interior_media_schema_parsing(monkeypatch):
    # Arrange: monkeypatch LLM to return valid InteriorRoomSchema JSON including new fields
    fake_reply = {
        "room_type": "Bedroom",
        "ceiling_height": "8 ft",
        "ceiling_material": "Drywall",
        "knee_wall_present": True,
        "wall_to_glass_ratio": 0.25,
        "ea_analysis": "Finished attic space with knee walls; modest glazing area relative to walls.",
        "summary": "Bedroom with standard ceiling height; knee walls present; approximately 25% wall-to-glass ratio.",
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
    ctx = {"type": "image_batch", "images": [{"b64": _fake_image_b64(), "file_name": "room.jpg"}]}
    parsed = run_agent(domain="interior", mode="media", context=ctx)

    # Assert
    assert parsed["room_type"] == "Bedroom"
    assert parsed["ceiling_material"] == "Drywall"
    assert parsed.get("knee_wall_present") is True
    assert isinstance(parsed.get("wall_to_glass_ratio"), float)
    assert 0.0 <= parsed.get("wall_to_glass_ratio") <= 1.0
    assert isinstance(parsed.get("summary"), str)
