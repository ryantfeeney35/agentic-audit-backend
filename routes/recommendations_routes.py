# routes/recommendations_routes.py
from flask import Blueprint, jsonify, request, abort
from agents.orchestrator import OrchestratorAgent
from models import AuditRecommendation, db

bp = Blueprint("recommendations", __name__)

@bp.route("/audits/<int:audit_id>/recommendations", methods=["GET"])
def get_recommendations(audit_id):
    # Default behavior: exclude hidden recommendations unless include_hidden=true
    include_hidden = str(request.args.get("include_hidden", "false")).lower() in ("1", "true", "yes")

    # If there are no recommendations at all for the audit, delegate to the agent to generate them.
    total_recs = AuditRecommendation.query.filter_by(audit_id=audit_id).count()
    if total_recs == 0:
        agent = OrchestratorAgent(audit_id)
        saved = agent.generate_recommendations()
        return jsonify([serialize_rec(r) for r in saved])

    # Otherwise, load existing recommendations (respect hidden filter)
    if include_hidden:
        existing = AuditRecommendation.query.filter_by(audit_id=audit_id).all()
    else:
        existing = AuditRecommendation.query.filter_by(audit_id=audit_id, is_hidden=False).all()

    # If any recommendation has a display_order set, respect that ordering.
    if any(r.display_order is not None for r in existing):
        ordered = sorted(existing, key=lambda r: (r.display_order if r.display_order is not None else 999999))
    else:
        # Default to ROI / payback_years ascending when no custom order exists.
        ordered = sorted(existing, key=lambda r: (r.payback_years if r.payback_years is not None else float("inf")))
    return jsonify([serialize_rec(r) for r in ordered])

@bp.route("/audits/<int:audit_id>/recommendations/regenerate", methods=["POST"])
def regenerate_recommendations(audit_id):
    agent = OrchestratorAgent(audit_id)
    saved = agent.generate_recommendations()
    # generate_recommendations clears old recommendations and saves new ones,
    # so returned recommendations will have no display_order set by design.
    return jsonify([serialize_rec(r) for r in saved])


@bp.route("/audits/<int:audit_id>/recommendations/order", methods=["PATCH"])
def patch_recommendations_order(audit_id):
    """Persist a manual ordering for recommendations on an audit.

    Expects JSON: { "order": [<recommendation_id>, ...] }
    """
    payload = request.get_json() or {}
    order = payload.get("order")
    if not isinstance(order, list):
        abort(400, description="Missing or invalid 'order' array in request body")

    # Load the recommendations that belong to this audit and that are referenced.
    recs = AuditRecommendation.query.filter(AuditRecommendation.audit_id == audit_id, AuditRecommendation.id.in_(order)).all()
    rec_map = {r.id: r for r in recs}

    # Validation: ensure all provided ids belong to this audit
    if set(order) != set(rec_map.keys()):
        abort(400, description="One or more recommendation ids are invalid for this audit")

    try:
        # Apply the new ordering (0-based index)
        for idx, rec_id in enumerate(order):
            rec = rec_map.get(rec_id)
            rec.display_order = int(idx)

        # Any other recommendations for this audit that weren't included should have display_order cleared
        other = AuditRecommendation.query.filter(AuditRecommendation.audit_id == audit_id, ~AuditRecommendation.id.in_(order)).all()
        for r in other:
            r.display_order = None

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        abort(500, description=f"Failed to persist recommendation order: {e}")

    # Return updated list sorted by display_order
    updated = AuditRecommendation.query.filter_by(audit_id=audit_id).all()
    ordered = sorted(updated, key=lambda r: (r.display_order if r.display_order is not None else 999999))
    return jsonify([serialize_rec(r) for r in ordered])


@bp.route("/audits/<int:audit_id>/recommendations/<int:rec_id>", methods=["PATCH"])
def patch_recommendation(audit_id, rec_id):
    """Toggle or patch fields on a single recommendation. Currently supports:
    { "is_hidden": true|false }
    """
    payload = request.get_json() or {}
    if 'is_hidden' not in payload:
        abort(400, description="Missing 'is_hidden' in request body")

    is_hidden = bool(payload.get('is_hidden'))
    rec = AuditRecommendation.query.filter_by(id=rec_id, audit_id=audit_id).first()
    if not rec:
        abort(404, description="Recommendation not found for this audit")

    try:
        rec.is_hidden = is_hidden
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        abort(500, description=f"Failed to update recommendation: {e}")

    return jsonify(serialize_rec(rec))

def serialize_rec(r):
    return {
        "id": r.id,
        "step_type": r.step_type,
        "summary": r.summary,
        "annual_savings_usd": r.annual_savings_usd,
        "upgrade_cost_usd": r.upgrade_cost_usd,
        "payback_years": r.payback_years,
        "display_order": r.display_order,
        "is_hidden": bool(getattr(r, "is_hidden", False)),
        "created_at": r.created_at.isoformat()
    }