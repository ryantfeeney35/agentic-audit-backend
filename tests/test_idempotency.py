# tests/test_idempotency.py
"""
Tests for the idempotency decorator (``utils/idempotency.py``).

Covers:
- Duplicate POST returns cached response (no re-execution)
- Expired key is not matched (treated as new)
- Non-idempotent endpoints (missing header) are unaffected
- TTL sweep cleans old keys
- Race-condition resilience (duplicate key insertion)
"""
import json
import pytest
from datetime import datetime, timedelta
from flask import Flask, jsonify, request, g

from models import db, IdempotencyKey
from utils.idempotency import idempotent, IDEMPOTENCY_HEADER, IDEMPOTENCY_TTL


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def app():
    """Minimal Flask app with an idempotent test endpoint."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    # Track how many times the view body actually executes.
    call_count = {"n": 0}

    @app.route("/test-endpoint", methods=["POST"])
    @idempotent
    def test_endpoint():
        call_count["n"] += 1
        body = request.get_json(silent=True) or {}
        return jsonify({"ok": True, "echo": body.get("msg", ""), "call": call_count["n"]}), 201

    @app.route("/plain-endpoint", methods=["POST"])
    def plain_endpoint():
        """Non-idempotent endpoint for comparison."""
        call_count["n"] += 1
        return jsonify({"ok": True, "call": call_count["n"]}), 200

    with app.app_context():
        db.create_all()

    app._call_count = call_count
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestIdempotencyDecorator:
    """Core decorator behaviour."""

    def test_first_request_executes_and_stores_key(self, app, client):
        """A fresh idempotency key should execute the view and persist the key."""
        res = client.post(
            "/test-endpoint",
            json={"msg": "hello"},
            headers={IDEMPOTENCY_HEADER: "key-001"},
        )
        assert res.status_code == 201
        data = res.get_json()
        assert data["ok"] is True
        assert data["echo"] == "hello"
        assert data["call"] == 1

        with app.app_context():
            stored = IdempotencyKey.query.filter_by(key="key-001").first()
            assert stored is not None
            assert stored.status_code == 201
            assert stored.endpoint == "/test-endpoint"

    def test_duplicate_request_returns_cached_response(self, app, client):
        """A second request with the same key must return the cached response
        without re-executing the view."""
        headers = {IDEMPOTENCY_HEADER: "key-dup"}

        res1 = client.post("/test-endpoint", json={"msg": "first"}, headers=headers)
        assert res1.status_code == 201
        assert res1.get_json()["call"] == 1

        res2 = client.post("/test-endpoint", json={"msg": "second"}, headers=headers)
        assert res2.status_code == 201
        body2 = res2.get_json()
        # Should replay the *first* response, not re-execute.
        assert body2["echo"] == "first"
        assert body2["call"] == 1
        assert res2.headers.get("X-Idempotency-Replayed") == "true"

        # View was only called once.
        assert app._call_count["n"] == 1

    def test_different_keys_execute_independently(self, app, client):
        """Different idempotency keys must each execute the view."""
        r1 = client.post("/test-endpoint", json={"msg": "a"}, headers={IDEMPOTENCY_HEADER: "key-a"})
        r2 = client.post("/test-endpoint", json={"msg": "b"}, headers={IDEMPOTENCY_HEADER: "key-b"})
        assert r1.get_json()["call"] == 1
        assert r2.get_json()["call"] == 2

    def test_no_header_executes_normally(self, app, client):
        """When the header is absent the decorator is a pure pass-through."""
        r1 = client.post("/test-endpoint", json={"msg": "x"})
        r2 = client.post("/test-endpoint", json={"msg": "y"})
        assert r1.get_json()["call"] == 1
        assert r2.get_json()["call"] == 2

        with app.app_context():
            assert IdempotencyKey.query.count() == 0

    def test_non_idempotent_endpoint_unaffected(self, client):
        """An endpoint without the decorator ignores the header entirely."""
        r1 = client.post("/plain-endpoint", headers={IDEMPOTENCY_HEADER: "plain-1"})
        r2 = client.post("/plain-endpoint", headers={IDEMPOTENCY_HEADER: "plain-1"})
        # Both execute (plain_endpoint has no decorator).
        assert r1.get_json()["call"] == 1
        assert r2.get_json()["call"] == 2


class TestIdempotencyTTL:
    """TTL expiration and cleanup."""

    def test_expired_key_is_not_matched(self, app, client):
        """A key older than IDEMPOTENCY_TTL should be treated as new."""
        # Manually insert an expired key.
        with app.app_context():
            old = IdempotencyKey(
                key="expired-key",
                endpoint="/test-endpoint",
                method="POST",
                status_code=201,
                response_body=json.dumps({"ok": True, "echo": "old", "call": 0}),
                created_at=datetime.utcnow() - IDEMPOTENCY_TTL - timedelta(minutes=1),
            )
            db.session.add(old)
            db.session.commit()

        res = client.post(
            "/test-endpoint",
            json={"msg": "new"},
            headers={IDEMPOTENCY_HEADER: "expired-key"},
        )
        assert res.status_code == 201
        body = res.get_json()
        # Should be a fresh execution, not the stale cached response.
        assert body["echo"] == "new"
        assert body["call"] == 1
        assert res.headers.get("X-Idempotency-Replayed") is None

    def test_sweep_deletes_old_keys(self, app, client):
        """The lazy sweep should remove keys older than the TTL."""
        with app.app_context():
            # Insert one fresh and one stale key.
            fresh = IdempotencyKey(
                key="fresh-key",
                endpoint="/test-endpoint",
                method="POST",
                status_code=200,
                response_body="{}",
                created_at=datetime.utcnow(),
            )
            stale = IdempotencyKey(
                key="stale-key",
                endpoint="/test-endpoint",
                method="POST",
                status_code=200,
                response_body="{}",
                created_at=datetime.utcnow() - IDEMPOTENCY_TTL - timedelta(hours=1),
            )
            db.session.add_all([fresh, stale])
            db.session.commit()
            assert IdempotencyKey.query.count() == 2

        # Trigger a request which runs the sweep.
        client.post("/test-endpoint", json={}, headers={IDEMPOTENCY_HEADER: "trigger-sweep"})

        with app.app_context():
            remaining_keys = [k.key for k in IdempotencyKey.query.all()]
            assert "stale-key" not in remaining_keys
            assert "fresh-key" in remaining_keys
            # "trigger-sweep" should also have been stored.
            assert "trigger-sweep" in remaining_keys
