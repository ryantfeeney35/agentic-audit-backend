"""Tests for SDG&E subscription CSV parser and handlers."""

import pytest
from flask import Flask
from models import db, UtilityConnection

from utils.sdge_subscriptions import (
    SubscriptionRecord,
    parse_subscription_csv,
    process_subscription_records,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    with app.app_context():
        db.create_all()
    yield app


@pytest.fixture
def ctx(app):
    with app.app_context():
        yield


# ---------------------------------------------------------------------------
# parse_subscription_csv
# ---------------------------------------------------------------------------

class TestParseSubscriptionCSV:

    def test_basic_ce_row(self):
        csv = "CE,ABC123,1234567,GRP1,MTR001,user@example.com,,TOU-DR1\n"
        records = parse_subscription_csv(csv)
        assert len(records) == 1
        r = records[0]
        assert r.transaction_code == "CE"
        assert r.obfuscated_key == "ABC123"
        assert r.account == "1234567"
        assert r.account_group == "GRP1"
        assert r.meter == "MTR001"
        assert r.email == "user@example.com"
        assert r.termination_date is None
        assert r.rate == "TOU-DR1"

    def test_cu_with_termination_date(self):
        csv = "CU,ABC123,1234567,GRP1,MTR001,user@example.com,2026-06-30,TOU-DR1\n"
        records = parse_subscription_csv(csv)
        assert len(records) == 1
        assert records[0].transaction_code == "CU"
        assert records[0].termination_date == "2026-06-30"

    def test_mc_row(self):
        csv = "MC,ABC123,1234567,GRP1,MTR002,user@example.com,,TOU-DR1\n"
        records = parse_subscription_csv(csv)
        assert len(records) == 1
        assert records[0].transaction_code == "MC"
        assert records[0].meter == "MTR002"

    def test_multiple_rows(self):
        csv = (
            "CE,KEY1,1111,G1,M1,a@b.com,,R1\n"
            "CU,KEY2,2222,G2,M2,c@d.com,2026-01-01,R2\n"
            "MC,KEY3,3333,G3,M3,e@f.com,,R3\n"
        )
        records = parse_subscription_csv(csv)
        assert len(records) == 3
        assert [r.transaction_code for r in records] == ["CE", "CU", "MC"]

    def test_blank_lines_skipped(self):
        csv = "\nCE,K1,1,G,M,x@y.com,,R\n\n"
        records = parse_subscription_csv(csv)
        assert len(records) == 1

    def test_short_row_skipped(self):
        csv = "CE,K1,1,G,M\n"  # only 5 fields
        records = parse_subscription_csv(csv)
        assert len(records) == 0

    def test_unknown_transaction_code_skipped(self):
        csv = "XX,K1,1,G,M,x@y.com,,R\n"
        records = parse_subscription_csv(csv)
        assert len(records) == 0

    def test_case_insensitive_transaction_code(self):
        csv = "ce,K1,1,G,M,x@y.com,,R\n"
        records = parse_subscription_csv(csv)
        assert len(records) == 1
        assert records[0].transaction_code == "CE"


# ---------------------------------------------------------------------------
# CE (Customer Enrollment) handler
# ---------------------------------------------------------------------------

class TestHandleEnrollment:

    def test_enrollment_creates_pending_data_connection(self, ctx):
        records = [SubscriptionRecord(
            transaction_code="CE",
            obfuscated_key="COK001",
            account="123",
            account_group="GRP",
            meter="MTR1",
            email="new@user.com",
            termination_date=None,
            rate="TOU-DR1",
        )]
        result = process_subscription_records(records)
        assert result["enrolled"] == 1

        conn = UtilityConnection.query.filter_by(obfuscated_key="COK001").first()
        assert conn is not None
        assert conn.status == "pending_data"
        assert conn.meter_number == "MTR1"
        assert conn.rate_tariff == "TOU-DR1"
        assert conn.account_group == "GRP"

    def test_duplicate_ce_is_idempotent(self, ctx):
        records = [SubscriptionRecord(
            transaction_code="CE",
            obfuscated_key="COK_DUP",
            account="123",
            account_group="GRP",
            meter="MTR1",
            email="dup@user.com",
            termination_date=None,
            rate="TOU",
        )]
        process_subscription_records(records)
        result = process_subscription_records(records)
        assert result["skipped"] == 1
        assert result["enrolled"] == 0

        # Only one connection exists
        conns = UtilityConnection.query.filter_by(obfuscated_key="COK_DUP").all()
        assert len(conns) == 1


# ---------------------------------------------------------------------------
# CU (Customer Un-Enrollment) handler
# ---------------------------------------------------------------------------

class TestHandleUnenrollment:

    def _seed_enrolled(self):
        conn = UtilityConnection(
            user_id="u1",
            audit_id=1,
            utility_name="SDGE",
            provider_name="sdge_cmd",
            data_scope="electric",
            status="connected",
            obfuscated_key="COK_CU",
            meter_number="MTR1",
        )
        db.session.add(conn)
        db.session.commit()
        return conn

    def test_unenrollment_revokes(self, ctx):
        self._seed_enrolled()
        records = [SubscriptionRecord(
            transaction_code="CU",
            obfuscated_key="COK_CU",
            account="123",
            account_group="GRP",
            meter="MTR1",
            email="u@e.com",
            termination_date="2026-06-30",
            rate="TOU",
        )]
        result = process_subscription_records(records)
        assert result["unenrolled"] == 1

        conn = UtilityConnection.query.filter_by(obfuscated_key="COK_CU").first()
        assert conn.status == "revoked"

    def test_unenrollment_unknown_key_skipped(self, ctx):
        records = [SubscriptionRecord(
            transaction_code="CU",
            obfuscated_key="UNKNOWN",
            account="123",
            account_group="GRP",
            meter="MTR1",
            email="u@e.com",
            termination_date="2026-06-30",
            rate="TOU",
        )]
        result = process_subscription_records(records)
        assert result["skipped"] == 1

    def test_duplicate_cu_is_idempotent(self, ctx):
        self._seed_enrolled()
        rec = SubscriptionRecord(
            transaction_code="CU",
            obfuscated_key="COK_CU",
            account="123",
            account_group="GRP",
            meter="MTR1",
            email="u@e.com",
            termination_date="2026-06-30",
            rate="TOU",
        )
        process_subscription_records([rec])
        result = process_subscription_records([rec])
        assert result["skipped"] == 1
        assert result["unenrolled"] == 0


# ---------------------------------------------------------------------------
# MC (Meter Change) handler
# ---------------------------------------------------------------------------

class TestHandleMeterChange:

    def _seed_enrolled(self):
        conn = UtilityConnection(
            user_id="u1",
            audit_id=1,
            utility_name="SDGE",
            provider_name="sdge_cmd",
            data_scope="electric",
            status="connected",
            obfuscated_key="COK_MC",
            meter_number="OLD_MTR",
        )
        db.session.add(conn)
        db.session.commit()
        return conn

    def test_meter_change_updates(self, ctx):
        self._seed_enrolled()
        records = [SubscriptionRecord(
            transaction_code="MC",
            obfuscated_key="COK_MC",
            account="123",
            account_group="GRP",
            meter="NEW_MTR",
            email="u@e.com",
            termination_date=None,
            rate="TOU",
        )]
        result = process_subscription_records(records)
        assert result["meter_changed"] == 1

        conn = UtilityConnection.query.filter_by(obfuscated_key="COK_MC").first()
        assert conn.meter_number == "NEW_MTR"
        assert "meter_changes" in conn.provider_metadata
        assert conn.provider_metadata["meter_changes"][0]["old"] == "OLD_MTR"
        assert conn.provider_metadata["meter_changes"][0]["new"] == "NEW_MTR"

    def test_meter_change_noop_same_meter(self, ctx):
        self._seed_enrolled()
        records = [SubscriptionRecord(
            transaction_code="MC",
            obfuscated_key="COK_MC",
            account="123",
            account_group="GRP",
            meter="OLD_MTR",
            email="u@e.com",
            termination_date=None,
            rate="TOU",
        )]
        result = process_subscription_records(records)
        assert result["skipped"] == 1

    def test_meter_change_unknown_key_skipped(self, ctx):
        records = [SubscriptionRecord(
            transaction_code="MC",
            obfuscated_key="MISSING",
            account="123",
            account_group="GRP",
            meter="NEW",
            email="u@e.com",
            termination_date=None,
            rate="TOU",
        )]
        result = process_subscription_records(records)
        assert result["skipped"] == 1
