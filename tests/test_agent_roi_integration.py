import json
import pytest


def test_agent_roi_integration_insulation(monkeypatch):
    # Monkeypatch the chat call to return a single attic insulation recommendation
    import agents.base_agent as base_agent_module

    def fake_chat(messages, domain):
        # Return a minimal AgentOutput JSON payload
        payload = {
            "summary": None,
            "followup_questions": [],
            "recommendations": [
                {
                    "step_type": "Insulation",
                    "summary": "Upgrade attic insulation to R-38; estimated net cost $2500",
                }
            ],
        }
        return json.dumps(payload)

    monkeypatch.setattr(base_agent_module, "run_orchestrator_chat", fake_chat)

    # Provide context with ROI inputs
    context = {
        "attic_area_sqft": 1200,
        "attic_current_r": 13,
        "attic_target_r": 38,
        "energy_rate_usd_per_kwh": 0.20,
        "net_upgrade_cost_usd": 2500,
        "analysis_horizon_years": 25,
        "climate": "mild",
    }

    out = base_agent_module.run_agent("insulation", context, mode="recommendations")

    recs = out.get("recommendations") or []
    assert len(recs) == 1
    r0 = recs[0]
    assert abs(float(r0.get("annual_savings_usd")) - 108.0) <= 0.01
    assert abs(float(r0.get("payback_years")) - 23.15) <= 0.05
    assert float(r0.get("upgrade_cost_usd")) == 2500
