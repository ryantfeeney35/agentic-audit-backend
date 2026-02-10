from flask import Blueprint, request, jsonify, g
from auth import require_auth
from models import Audit, RoomMeasurement, db

bp = Blueprint("room_measurements", __name__)

ALLOWED_SOURCES = {"arkit", "arcore", "manual"}
QUALITY_KEY_MAP = {
    "trackingLostCount": "tracking_lost_count",
    "tracking_lost_count": "tracking_lost_count",
    "avgTrackingQuality": "avg_tracking_quality",
    "avg_tracking_quality": "avg_tracking_quality",
    "loopClosureMeters": "loop_closure_meters",
    "loop_closure_meters": "loop_closure_meters",
    "scanDurationSec": "scan_duration_seconds",
    "scan_duration_seconds": "scan_duration_seconds",
    "scanDurationSeconds": "scan_duration_seconds",
    "deviceModel": "device_model",
    "device_model": "device_model",
    "os": "os",
}

def _fetch_audit_or_forbidden(audit_id):
    audit = Audit.query.filter_by(id=audit_id, user_id=g.current_user['id']).first()
    if not audit:
        return None, (jsonify({"error": "Audit not found or access denied"}), 403)
    return audit, None


def _normalize_vertices(vertices_payload):
    normalized = []
    if not vertices_payload:
        return normalized
    for idx, vertex in enumerate(vertices_payload):
        x_val = None
        y_val = None
        if isinstance(vertex, dict):
            x_val = vertex.get("x_m")
            if x_val is None:
                x_val = vertex.get("x")
            y_val = vertex.get("y_m")
            if y_val is None:
                y_val = vertex.get("y")
        elif isinstance(vertex, (list, tuple)) and len(vertex) >= 2:
            x_val, y_val = vertex[0], vertex[1]
        if x_val is None or y_val is None:
            continue
        try:
            normalized.append({"x_m": float(x_val), "y_m": float(y_val)})
        except (TypeError, ValueError):
            continue
    return normalized


def _extract_quality_metadata(payload):
    raw_quality = payload.get("quality") or payload.get("quality_metadata") or payload.get("qualityMetadata")
    if not isinstance(raw_quality, dict):
        return {}
    normalized = {}
    for incoming_key, value in raw_quality.items():
        canonical_key = QUALITY_KEY_MAP.get(incoming_key, incoming_key)
        normalized[canonical_key] = value
    return normalized


