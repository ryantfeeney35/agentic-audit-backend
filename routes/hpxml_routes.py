"""HPXML export + Home Energy Score (HEScore) endpoints.

  GET  /api/audits/<id>/hpxml                 -> download/preview generated HPXML
  GET  /api/audits/<id>/hpxml/validation      -> gap analysis + local translator check
  GET  /api/audits/<id>/hes-inputs            -> current supplemental HES inputs
  PUT  /api/audits/<id>/hes-inputs            -> update supplemental HES inputs
  POST /api/audits/<id>/home-energy-score     -> generate official score via DOE API
  GET  /api/audits/<id>/home-energy-score     -> latest score record
"""
from flask import Blueprint, request, jsonify, g, Response
from auth import require_auth
from models import db, Audit, HomeEnergyScore

from hpxml.mapper import build_hes_model
from hpxml.serializer import to_hpxml
from hpxml.gaps import compute_gaps, is_scoreable
from hpxml.validation import validate_with_translator
from hpxml.hes_client import HESClient, HESConfigError, HESAPIError

bp = Blueprint("hpxml", __name__)


def _get_owned_audit(audit_id):
    """Load an audit owned by the current user, or return (None, error_response)."""
    audit = Audit.query.filter_by(id=audit_id, user_id=g.current_user["id"]).first()
    if not audit:
        return None, (jsonify({"error": "Audit not found or access denied"}), 403)
    return audit, None


@bp.route("/audits/<int:audit_id>/hpxml", methods=["GET"])
@require_auth
def get_hpxml(audit_id):
    audit, err = _get_owned_audit(audit_id)
    if err:
        return err
    model = build_hes_model(audit)
    xml = to_hpxml(model)
    if request.args.get("download") == "true":
        return Response(
            xml,
            mimetype="application/xml",
            headers={"Content-Disposition": f'attachment; filename="audit-{audit_id}.xml"'},
        )
    return jsonify({"audit_id": audit_id, "hpxml": xml})


@bp.route("/audits/<int:audit_id>/hpxml/validation", methods=["GET"])
@require_auth
def validate_hpxml(audit_id):
    audit, err = _get_owned_audit(audit_id)
    if err:
        return err
    model = build_hes_model(audit)
    gaps = [g.to_dict() for g in compute_gaps(model)]
    xml = to_hpxml(model)
    translator = validate_with_translator(xml)
    return jsonify({
        "audit_id": audit_id,
        "scoreable": is_scoreable(model),
        "gaps": gaps,
        "required_count": sum(1 for g in gaps if g["severity"] == "required"),
        "review_count": sum(1 for g in gaps if g["severity"] == "review"),
        "translator": translator,
    })


@bp.route("/audits/<int:audit_id>/hes-inputs", methods=["GET"])
@require_auth
def get_hes_inputs(audit_id):
    audit, err = _get_owned_audit(audit_id)
    if err:
        return err
    return jsonify({"audit_id": audit_id, "hes_inputs": audit.hes_inputs or {}})


@bp.route("/audits/<int:audit_id>/hes-inputs", methods=["PUT", "PATCH"])
@require_auth
def update_hes_inputs(audit_id):
    audit, err = _get_owned_audit(audit_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    incoming = data.get("hes_inputs", data)
    if not isinstance(incoming, dict):
        return jsonify({"error": "hes_inputs must be an object"}), 400
    # Merge (shallow) so partial form saves don't wipe other sections.
    current = dict(audit.hes_inputs or {})
    current.update(incoming)
    audit.hes_inputs = current
    db.session.commit()

    model = build_hes_model(audit)
    return jsonify({
        "audit_id": audit_id,
        "hes_inputs": audit.hes_inputs,
        "scoreable": is_scoreable(model),
        "gaps": [g.to_dict() for g in compute_gaps(model)],
    })


@bp.route("/audits/<int:audit_id>/home-energy-score", methods=["GET"])
@require_auth
def get_home_energy_score(audit_id):
    audit, err = _get_owned_audit(audit_id)
    if err:
        return err
    record = (
        HomeEnergyScore.query
        .filter_by(audit_id=audit_id, user_id=g.current_user["id"])
        .order_by(HomeEnergyScore.created_at.desc())
        .first()
    )
    if not record:
        return jsonify({"audit_id": audit_id, "score": None}), 200
    return jsonify(record.to_dict())


@bp.route("/audits/<int:audit_id>/home-energy-score", methods=["POST"])
@require_auth
def generate_home_energy_score(audit_id):
    audit, err = _get_owned_audit(audit_id)
    if err:
        return err

    model = build_hes_model(audit)
    if not is_scoreable(model):
        return jsonify({
            "error": "Audit is missing required inputs for a Home Energy Score",
            "gaps": [g.to_dict() for g in compute_gaps(model) if g.severity == "required"],
        }), 422

    body = request.get_json(silent=True) or {}
    assessment_type = body.get("assessment_type", model.assessment_type or "initial")
    xml = to_hpxml(model)

    record = HomeEnergyScore(
        user_id=g.current_user["id"],
        audit_id=audit_id,
        status="submitted",
        assessment_type=assessment_type,
        hpxml=xml,
    )
    db.session.add(record)
    db.session.commit()

    try:
        client = HESClient()
        result = client.score_building(
            hpxml=xml,
            address=model.address.model_dump(),
            assessment_type=assessment_type,
            assessment_date=model.assessment_date,
            external_id=f"audit-{audit_id}",
        )
        record.status = "scored"
        record.hescore_building_id = str(result.get("building_id")) if result.get("building_id") else None
        record.base_score = result.get("score")
        record.label_url = result.get("label_url")
        record.raw_result = result.get("results") or {}
        db.session.commit()
        return jsonify(record.to_dict()), 201
    except HESConfigError as exc:
        record.status = "error"
        record.error_message = f"Configuration error: {exc}"
        db.session.commit()
        return jsonify({"error": str(exc), "record": record.to_dict()}), 503
    except HESAPIError as exc:
        record.status = "error"
        record.error_message = str(exc)
        db.session.commit()
        return jsonify({"error": str(exc), "record": record.to_dict()}), 502
