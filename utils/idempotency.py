# utils/idempotency.py
"""
Decorator-based idempotency guard for POST endpoints.

The mobile client attaches an ``X-Idempotency-Key`` header when replaying
queued mutations that were stored while offline.  This module:

1. Checks for an existing key before invoking the view.
2. If the key exists and hasn't expired, returns the cached response verbatim.
3. If the key is new, lets the view run, then stores the response.
4. Lazily sweeps expired keys (>24 h) on every check.

Usage::

    @bp.route('/things', methods=['POST'])
    @require_auth
    @idempotent
    def create_thing():
        ...

The decorator is intentionally a no-op when the header is absent so that
normal (non-offline-drain) requests are unaffected.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from functools import wraps

from flask import request, make_response

from models import db, IdempotencyKey

logger = logging.getLogger(__name__)

# Keys older than this are ignored and eligible for cleanup.
IDEMPOTENCY_TTL = timedelta(hours=24)

# Header the client sends.
IDEMPOTENCY_HEADER = "X-Idempotency-Key"


def _sweep_expired() -> None:
    """Delete idempotency keys older than the TTL (best-effort)."""
    try:
        cutoff = datetime.utcnow() - IDEMPOTENCY_TTL
        IdempotencyKey.query.filter(IdempotencyKey.created_at < cutoff).delete()
        db.session.commit()
    except Exception:
        db.session.rollback()


def idempotent(fn):
    """Flask view decorator that enforces idempotency via ``X-Idempotency-Key``.

    When the header is missing the view executes normally (no idempotency logic).
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        key_value = request.headers.get(IDEMPOTENCY_HEADER)

        # No header → execute normally (not an offline-drain replay).
        if not key_value:
            return fn(*args, **kwargs)

        # Lazy cleanup of stale keys (cheap on small tables).
        _sweep_expired()

        # Check for existing key.
        existing = IdempotencyKey.query.filter_by(key=key_value).first()

        if existing:
            # Key exists and is still within its TTL – return cached response.
            age = datetime.utcnow() - existing.created_at
            if age <= IDEMPOTENCY_TTL:
                logger.info("Idempotency hit for key=%s endpoint=%s", key_value, existing.endpoint)
                resp = make_response(existing.response_body, existing.status_code)
                resp.headers["Content-Type"] = "application/json"
                resp.headers["X-Idempotency-Replayed"] = "true"
                return resp
            else:
                # Expired – delete and process as new.
                db.session.delete(existing)
                db.session.commit()

        # Execute the view function.
        response = fn(*args, **kwargs)

        # Normalise response to (body, status_code) so we can persist.
        if isinstance(response, tuple):
            body, status_code = response[0], response[1]
        else:
            body = response
            status_code = response.status_code if hasattr(response, "status_code") else 200

        # Get response body as string.
        if hasattr(body, "get_data"):
            response_text = body.get_data(as_text=True)
        elif hasattr(body, "json"):
            response_text = json.dumps(body.json)
        elif isinstance(body, (dict, list)):
            response_text = json.dumps(body)
        else:
            response_text = str(body)

        # Persist the key.
        try:
            record = IdempotencyKey(
                key=key_value,
                endpoint=request.path,
                method=request.method,
                status_code=status_code if isinstance(status_code, int) else 200,
                response_body=response_text,
            )
            db.session.add(record)
            db.session.commit()
            logger.info("Idempotency stored key=%s endpoint=%s", key_value, request.path)
        except Exception:
            # Unique-constraint race – another worker already stored it.
            db.session.rollback()
            logger.warning("Idempotency key=%s already stored (race)", key_value)

        return response

    return wrapper