def _parse_bool(payload, key, fallback_key=None, default=False):
    value = payload.get(key)
    if value is None and fallback_key:
        value = payload.get(fallback_key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _parse_float(payload, *keys):
    for key in keys:
        if key in payload and payload[key] is not None:
            try:
                return float(payload[key])
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a number")
    raise KeyError(keys[0])


def _parse_area_sqft(payload):
    try:
        return _parse_float(payload, "area_sqft", "areaSqFt")
    except KeyError:
        pass
    area_sq_m = None
    for key in ("area_sq_m", "areaSqM"):
        if key in payload and payload[key] is not None:
            area_sq_m = payload[key]
            break
    if area_sq_m is not None:
        try:
            return float(area_sq_m) * 10.7639
        except (TypeError, ValueError):
            raise ValueError("areaSqM must be a number")
    raise KeyError("area_sqft")


@bp.route('/audits/<int:audit_id>/rooms/<string:room_id>/measurements', methods=['POST'])
@require_auth
def create_room_measurement(audit_id, room_id):
    """
    POST /api/audits/<audit_id>/rooms/<room_id>/measurements

    Request Schema (RoomScanResultPayload)
    {
        "source": "arkit" | "arcore" | "manual",
        "areaSqFt": number,
        "verticesMeters": [{"x": number, "y": number}, ...],
        "confidenceScore": number (0-1),
        "quality": {
            "trackingLostCount"?: number,
            "avgTrackingQuality"?: number,
            "loopClosureMeters"?: number,
            "scanDurationSec"?: number,
            "deviceModel"?: string,
            "os"?: string
        },
        "userModified"?: boolean,
        "userVerified"?: boolean
    }

    Response Schema
    {
        "measurement": RoomMeasurement
    }

    Errors:
        400 - Validation failed (missing fields, constraints)
        403 - Audit not found or access denied
    """
    _, error_response = _fetch_audit_or_forbidden(audit_id)
    if error_response:
        return error_response

    payload = request.get_json() or {}
    errors = []

    source = payload.get('source', '').lower()
    if source not in ALLOWED_SOURCES:
        errors.append("source must be one of arkit, arcore, manual")

    try:
        area_sqft = _parse_area_sqft(payload)
        if area_sqft <= 0:
            errors.append("area_sqft must be greater than zero")
    except KeyError:
        errors.append("area_sqft (or areaSqFt/areaSqM) is required")
        area_sqft = None
    except ValueError as exc:
        errors.append(str(exc))
        area_sqft = None

    try:
        confidence_score = _parse_float(payload, 'confidence_score', 'confidenceScore')
        if not 0 <= confidence_score <= 1:
            errors.append("confidence_score must be between 0 and 1")
    except (KeyError, ValueError):
        errors.append("confidence_score (or confidenceScore) is required and must be 0-1")
        confidence_score = None

    vertices = payload.get('polygon_vertices') or payload.get('polygonVertices') or payload.get('vertices_meters') or payload.get('verticesMeters')
    normalized_vertices = _normalize_vertices(vertices)

    if source in {'arkit', 'arcore'} and len(normalized_vertices) < 3:
        errors.append("At least three vertices are required for AR-based scans")

    normalized_room_id = room_id.strip()
    if not normalized_room_id:
        errors.append("room_id path parameter cannot be empty")

    quality_metadata = _extract_quality_metadata(payload)
    user_modified = _parse_bool(payload, 'user_modified', fallback_key='userModified', default=False)
    user_verified = _parse_bool(payload, 'user_verified', fallback_key='userVerified', default=False)

    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400

    # Upsert: Check if a measurement already exists for this audit/room
    existing = RoomMeasurement.query.filter_by(
        audit_id=audit_id,
        room_id=normalized_room_id
    ).first()

    if existing:
        # Update the existing measurement
        existing.area_sqft = area_sqft
        existing.polygon_vertices = normalized_vertices
        existing.source = source
        existing.confidence_score = confidence_score
        existing.quality_metadata = quality_metadata
        existing.user_modified = user_modified
        existing.user_verified = user_verified
        measurement = existing
    else:
        # Create a new measurement
        measurement = RoomMeasurement(
            audit_id=audit_id,
            room_id=normalized_room_id,
            area_sqft=area_sqft,
            polygon_vertices=normalized_vertices,
            source=source,
            confidence_score=confidence_score,
            quality_metadata=quality_metadata,
            user_modified=user_modified,
            user_verified=user_verified,
        )
        db.session.add(measurement)
    
    db.session.commit()

    return jsonify({"measurement": measurement.to_dict()}), 201


@bp.route('/audits/<int:audit_id>/rooms/<string:room_id>/measurements/latest', methods=['GET'])
@require_auth
def get_latest_room_measurement(audit_id, room_id):
    """
    GET /api/audits/<audit_id>/rooms/<room_id>/measurements/latest

    Response Schema
    {
        "measurement": RoomMeasurement
    }

    Errors:
        403 - Audit not found or access denied
        404 - Room has no stored measurements
    """
    _, error_response = _fetch_audit_or_forbidden(audit_id)
    if error_response:
        return error_response

    normalized_room_id = room_id.strip()

    measurement = (
        RoomMeasurement.query
        .filter_by(audit_id=audit_id, room_id=normalized_room_id)
        .order_by(RoomMeasurement.created_at.desc(), RoomMeasurement.updated_at.desc())
        .first()
    )

    if not measurement:
        return jsonify({"error": "No measurements found for room"}), 404

    return jsonify({"measurement": measurement.to_dict()}), 200


@bp.route('/audits/<int:audit_id>/measurements/summary', methods=['GET'])
@require_auth
def get_measurement_summary(audit_id):
    """
    GET /api/audits/<audit_id>/measurements/summary

    Response Schema
    {
        "audit_id": int,
        "rooms": [
            {
                "room_id": str,
                "latest_measurement": RoomMeasurement
            }
        ],
        "totals": {
            "rooms": int,
            "measured_area_sqft": float,
            "verified_rooms": int,
            "manual_entries": int,
            "avg_confidence_score": float | null
        },
        "last_updated": ISO8601 | null
    }

    Errors:
        403 - Audit not found or access denied
    """
    _, error_response = _fetch_audit_or_forbidden(audit_id)
    if error_response:
        return error_response

    measurements = (
        RoomMeasurement.query
        .filter_by(audit_id=audit_id)
        .order_by(RoomMeasurement.created_at.desc(), RoomMeasurement.updated_at.desc())
        .all()
    )

    latest_by_room = {}
    for measurement in measurements:
        if measurement.room_id not in latest_by_room:
            latest_by_room[measurement.room_id] = measurement

    rooms_payload = []
    total_area = 0.0
    verified_count = 0
    manual_count = 0
    confidence_scores = []
    latest_timestamp = None

    for room, measurement in sorted(latest_by_room.items()):
        rooms_payload.append({
            "room_id": room,
            "latest_measurement": measurement.to_dict(),
        })
        total_area += measurement.area_sqft or 0
        if measurement.user_verified:
            verified_count += 1
        if measurement.source == 'manual':
            manual_count += 1
        if measurement.confidence_score is not None:
            confidence_scores.append(measurement.confidence_score)
        for ts in (measurement.updated_at, measurement.created_at):
            if ts and (latest_timestamp is None or ts > latest_timestamp):
                latest_timestamp = ts

    summary = {
        "audit_id": audit_id,
        "rooms": rooms_payload,
        "totals": {
            "rooms": len(latest_by_room),
            "measured_area_sqft": round(total_area, 2),
            "verified_rooms": verified_count,
            "manual_entries": manual_count,
            "avg_confidence_score": round(sum(confidence_scores) / len(confidence_scores), 3) if confidence_scores else None,
        },
        "last_updated": latest_timestamp.isoformat() if latest_timestamp else None,
    }

    return jsonify(summary), 200
