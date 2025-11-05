import json
from agents.base_agent import run_agent


def _mk_ctx():
    return {"type": "image_batch", "images": [{"b64": "ZmFrZQ==", "file_name": "r1.jpg"}]}


def test_roof_followups_gated_by_confidence_low(monkeypatch):
    low_conf = {
        "finish_type": "Tile",
        "color": "light",
        "visible_vents": [],
        "shading": "None",
        "condition_issues": ["Limited visibility"],
        "summary": "Tile roof, vents not clearly visible",
        "followup_questions": [
            "Confirm presence of soffit intake at eaves",
            "Is there continuous ridge exhaust across the main spans?",
        ],
        "recommendation": "Verify intake/exhaust continuity; clear debris if present.",
        "confidence": 0.4,
    }

    monkeypatch.setattr(
        "agents.base_agent.llm",
        type("DummyLLM", (), {"invoke": staticmethod(lambda msgs: type("R", (), {"content": json.dumps(low_conf)})())}),
    )
    parsed = run_agent(domain="roof", mode="media", context=_mk_ctx())
    assert parsed["confidence"] == 0.4
    assert len(parsed["followup_questions"]) >= 1


def test_roof_followups_gated_by_confidence_high(monkeypatch):
    high_conf = {
        "finish_type": "Metal",
        "color": "medium",
        "visible_vents": [
            {
                "type": "ridge",
                "function": "exhaust",
                "location": "ridge",
                "condition": "good",
                "notes": None,
                "is_obstructed": False,
                "confidence": 0.93,
            }
        ],
        "shading": "None",
        "condition_issues": [],
        "summary": "Standing seam roof with ridge vent visible.",
        "followup_questions": [],
        "recommendation": "Maintain clear ridge; verify soffit intake continuity.",
        "confidence": 0.92,
    }

    monkeypatch.setattr(
        "agents.base_agent.llm",
        type("DummyLLM", (), {"invoke": staticmethod(lambda msgs: type("R", (), {"content": json.dumps(high_conf)})())}),
    )
    parsed = run_agent(domain="roof", mode="media", context=_mk_ctx())
    assert parsed["confidence"] == 0.92
    assert parsed["followup_questions"] == []
