"""Tests for SFTP ingest and data-notify routes (Groups 6 & 7)."""

import json
import os
import sys
import pytest
from unittest.mock import patch, MagicMock

# Ensure boto3 is importable (stub if not installed)
if 'boto3' not in sys.modules:
    sys.modules['boto3'] = MagicMock()

from flask import Flask
from models import db, UtilityConnection, UtilityUsageData, UtilityUsageSummary
from routes.utility_routes import utility_bp


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    os.environ["SFTP_INGEST_SECRET"] = "test-secret-123"
    db.init_app(app)
    app.register_blueprint(utility_bp)
    with app.app_context():
        db.create_all()
    yield app
    os.environ.pop("SFTP_INGEST_SECRET", None)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def ctx(app):
    with app.app_context():
        yield


# ---------------------------------------------------------------------------
# SFTP Ingest route (Group 6)
# ---------------------------------------------------------------------------

class TestSFTPIngest:

    def test_missing_secret_header_returns_401(self, client):
        res = client.post(
            "/api/utility/sftp-ingest",
            json={"s3_key": "incoming/test.xml", "bucket": "b"},
        )
        assert res.status_code == 401

    def test_wrong_secret_returns_401(self, client):
        res = client.post(
            "/api/utility/sftp-ingest",
            json={"s3_key": "incoming/test.xml", "bucket": "b"},
            headers={"X-Ingest-Secret": "wrong"},
        )
        assert res.status_code == 401

    def test_missing_body_returns_400(self, client):
        res = client.post(
            "/api/utility/sftp-ingest",
            json={},
            headers={"X-Ingest-Secret": "test-secret-123"},
        )
        assert res.status_code == 400
        assert "file path" in res.get_json()["error"].lower() or "Missing" in res.get_json()["error"]

    @patch("routes.utility_routes._get_storage_client")
    def test_subscription_csv_processing(self, mock_get_client, client, ctx):
        csv_content = "CE,COK1,1234,GRP,MTR,x@y.com,,TOU\n"
        mock_s3 = MagicMock()
        mock_get_client.return_value = (mock_s3, "test-bucket")
        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=lambda: csv_content.encode("utf-8"))
        }

        res = client.post(
            "/api/utility/sftp-ingest",
            json={
                "s3_key": "incoming/SUSTAINRGY_SUBSCRIPTIONS_20260101.CSV",
                "bucket": "test-bucket",
            },
            headers={"X-Ingest-Secret": "test-secret-123"},
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["type"] == "subscription_csv"
        assert data["summary"]["enrolled"] == 1

    @patch("routes.utility_routes._get_storage_client")
    def test_unrecognized_file(self, mock_get_client, client, ctx):
        mock_s3 = MagicMock()
        mock_get_client.return_value = (mock_s3, "test-bucket")
        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=lambda: b"whatever")
        }

        res = client.post(
            "/api/utility/sftp-ingest",
            json={
                "s3_key": "incoming/random_unknown.txt",
                "bucket": "test-bucket",
            },
            headers={"X-Ingest-Secret": "test-secret-123"},
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["type"] == "unrecognized"

    @patch("routes.utility_routes._get_storage_client")
    def test_path_format_webhook(self, mock_get_client, client, ctx):
        """SFTP To Go sends {path: '/prod/...'} instead of {s3_key, bucket}."""
        csv_content = "CE,COK2,5678,GRP,MTR,z@w.com,,TOU\n"
        mock_s3 = MagicMock()
        mock_get_client.return_value = (mock_s3, "sftptogo-bucket")
        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=lambda: csv_content.encode("utf-8"))
        }

        res = client.post(
            "/api/utility/sftp-ingest",
            json={"path": "/prod/SUSTAINRGY_SUBSCRIPTIONS_20260201.CSV"},
            headers={"X-Ingest-Secret": "test-secret-123"},
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["type"] == "subscription_csv"
        # Verify the leading slash was stripped for the S3 key
        call_args = mock_s3.get_object.call_args
        assert call_args[1]["Key"] == "prod/SUSTAINRGY_SUBSCRIPTIONS_20260201.CSV"


# ---------------------------------------------------------------------------
# Data Notify route (Group 7)
# ---------------------------------------------------------------------------

class TestDataNotify:

    def test_missing_subscription_id_returns_400(self, client):
        res = client.post(
            "/api/utility/data-notify",
            json={"resource_uri": "http://example.com/resource"},
        )
        assert res.status_code == 400

    def test_unknown_subscription_returns_404(self, client, ctx):
        res = client.post(
            "/api/utility/data-notify",
            json={"subscription_id": "999999"},
        )
        assert res.status_code == 404

    def test_matched_subscription_triggers_sync(self, client, ctx):
        # Seed a connection with subscription_id in metadata
        conn = UtilityConnection(
            user_id="u1",
            audit_id=1,
            utility_name="SDGE",
            provider_name="sdge_cmd",
            data_scope="electric",
            status="connected",
            provider_metadata={"subscription_id": "SUB123"},
        )
        db.session.add(conn)
        db.session.commit()
        conn_id = conn.id

        # Mock the provider sync
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.records_imported = 42

        with patch("routes.utility_routes.registry") as mock_registry:
            mock_provider = MagicMock()
            mock_provider.sync_usage.return_value = mock_result
            mock_registry.get_provider.return_value = mock_provider

            res = client.post(
                "/api/utility/data-notify",
                json={"subscription_id": "SUB123"},
            )

        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert data["records_imported"] == 42
