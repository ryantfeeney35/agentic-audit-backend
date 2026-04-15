# backend/utils/sdge_subscriptions.py
"""
SDG&E Subscription CSV Parser & Handlers

Parses SDG&E subscription CSV files delivered via SFTP and processes
enrollment (CE), un-enrollment (CU), and meter-change (MC) events.

CSV format (comma-delimited, no header row):
  Transaction Code, Obfuscated Key, Account, Account Group, Meter, Email, Termination Date, Rate

Transaction codes:
  CE = Customer Enrollment
  CU = Customer Un-Enrollment
  MC = Meter Change
"""

import csv
import io
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class SubscriptionRecord:
    """Single row from an SDG&E subscription CSV."""
    transaction_code: str   # CE, CU, or MC
    obfuscated_key: str
    account: str
    account_group: str
    meter: str
    email: str
    termination_date: Optional[str]  # May be blank for CE
    rate: str


def parse_subscription_csv(csv_content: str) -> List[SubscriptionRecord]:
    """
    Parse SDG&E subscription CSV content into typed records.

    Args:
        csv_content: Raw CSV string (no header row expected).

    Returns:
        List of SubscriptionRecord dataclass instances.
    """
    records: List[SubscriptionRecord] = []
    reader = csv.reader(io.StringIO(csv_content))

    for line_num, row in enumerate(reader, start=1):
        # Skip blank lines
        if not row or all(c.strip() == "" for c in row):
            continue

        if len(row) < 8:
            logger.warning(
                "Subscription CSV line %d has %d fields (expected 8), skipping: %s",
                line_num, len(row), row,
            )
            continue

        txn_code = row[0].strip().upper()
        if txn_code not in ("CE", "CU", "MC"):
            logger.warning(
                "Unknown transaction code '%s' on line %d, skipping", txn_code, line_num
            )
            continue

        records.append(SubscriptionRecord(
            transaction_code=txn_code,
            obfuscated_key=row[1].strip(),
            account=row[2].strip(),
            account_group=row[3].strip(),
            meter=row[4].strip(),
            email=row[5].strip(),
            termination_date=row[6].strip() or None,
            rate=row[7].strip(),
        ))

    logger.info("Parsed %d subscription records from CSV", len(records))
    return records


# ---------------------------------------------------------------------------
# Transaction handlers
# ---------------------------------------------------------------------------

def _get_deps():
    """Lazy imports to avoid circular dependencies."""
    from app import db
    from models import UtilityConnection
    return db, UtilityConnection


def process_subscription_records(records: List[SubscriptionRecord]) -> Dict[str, Any]:
    """
    Process a batch of subscription records.

    Dispatches each record to the appropriate handler (CE/CU/MC).
    All operations are idempotent — re-processing the same CSV is safe.

    Returns:
        Summary dict: {enrolled, unenrolled, meter_changed, skipped, errors}
    """
    summary: Dict[str, int] = {
        "enrolled": 0,
        "unenrolled": 0,
        "meter_changed": 0,
        "skipped": 0,
        "errors": 0,
    }

    for rec in records:
        try:
            if rec.transaction_code == "CE":
                if _handle_enrollment(rec):
                    summary["enrolled"] += 1
                else:
                    summary["skipped"] += 1
            elif rec.transaction_code == "CU":
                if _handle_unenrollment(rec):
                    summary["unenrolled"] += 1
                else:
                    summary["skipped"] += 1
            elif rec.transaction_code == "MC":
                if _handle_meter_change(rec):
                    summary["meter_changed"] += 1
                else:
                    summary["skipped"] += 1
        except Exception as e:
            logger.exception("Error processing subscription record %s: %s", rec, e)
            summary["errors"] += 1

    db, _ = _get_deps()
    db.session.commit()

    logger.info("Subscription processing complete: %s", summary)
    return summary


