"""
Enphase Telemetry Sync

Handles fetching and storing telemetry data from Enphase API.
Supports initial sync (12 months) and incremental sync (since last sync).
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

from models import db, EnphaseConnection, EnphaseTelemetryInterval
from utils.enphase import EnphaseClient, EnphaseAuthError

logger = logging.getLogger(__name__)

# Telemetry types to fetch
TELEMETRY_TYPES = ['production', 'consumption', 'battery']

# Max raw payload size (10KB)
MAX_PAYLOAD_SIZE = 10 * 1024


def sync_telemetry(
    connection: EnphaseConnection,
    months: int = 12,
    incremental: bool = True
) -> Dict[str, Any]:
    """
    Sync telemetry data from Enphase API.
    
    Args:
        connection: EnphaseConnection with valid tokens
        months: Number of months to sync (for initial sync)
        incremental: If True and data exists, only fetch new intervals
        
    Returns:
        Dict with sync results: records_synced, date_range, errors
    """
    logger.info(
        f"ENPHASE_SYNC_START | connection={connection.id} "
        f"system={connection.system_id} months={months} incremental={incremental}"
    )
    
    client = EnphaseClient()
    
    # Determine time range
    end_at = datetime.utcnow()
    
    if incremental and connection.last_sync_at:
        # Incremental: start from last sync (with 1 hour overlap for safety)
        start_at = connection.last_sync_at - timedelta(hours=1)
        logger.info(f"ENPHASE_SYNC_INCREMENTAL | start={start_at.isoformat()}")
    else:
        # Initial: fetch N months of history
        start_at = end_at - timedelta(days=30 * months)
        logger.info(f"ENPHASE_SYNC_INITIAL | start={start_at.isoformat()} months={months}")
    
    # Check and refresh token if needed
    if connection.token_expires_at and connection.token_expires_at <= datetime.utcnow() + timedelta(minutes=5):
        logger.info("ENPHASE_SYNC_TOKEN_REFRESH | Refreshing expired token")
        try:
            tokens = client.refresh_access_token(connection.refresh_token)
            connection.access_token = tokens.access_token
            connection.refresh_token = tokens.refresh_token
            connection.token_expires_at = tokens.expires_at
            db.session.commit()
        except EnphaseAuthError as e:
            logger.error(f"ENPHASE_SYNC_TOKEN_REFRESH_FAILED | error={e}")
            raise
    
    records_synced = 0
    errors = []
    
    # Fetch each telemetry type
    for telemetry_type in TELEMETRY_TYPES:
        try:
            type_records = _fetch_and_store_telemetry(
                client=client,
                connection=connection,
                telemetry_type=telemetry_type,
                start_at=start_at,
                end_at=end_at
            )
            records_synced += type_records
            logger.info(f"ENPHASE_SYNC_TYPE_SUCCESS | type={telemetry_type} records={type_records}")
        except Exception as e:
            logger.error(f"ENPHASE_SYNC_TYPE_ERROR | type={telemetry_type} error={e}")
            errors.append(f"{telemetry_type}: {e}")
    
    # Also fetch grid data (import/export)
    try:
        grid_records = _fetch_and_store_grid_telemetry(
            client=client,
            connection=connection,
            start_at=start_at,
            end_at=end_at
        )
        records_synced += grid_records
        logger.info(f"ENPHASE_SYNC_TYPE_SUCCESS | type=grid records={grid_records}")
    except Exception as e:
        logger.error(f"ENPHASE_SYNC_TYPE_ERROR | type=grid error={e}")
        errors.append(f"grid: {e}")
    
    logger.info(
        f"ENPHASE_SYNC_COMPLETE | connection={connection.id} "
        f"records={records_synced} errors={len(errors)}"
    )
    
    return {
        'records_synced': records_synced,
        'date_range': {
            'start': start_at.isoformat(),
            'end': end_at.isoformat()
        },
        'errors': errors if errors else None
    }


def _fetch_and_store_telemetry(
    client: EnphaseClient,
    connection: EnphaseConnection,
    telemetry_type: str,
    start_at: datetime,
    end_at: datetime
) -> int:
    """Fetch and store a single telemetry type."""
    
    # Fetch from API
    points = client.get_telemetry(
        access_token=connection.access_token,
        system_id=connection.system_id,
        telemetry_type=telemetry_type,
        start_at=start_at,
        end_at=end_at,
        granularity='15mins'
    )
    
    if not points:
        return 0
    
    records_created = 0
    
    for point in points:
        # Calculate interval start from end timestamp (15 min prior)
        interval_end = point.timestamp
        interval_start = interval_end - timedelta(minutes=15)
        
        # Prepare telemetry values (convert Wh to kWh)
        values = {}
        if telemetry_type == 'production' and point.production_wh is not None:
            values['production_kwh'] = point.production_wh / 1000
        elif telemetry_type == 'consumption' and point.consumption_wh is not None:
            values['consumption_kwh'] = point.consumption_wh / 1000
        elif telemetry_type == 'battery':
            if point.battery_charge_wh is not None:
                values['battery_charge_kwh'] = point.battery_charge_wh / 1000
            if point.battery_discharge_wh is not None:
                values['battery_discharge_kwh'] = point.battery_discharge_wh / 1000
        
        if not values:
            continue
        
        # Upsert: find existing or create new
        existing = EnphaseTelemetryInterval.query.filter_by(
            connection_id=connection.id,
            interval_start=interval_start
        ).first()
        
        if existing:
            # Update existing record with new values
            for key, value in values.items():
                setattr(existing, key, value)
            existing.updated_at = datetime.utcnow()
        else:
            # Create new record
            interval = EnphaseTelemetryInterval(
                audit_id=connection.audit_id,
                property_id=connection.property_id,
                connection_id=connection.id,
                system_id=connection.system_id,
                interval_start=interval_start,
                interval_end=interval_end,
                granularity='15m',
                **values
            )
            db.session.add(interval)
            records_created += 1
    
    db.session.commit()
    return records_created


def _fetch_and_store_grid_telemetry(
    client: EnphaseClient,
    connection: EnphaseConnection,
    start_at: datetime,
    end_at: datetime
) -> int:
    """Fetch and store grid import/export telemetry."""
    
    # Fetch grid data from API
    points = client.get_telemetry(
        access_token=connection.access_token,
        system_id=connection.system_id,
        telemetry_type='grid',
        start_at=start_at,
        end_at=end_at,
        granularity='15mins'
    )
    
    if not points:
        return 0
    
    records_updated = 0
    
    for point in points:
        interval_end = point.timestamp
        interval_start = interval_end - timedelta(minutes=15)
        
        # Grid data updates existing records (or creates new ones)
        existing = EnphaseTelemetryInterval.query.filter_by(
            connection_id=connection.id,
            interval_start=interval_start
        ).first()
        
        if existing:
            if point.grid_import_wh is not None:
                existing.grid_import_kwh = point.grid_import_wh / 1000
            if point.grid_export_wh is not None:
                existing.grid_export_kwh = point.grid_export_wh / 1000
            existing.updated_at = datetime.utcnow()
            records_updated += 1
        else:
            # Create new record with grid data only
            interval = EnphaseTelemetryInterval(
                audit_id=connection.audit_id,
                property_id=connection.property_id,
                connection_id=connection.id,
                system_id=connection.system_id,
                interval_start=interval_start,
                interval_end=interval_end,
                granularity='15m',
                grid_import_kwh=point.grid_import_wh / 1000 if point.grid_import_wh else None,
                grid_export_kwh=point.grid_export_wh / 1000 if point.grid_export_wh else None,
            )
            db.session.add(interval)
            records_updated += 1
    
    db.session.commit()
    return records_updated


def _truncate_payload(payload: dict) -> Optional[str]:
    """Truncate raw payload if it exceeds size limit."""
    if payload is None:
        return None
    
    json_str = json.dumps(payload)
    
    if len(json_str) <= MAX_PAYLOAD_SIZE:
        return json_str
    
    # Truncate by removing verbose fields
    truncated = {
        'truncated': True,
        'original_size': len(json_str),
        'summary': {k: v for k, v in payload.items() if not isinstance(v, (list, dict))}
    }
    
    return json.dumps(truncated)
