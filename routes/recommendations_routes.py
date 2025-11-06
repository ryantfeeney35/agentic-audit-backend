# routes/recommendations_routes.py
from flask import Blueprint, jsonify, request, abort, current_app
from agents.orchestrator import OrchestratorAgent
from models import AuditRecommendation, db, AuditMedia, Audit
import tempfile
import os
from werkzeug.utils import secure_filename
from threading import Thread
from supabase_utils import upload_to_supabase_and_get_url
from agents.roi import AtticInsulationROIInput, calculate_attic_insulation_roi

bp = Blueprint("recommendations", __name__)

@bp.route("/roi/insulation/attic", methods=["POST"])
def roi_insulation_attic():
    """Deterministic ROI calculator for attic insulation upgrades.

    Body must match AtticInsulationROIInput; returns ROIResult JSON.
    """
    data = request.get_json(silent=True) or {}
    try:
        inp = AtticInsulationROIInput(**data)
    except Exception as e:
        abort(400, description=f"Invalid input: {e}")

    res = calculate_attic_insulation_roi(inp)
    return jsonify(res.model_dump())

@bp.route("/audits/<int:audit_id>/recommendations", methods=["GET"])
def get_recommendations(audit_id):
    # Default behavior: exclude hidden recommendations unless include_hidden=true
    include_hidden = str(request.args.get("include_hidden", "false")).lower() in ("1", "true", "yes")

    # Optional source filter: all (default), audio, ai
    source = str(request.args.get("source", "all")).lower()
    if source not in ("all", "audio", "ai"):
        abort(400, description="Invalid source filter. Allowed: all, audio, ai")

    # If there are no recommendations at all for the audit, delegate to the agent to generate them.
    total_recs = AuditRecommendation.query.filter_by(audit_id=audit_id).count()
    if total_recs == 0:
        agent = OrchestratorAgent(audit_id)
        saved = agent.generate_recommendations()
        # If caller requested a source filter, apply it to the generated results before returning
        if source != "all":
            saved = [r for r in saved if (getattr(r, 'source', None) or 'ai').lower() == source]
        return jsonify([serialize_rec(r) for r in saved])

    # Otherwise, load existing recommendations (respect hidden filter)
    # Build base query
    q = AuditRecommendation.query.filter_by(audit_id=audit_id)
    if not include_hidden:
        q = q.filter_by(is_hidden=False)
    if source != "all":
        q = q.filter(AuditRecommendation.source == source)

    existing = q.all()

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
        "summary_override": getattr(r, "summary_override", None),
        "annual_savings_usd": r.annual_savings_usd,
        "upgrade_cost_usd": r.upgrade_cost_usd,
        "payback_years": r.payback_years,
        "display_order": r.display_order,
        "is_hidden": bool(getattr(r, "is_hidden", False)),
        "source": (getattr(r, "source", None) or "ai"),
        "created_at": r.created_at.isoformat(),
        # recommended media association
        "recommended_media_id": getattr(r, "recommended_media_id", None),
        "recommended_media_source": getattr(r, "recommended_media_source", None),
        "recommended_media_url": (r.recommended_media.media_url if getattr(r, 'recommended_media', None) else None),
        # ROI inputs persisted per recommendation
        "roi_inputs": getattr(r, "roi_inputs", None) or {},
    }



@bp.route("/audits/<int:audit_id>/recommendations/<int:rec_id>/audio", methods=["POST"])
def upload_recommendation_audio(audit_id, rec_id):
    """Accept an audio recording for a recommendation, upload to Supabase, and trigger transcription + refinement.

    Returns 202 with processing status and media_url. The orchestrator will update AuditRecommendation.summary_override when complete.
    """
    rec = AuditRecommendation.query.filter_by(id=rec_id, audit_id=audit_id).first()
    if not rec:
        abort(404, description="Recommendation not found for this audit")

    # Accept file under 'audio' form field for clarity
    file = request.files.get("audio") or request.files.get("file")
    if not file:
        abort(400, description="Missing audio file in 'audio' form field")

    filename = secure_filename(f"{audit_id}_rec_{rec_id}_{file.filename}")
    tmp_dir = tempfile.gettempdir()
    tmp_path = os.path.join(tmp_dir, filename)
    file.save(tmp_path)

    # Upload to Supabase (uses existing helper)
    # Use the recommendation step_type as label so files are organized by domain
    step_label = rec.step_type or f"recommendation_{rec_id}"
    media_url = upload_to_supabase_and_get_url(tmp_path, audit_id, step_label, media_type="audio", step_type=rec.step_type)
    if not media_url:
        abort(500, description="Failed to upload audio to storage")

    # Trigger async processing by the orchestrator (do not block the request)
    def _process(app):
        # run inside the Flask application context so DB/session works
        with app.app_context():
            try:
                agent = OrchestratorAgent(audit_id)
                agent.process_recommendation_audio(rec_id, media_url, local_path=tmp_path)
            except Exception as e:
                app.logger.exception("Failed to process recommendation audio: %s", e)

    Thread(target=_process, args=(current_app._get_current_object(),)).start()

    return jsonify({"status": "processing", "media_url": media_url}), 202


