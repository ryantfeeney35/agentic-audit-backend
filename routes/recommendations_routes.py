# routes/recommendations_routes.py
from flask import Blueprint, jsonify
from agents.orchestrator import OrchestratorAgent
from models import AuditRecommendation

bp = Blueprint("recommendations", __name__)

@bp.route("/audits/<int:audit_id>/recommendations", methods=["GET"])
def get_recommendations(audit_id):
    existing = AuditRecommendation.query.filter_by(audit_id=audit_id).all()
    if existing:
        return jsonify([serialize_rec(r) for r in existing])
    agent = OrchestratorAgent(audit_id)
    saved = agent.generate_recommendations()
    return jsonify([serialize_rec(r) for r in saved])

@bp.route("/audits/<int:audit_id>/recommendations/regenerate", methods=["POST"])
def regenerate_recommendations(audit_id):
    agent = OrchestratorAgent(audit_id)
    saved = agent.generate_recommendations()
    return jsonify([serialize_rec(r) for r in saved])

def serialize_rec(r):
    return {
        "step_type": r.step_type,
        "summary": r.summary,
        "annual_savings_usd": r.annual_savings_usd,
        "upgrade_cost_usd": r.upgrade_cost_usd,
        "payback_years": r.payback_years,
        "created_at": r.created_at.isoformat()
    }