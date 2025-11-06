import json
from agents.base_agent import run_agent


def _mk_ctx():
    return {"type": "image_batch", "images": [{"b64": "ZmFrZQ==", "file_name": "a.jpg"}]}


def test_followups_gated_by_confidence_low(monkeypatch):
    low_conf = {
        "orientation": "East",
        "siding_type": "Wood",
        "shading": "None",
        "glass_wall_ratio": "High",
        "ea_analysis": "High solar exposure",
        "summary": "East side with high glazing",
        "vent_assessment": {
            "detected_vents": [],
            "balance": "Unknown",
            "moisture_signs": [],
            "issues": ["Limited visibility"],
            "confidence": 0.55,
            "followup_questions": [
                "Confirm presence of continuous soffit intake",
                "Is there ridge venting along the full span?",
            ],
            "recommendation": "Verify intake and exhaust continuity before sealing upgrades."
        },
    }
    monkeypatch.setattr(
        "agents.base_agent.llm",
        type("DummyLLM", (), {"invoke": staticmethod(lambda msgs: type("R", (), {"content": json.dumps(low_conf)})())}),
    )
    parsed = run_agent(domain="exterior", mode="media", context=_mk_ctx())
    assert parsed["vent_assessment"]["confidence"] == 0.55
    assert len(parsed["vent_assessment"]["followup_questions"]) >= 1


def test_followups_gated_by_confidence_high(monkeypatch):
    high_conf = {
        "orientation": "West",
        "siding_type": "Vinyl",
        "shading": "Partial",
        "glass_wall_ratio": "Medium",
        "ea_analysis": "OK",
        "summary": "West side OK",
        "vent_assessment": {
            "detected_vents": [
                {
                    "type": "ridge",
                    "function": "exhaust",
                    "location": "ridge",
                    "condition": "good",
                    "notes": None,
                    "is_obstructed": False,
                    "confidence": 0.9,
                }
            ],
            "balance": "Balanced",
            "moisture_signs": [],
            "issues": [],
            "confidence": 0.9,
            "followup_questions": [],
            "recommendation": "Maintain clear exhaust at ridge; inspect soffit intakes annually."
        },
    }
    monkeypatch.setattr(
        "agents.base_agent.llm",
        type("DummyLLM", (), {"invoke": staticmethod(lambda msgs: type("R", (), {"content": json.dumps(high_conf)})())}),
    )
    parsed = run_agent(domain="exterior", mode="media", context=_mk_ctx())
    assert parsed["vent_assessment"]["confidence"] == 0.9
    assert parsed["vent_assessment"]["followup_questions"] == []