@bp.route("/audits/<int:audit_id>/recommendations/<int:rec_id>/recommended_media", methods=["PATCH"])
def patch_recommendation_media(audit_id, rec_id):
    """Allow auditors to override or clear the recommended media for a recommendation.

    Payload: { "recommended_media_id": <int|null>, "source": "auditor" }
    If `recommended_media_id` is null, the association will be cleared.
    """
    payload = request.get_json() or {}
    if 'recommended_media_id' not in payload:
        abort(400, description="Missing 'recommended_media_id' in request body")

    rec = AuditRecommendation.query.filter_by(id=rec_id, audit_id=audit_id).first()
    if not rec:
        abort(404, description="Recommendation not found for this audit")

    try:
        rm_id = payload.get('recommended_media_id')
        if rm_id is None:
            rec.recommended_media_id = None
            rec.recommended_media_source = None
        else:
            # Validate that media belongs to the same audit
            media = AuditMedia.query.filter_by(id=int(rm_id), audit_id=audit_id).first()
            if not media:
                abort(400, description="Invalid recommended_media_id for this audit")
            rec.recommended_media_id = media.id
            # mark that auditor explicitly selected this media unless caller specified otherwise
            rec.recommended_media_source = payload.get('source') or 'auditor'

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        abort(500, description=f"Failed to update recommended media: {e}")

    return jsonify(serialize_rec(rec))


@bp.route("/audits/<int:audit_id>/roi-defaults", methods=["GET"])
def get_audit_roi_defaults(audit_id):
    audit = Audit.query.filter_by(id=audit_id).first()
    if not audit:
        abort(404, description="Audit not found")
    return jsonify(audit.roi_defaults or {})


@bp.route("/audits/<int:audit_id>/roi-defaults", methods=["PATCH"])
def patch_audit_roi_defaults(audit_id):
    audit = Audit.query.filter_by(id=audit_id).first()
    if not audit:
        abort(404, description="Audit not found")
    payload = request.get_json() or {}
    if not isinstance(payload, dict):
        abort(400, description="Body must be a JSON object")
    try:
        # Merge into a new dict so SQLAlchemy change tracking detects the update
        base = dict(audit.roi_defaults or {})
        merged = {**base, **{k: payload[k] for k in payload.keys()}}
        audit.roi_defaults = merged
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        abort(500, description=f"Failed to update ROI defaults: {e}")
    return jsonify(audit.roi_defaults or {})


@bp.route("/audits/<int:audit_id>/recommendations/<int:rec_id>/roi-inputs", methods=["PATCH"])
def patch_recommendation_roi_inputs(audit_id, rec_id):
    rec = AuditRecommendation.query.filter_by(id=rec_id, audit_id=audit_id).first()
    if not rec:
        abort(404, description="Recommendation not found for this audit")

    payload = request.get_json() or {}
    if not isinstance(payload, dict):
        abort(400, description="Body must be a JSON object")

    # Merge and persist roi_inputs
    try:
        cur = rec.roi_inputs or {}
        # Coerce incoming values: convert numeric-like strings to numbers and drop empty strings
        for k, v in payload.items():
            # normalize empty strings -> remove key
            if isinstance(v, str) and v.strip() == "":
                if k in cur:
                    cur.pop(k, None)
                continue

            # attempt to coerce numeric strings to numbers
            if isinstance(v, str):
                try:
                    num = float(v)
                    # if the string represents an integer value, keep as int
                    if num.is_integer():
                        cur[k] = int(num)
                    else:
                        cur[k] = num
                    continue
                except ValueError:
                    # not a numeric string, keep as-is
                    pass

            # otherwise persist value as provided (number, bool, etc.)
            cur[k] = v

        rec.roi_inputs = cur
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Failed to persist roi_inputs for rec %s: %s", rec_id, e)
        abort(500, description=f"Failed to update ROI inputs: {e}")

    # Optionally compute and persist ROI-derived numeric fields for attic insulation
    try:
        if (rec.step_type or '').lower() == 'insulation' and ('attic' in (rec.summary or '').lower() or 'attic' in (rec.summary_override or '').lower() if rec.summary_override else False):
            audit = Audit.query.filter_by(id=audit_id).first()
            defaults = (audit.roi_defaults or {}) if audit else {}
            # Build input from roi_inputs + defaults
            area = (rec.roi_inputs or {}).get('attic_area_sqft')
            current_r = (rec.roi_inputs or {}).get('attic_current_r')
            target_r = (rec.roi_inputs or {}).get('attic_target_r', 38)
            net_cost = (rec.roi_inputs or {}).get('net_upgrade_cost_usd')
            if area and current_r is not None and net_cost:
                roi_inp = AtticInsulationROIInput(
                    area_sqft=float(area),
                    current_r_value=float(current_r),
                    target_r_value=float(target_r),
                    energy_rate_usd_per_kwh=float(defaults.get('energy_rate_usd_per_kwh', 0.20)),
                    net_upgrade_cost_usd=float(net_cost),
                    analysis_horizon_years=int(defaults.get('analysis_horizon_years', 25)),
                    climate=str(defaults.get('climate', 'mild')),
                )
                res = calculate_attic_insulation_roi(roi_inp)
                rec.annual_savings_usd = res.annual_savings_usd
                rec.upgrade_cost_usd = float(net_cost)
                rec.payback_years = res.payback_years
                db.session.commit()
    except Exception:
        # Non-fatal if compute fails
        db.session.rollback()

    return jsonify(serialize_rec(rec))