def _handle_enrollment(rec: SubscriptionRecord) -> bool:
    """
    Handle CE (Customer Enrollment).

    1. Check idempotency — skip if obfuscated_key already enrolled.
    2. Try to match by email to an existing pending UtilityConnection.
    3. If no match, create a placeholder connection for manual review.

    Returns True if a new enrollment was processed, False if skipped.
    """
    db, UtilityConnection = _get_deps()

    # Idempotency: skip if already enrolled with this obfuscated key
    existing = UtilityConnection.query.filter_by(
        obfuscated_key=rec.obfuscated_key,
        utility_name="SDGE",
    ).first()
    if existing and existing.status not in ("revoked",):
        logger.debug("Skipping duplicate CE for obfuscated_key=%s", rec.obfuscated_key)
        return False

    if existing and existing.status == "revoked":
        # Re-enrollment after a previous un-enrollment
        conn = existing
    else:
        # Try to match by email to a pending connection
        pending = UtilityConnection.query.filter_by(
            utility_name="SDGE",
            provider_name="sdge_cmd",
        ).filter(
            UtilityConnection.status.in_(["pending_authorization", "not_connected"]),
        ).all()
        conn = None
        for candidate in pending:
            meta = candidate.provider_metadata or {}
            if meta.get("email") == rec.email:
                conn = candidate
                break

        if conn is None:
            # No match — see if there's a connection for this user by email
            # (broader search using the user relationship isn't possible here
            #  since we only have email; create a pending_data placeholder)
            conn = UtilityConnection(
                user_id="pending",  # Will be resolved when user is matched
                audit_id=0,         # Placeholder — resolved on match
                utility_name="SDGE",
                provider_name="sdge_cmd",
                data_scope="electric",
                status="pending_data",
                provider_metadata={"email": rec.email, "source": "subscription_csv"},
            )
            db.session.add(conn)
            logger.info(
                "Created pending_data connection for unmatched CE email=%s, cok=%s",
                rec.email, rec.obfuscated_key,
            )

    # Populate subscription fields
    conn.obfuscated_key = rec.obfuscated_key
    conn.meter_number = rec.meter
    conn.rate_tariff = rec.rate
    conn.account_group = rec.account_group
    if conn.status != "pending_data":
        conn.status = "connected"

    # Store account in provider_metadata
    metadata = conn.provider_metadata or {}
    metadata["account"] = rec.account
    metadata["enrollment_date"] = datetime.utcnow().isoformat()
    conn.provider_metadata = metadata

    return True


def _handle_unenrollment(rec: SubscriptionRecord) -> bool:
    """
    Handle CU (Customer Un-Enrollment).

    Find connection by obfuscated_key, revoke it, clear tokens.
    Returns True if a connection was revoked, False if not found or already revoked.
    """
    db, UtilityConnection = _get_deps()

    conn = UtilityConnection.query.filter_by(
        obfuscated_key=rec.obfuscated_key,
        utility_name="SDGE",
    ).first()

    if not conn:
        logger.warning("CU for unknown obfuscated_key=%s", rec.obfuscated_key)
        return False

    if conn.status == "revoked":
        logger.debug("Skipping duplicate CU for obfuscated_key=%s", rec.obfuscated_key)
        return False

    # Revoke: clear tokens and mark as revoked
    conn.access_token = None
    conn.refresh_token = None
    conn.token_expires_at = None
    conn.status = "revoked"

    metadata = conn.provider_metadata or {}
    metadata["termination_date"] = rec.termination_date
    metadata["revoked_at"] = datetime.utcnow().isoformat()
    metadata["revoked_by"] = "subscription_csv"
    conn.provider_metadata = metadata

    logger.info(
        "Un-enrolled connection id=%s, obfuscated_key=%s",
        conn.id, rec.obfuscated_key,
    )
    return True


def _handle_meter_change(rec: SubscriptionRecord) -> bool:
    """
    Handle MC (Meter Change).

    Find connection by obfuscated_key, update meter_number.
    Returns True if meter was updated, False if not found or unchanged.
    """
    db, UtilityConnection = _get_deps()

    conn = UtilityConnection.query.filter_by(
        obfuscated_key=rec.obfuscated_key,
        utility_name="SDGE",
    ).first()

    if not conn:
        logger.warning("MC for unknown obfuscated_key=%s", rec.obfuscated_key)
        return False

    old_meter = conn.meter_number
    new_meter = rec.meter

    if old_meter == new_meter:
        logger.debug("Skipping no-op MC for obfuscated_key=%s", rec.obfuscated_key)
        return False

    conn.meter_number = new_meter

    metadata = conn.provider_metadata or {}
    meter_changes = metadata.get("meter_changes", [])
    meter_changes.append({
        "old": old_meter,
        "new": new_meter,
        "changed_at": datetime.utcnow().isoformat(),
    })
    metadata["meter_changes"] = meter_changes
    conn.provider_metadata = metadata

    logger.info(
        "Meter change for obfuscated_key=%s: %s -> %s",
        rec.obfuscated_key, old_meter, new_meter,
    )
    return True
