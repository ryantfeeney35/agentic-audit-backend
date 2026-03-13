"""
Enphase Routes Blueprint

Routes for Enphase API integration including:
- OAuth authorization flow
- Connection management
- Telemetry data retrieval and sync
"""

import os
import secrets
import logging
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, g, redirect, url_for
from auth import require_auth
from models import db, EnphaseConnection, EnphaseTelemetryInterval, Audit
from utils.enphase import EnphaseClient, EnphaseAuthError, EnphaseAPIError

logger = logging.getLogger(__name__)

# Blueprint for Enphase-specific routes
enphase_bp = Blueprint('enphase', __name__, url_prefix='/api/enphase')

# Blueprint for audit-scoped Enphase routes
audit_enphase_bp = Blueprint('audit_enphase', __name__, url_prefix='/api/audits')


def get_enphase_client() -> EnphaseClient:
    """Get or create EnphaseClient instance."""
    return EnphaseClient()


# ============================================================================
# OAuth Routes
# ============================================================================

@enphase_bp.route('/connect', methods=['POST'])
@require_auth
def initiate_connection():
    """
    POST /api/enphase/connect
    
    Initiate Enphase OAuth authorization flow.
    
    Body:
    {
        "audit_id": 123,
        "property_id": 456  // optional
    }
    
    Response:
    {
        "auth_url": "https://api.enphaseenergy.com/oauth/authorize?...",
        "state": "...",
        "connection_id": 789
    }
    """
    try:
        data = request.get_json() or {}
        user_id = g.current_user['id']
        
        # Validate required fields
        audit_id = data.get('audit_id')
        if not audit_id:
            return jsonify({'error': 'audit_id is required'}), 400
        
        # Verify audit exists and belongs to user
        audit = Audit.query.filter_by(id=audit_id, user_id=user_id).first()
        if not audit:
            return jsonify({'error': 'Audit not found'}), 404
        
        # Check for existing active connection
        existing = EnphaseConnection.query.filter_by(
            audit_id=audit_id,
            user_id=user_id
        ).filter(
            EnphaseConnection.status.in_(['connected', 'pending_authorization', 'sync_in_progress'])
        ).first()
        
        if existing:
            if existing.status == 'connected':
                return jsonify({
                    'error': 'Active Enphase connection already exists',
                    'connection_id': existing.id,
                    'status': existing.status
                }), 409
            elif existing.status == 'pending_authorization':
                # Return existing pending auth URL
                client = get_enphase_client()
                auth_url = client.get_authorization_url(existing.oauth_state)
                return jsonify({
                    'authorization_url': auth_url,
                    'state': existing.oauth_state,
                    'connection_id': existing.id
                }), 200
        
        # Generate CSRF state token
        oauth_state = secrets.token_urlsafe(32)
        
        # Create pending connection
        connection = EnphaseConnection(
            user_id=user_id,
            audit_id=audit_id,
            property_id=data.get('property_id'),
            status='pending_authorization',
            oauth_state=oauth_state
        )
        db.session.add(connection)
        db.session.commit()
        
        # Generate authorization URL
        client = get_enphase_client()
        auth_url = client.get_authorization_url(oauth_state)
        
        logger.info(
            "ENPHASE_CONNECTION_INITIATED | audit=%s user=%s connection=%s",
            audit_id, user_id, connection.id
        )
        
        return jsonify({
            'authorization_url': auth_url,
            'state': oauth_state,
            'connection_id': connection.id
        }), 200
        
    except Exception as e:
        logger.error(f"ENPHASE_CONNECT_ERROR | error={e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@enphase_bp.route('/oauth/callback', methods=['GET'])
def oauth_callback():
    """
    GET /api/enphase/oauth/callback
    
    Handle OAuth callback from Enphase.
    Exchanges authorization code for tokens and discovers systems.
    
    Query params:
    - code: Authorization code from Enphase
    - state: CSRF state token
    - error: Error code if authorization failed
    - error_description: Human-readable error message
    """
    try:
        # Check for OAuth error
        error = request.args.get('error')
        if error:
            error_description = request.args.get('error_description', 'Authorization denied')
            state = request.args.get('state')
            
            logger.warning(f"ENPHASE_OAUTH_ERROR | error={error} description={error_description}")
            
            # Update connection status if we can find it
            if state:
                connection = EnphaseConnection.query.filter_by(oauth_state=state).first()
                if connection:
                    connection.status = 'failed'
                    connection.last_sync_error = f"{error}: {error_description}"
                    db.session.commit()
            
            # Redirect to app with error
            return redirect(f"/enphase/callback?error={error}&description={error_description}")
        
        # Get authorization code and state
        code = request.args.get('code')
        state = request.args.get('state')
        
        if not code or not state:
            logger.error("ENPHASE_OAUTH_CALLBACK_INVALID | Missing code or state")
            return redirect("/enphase/callback?error=invalid_request&description=Missing+code+or+state")
        
        # Validate state and find connection (CSRF protection)
        connection = EnphaseConnection.query.filter_by(oauth_state=state).first()
        if not connection:
            logger.error(f"ENPHASE_OAUTH_STATE_INVALID | state={state[:10]}...")
            return redirect("/enphase/callback?error=invalid_state&description=Invalid+state+parameter")
        
        if connection.status != 'pending_authorization':
            logger.warning(f"ENPHASE_OAUTH_CALLBACK_DUPLICATE | connection={connection.id}")
            return redirect(f"/enphase/callback?success=true&connection_id={connection.id}")
        
        # Exchange code for tokens
        client = get_enphase_client()
        try:
            tokens = client.exchange_code_for_tokens(code)
        except EnphaseAuthError as e:
            logger.error(f"ENPHASE_TOKEN_EXCHANGE_FAILED | connection={connection.id} error={e}")
            connection.status = 'failed'
            connection.last_sync_error = str(e)
            db.session.commit()
            return redirect(f"/enphase/callback?error=token_exchange_failed&description={e}")
        
        # Store encrypted tokens
        connection.access_token = tokens.access_token
        connection.refresh_token = tokens.refresh_token
        connection.token_expires_at = tokens.expires_at
        connection.oauth_state = None  # Clear state after use
        
        # Discover systems
        try:
            systems = client.get_systems(tokens.access_token)
            
            if not systems:
                logger.warning(f"ENPHASE_NO_SYSTEMS | connection={connection.id}")
                connection.status = 'failed'
                connection.last_sync_error = "No Enphase systems found"
                db.session.commit()
                return redirect("/enphase/callback?error=no_systems&description=No+Enphase+systems+found")
            
            # Use first system as primary, store all in metadata
            primary_system = systems[0]
            connection.system_id = primary_system.system_id
            connection.system_name = primary_system.name
            connection.provider_metadata = {
                'systems': [
                    {
                        'system_id': s.system_id,
                        'name': s.name,
                        'status': s.status,
                        'timezone': s.timezone
                    } for s in systems
                ],
                'primary_system': primary_system.system_id
            }
            connection.status = 'connected'
            
            logger.info(
                f"ENPHASE_CONNECTION_SUCCESS | connection={connection.id} "
                f"system={connection.system_id} systems_count={len(systems)}"
            )
            
        except EnphaseAPIError as e:
            logger.error(f"ENPHASE_SYSTEM_DISCOVERY_FAILED | connection={connection.id} error={e}")
            # Still mark as connected - we have valid tokens
            connection.status = 'connected'
            connection.last_sync_error = f"System discovery failed: {e}"
        
        db.session.commit()
        
        # Redirect to app with success
        return redirect(f"/enphase/callback?success=true&connection_id={connection.id}")
        
    except Exception as e:
        logger.error(f"ENPHASE_OAUTH_CALLBACK_ERROR | error={e}")
        db.session.rollback()
        return redirect(f"/enphase/callback?error=internal_error&description={e}")


# ============================================================================
# Connection Management Routes (audit-scoped)
# ============================================================================

@audit_enphase_bp.route('/<int:audit_id>/enphase-connection', methods=['GET'])
@require_auth
def get_connection(audit_id: int):
    """
    GET /api/audits/{id}/enphase-connection
    
    Get Enphase connection status for an audit.
    
    Response:
    {
        "connection": {
            "id": 123,
            "status": "connected",
            "system_id": "...",
            "last_sync_at": "...",
            "systems": [...]
        } | null,
        "summary": {...} | null
    }
    """
    try:
        user_id = g.current_user['id']
        
        # Verify audit access
        audit = Audit.query.filter_by(id=audit_id, user_id=user_id).first()
        if not audit:
            return jsonify({'error': 'Audit not found'}), 404
        
        # Get active connection
        connection = EnphaseConnection.query.filter_by(
            audit_id=audit_id,
            user_id=user_id
        ).filter(
            EnphaseConnection.status.notin_(['disconnected', 'failed'])
        ).first()
        
        if not connection:
            return jsonify({'connection': None, 'summary': None}), 200
        
        # Build connection response
        connection_data = {
            'id': connection.id,
            'status': connection.status,
            'system_id': connection.system_id,
            'system_name': connection.system_name,
            'last_sync_at': connection.last_sync_at.isoformat() if connection.last_sync_at else None,
            'last_sync_error': connection.last_sync_error,
            'provider_metadata': connection.provider_metadata,
            'systems': connection.provider_metadata.get('systems', []),
            'created_at': connection.created_at.isoformat()
        }
        
        # Calculate summary from telemetry if available
        summary = None
        if connection.status == 'connected' and connection.last_sync_at:
            summary = _calculate_telemetry_summary(connection.id)
        
        return jsonify({
            'connection': connection_data,
            'summary': summary
        }), 200
        
    except Exception as e:
        logger.error(f"ENPHASE_GET_CONNECTION_ERROR | audit={audit_id} error={e}")
        return jsonify({'error': str(e)}), 500


@audit_enphase_bp.route('/<int:audit_id>/enphase-connection', methods=['DELETE'])
@require_auth
def disconnect(audit_id: int):
    """
    DELETE /api/audits/{id}/enphase-connection
    
    Disconnect Enphase account. Retains telemetry data but clears tokens.
    """
    try:
        user_id = g.current_user['id']
        
        # Verify audit access
        audit = Audit.query.filter_by(id=audit_id, user_id=user_id).first()
        if not audit:
            return jsonify({'error': 'Audit not found'}), 404
        
        # Find connection
        connection = EnphaseConnection.query.filter_by(
            audit_id=audit_id,
            user_id=user_id
        ).filter(
            EnphaseConnection.status != 'disconnected'
        ).first()
        
        if not connection:
            return jsonify({'error': 'No active connection found'}), 404
        
        # Clear tokens and mark disconnected
        connection.access_token = None
        connection.refresh_token = None
        connection.token_expires_at = None
        connection.status = 'disconnected'
        
        db.session.commit()
        
        logger.info(f"ENPHASE_DISCONNECTED | connection={connection.id} audit={audit_id}")
        
        return jsonify({'success': True}), 200
        
    except Exception as e:
        logger.error(f"ENPHASE_DISCONNECT_ERROR | audit={audit_id} error={e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@audit_enphase_bp.route('/<int:audit_id>/enphase-connection/sync', methods=['POST'])
@require_auth
def trigger_sync(audit_id: int):
    """
    POST /api/audits/{id}/enphase-connection/sync
    
    Trigger manual telemetry data sync.
    
    Body (optional):
    {
        "months": 12  // Number of months to sync (default: 12)
    }
    """
    try:
        user_id = g.current_user['id']
        data = request.get_json() or {}
        
        # Verify audit access
        audit = Audit.query.filter_by(id=audit_id, user_id=user_id).first()
        if not audit:
            return jsonify({'error': 'Audit not found'}), 404
        
        # Find active connection
        connection = EnphaseConnection.query.filter_by(
            audit_id=audit_id,
            user_id=user_id,
            status='connected'
        ).first()
        
        if not connection:
            return jsonify({'error': 'No active Enphase connection'}), 404
        
        if not connection.system_id:
            return jsonify({'error': 'No Enphase system associated with connection'}), 400
        
        # Check token expiration and refresh if needed
        if connection.token_expires_at and connection.token_expires_at <= datetime.utcnow() + timedelta(minutes=5):
            try:
                client = get_enphase_client()
                tokens = client.refresh_access_token(connection.refresh_token)
                connection.access_token = tokens.access_token
                connection.refresh_token = tokens.refresh_token
                connection.token_expires_at = tokens.expires_at
                db.session.commit()
            except EnphaseAuthError:
                connection.status = 'requires_reauthorization'
                db.session.commit()
                return jsonify({
                    'error': 'Token expired and refresh failed',
                    'requires_reauthorization': True
                }), 401
        
        # Update status
        connection.status = 'sync_in_progress'
        db.session.commit()
        
        # Perform sync
        months = data.get('months', 12)
        try:
            from utils.enphase.sync import sync_telemetry
            result = sync_telemetry(connection, months=months)
            
            connection.status = 'connected'
            connection.last_sync_at = datetime.utcnow()
            connection.last_sync_error = None
            db.session.commit()
            
            logger.info(
                f"ENPHASE_SYNC_SUCCESS | connection={connection.id} "
                f"records={result.get('records_synced', 0)}"
            )
            
            return jsonify({
                'success': True,
                'records_synced': result.get('records_synced', 0),
                'date_range': result.get('date_range')
            }), 200
            
        except Exception as e:
            logger.error(f"ENPHASE_SYNC_FAILED | connection={connection.id} error={e}")
            connection.status = 'connected'  # Keep connected, just log error
            connection.last_sync_error = str(e)
            db.session.commit()
            return jsonify({'error': f'Sync failed: {e}'}), 500
        
    except Exception as e:
        logger.error(f"ENPHASE_SYNC_ERROR | audit={audit_id} error={e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# ============================================================================
# Telemetry Routes
# ============================================================================

@audit_enphase_bp.route('/<int:audit_id>/enphase-telemetry', methods=['GET'])
@require_auth
def get_telemetry(audit_id: int):
    """
    GET /api/audits/{id}/enphase-telemetry
    
    Query stored telemetry data.
    
    Query params:
    - start_date: ISO date string (default: 12 months ago)
    - end_date: ISO date string (default: now)
    - aggregate: 'daily' | 'monthly' | None (default: None = raw intervals)
    - limit: Max records (default: 1000)
    - offset: Pagination offset (default: 0)
    
    Response:
    {
        "intervals": [...],
        "pagination": {"total": N, "limit": M, "offset": O},
        "summary": {...}
    }
    """
    try:
        user_id = g.current_user['id']
        
        # Verify audit access
        audit = Audit.query.filter_by(id=audit_id, user_id=user_id).first()
        if not audit:
            return jsonify({'error': 'Audit not found'}), 404
        
        # Get connection
        connection = EnphaseConnection.query.filter_by(
            audit_id=audit_id,
            user_id=user_id
        ).first()
        
        if not connection:
            return jsonify({
                'intervals': [],
                'pagination': {'total': 0, 'limit': 0, 'offset': 0},
                'summary': None
            }), 200
        
        # Parse query params
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        aggregate = request.args.get('aggregate')
        limit = min(int(request.args.get('limit', 1000)), 10000)
        offset = int(request.args.get('offset', 0))
        
        # Build query
        query = EnphaseTelemetryInterval.query.filter_by(connection_id=connection.id)
        
        if start_date:
            query = query.filter(EnphaseTelemetryInterval.interval_start >= datetime.fromisoformat(start_date))
        if end_date:
            query = query.filter(EnphaseTelemetryInterval.interval_end <= datetime.fromisoformat(end_date))
        
        total = query.count()
        
        # Handle aggregation
        if aggregate == 'daily':
            intervals = _aggregate_daily(query)
        elif aggregate == 'monthly':
            intervals = _aggregate_monthly(query)
        else:
            intervals = query.order_by(EnphaseTelemetryInterval.interval_start).offset(offset).limit(limit).all()
            intervals = [_serialize_interval(i) for i in intervals]
        
        # Calculate summary
        summary = _calculate_telemetry_summary(connection.id)
        
        return jsonify({
            'intervals': intervals,
            'pagination': {
                'total': total,
                'limit': limit,
                'offset': offset
            },
            'summary': summary
        }), 200
        
    except Exception as e:
        logger.error(f"ENPHASE_GET_TELEMETRY_ERROR | audit={audit_id} error={e}")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# Helper Functions
# ============================================================================

def _serialize_interval(interval: EnphaseTelemetryInterval) -> dict:
    """Serialize a telemetry interval to JSON."""
    return {
        'id': interval.id,
        'interval_start': interval.interval_start.isoformat(),
        'interval_end': interval.interval_end.isoformat(),
        'granularity': interval.granularity,
        'production_kwh': interval.production_kwh,
        'consumption_kwh': interval.consumption_kwh,
        'grid_import_kwh': interval.grid_import_kwh,
        'grid_export_kwh': interval.grid_export_kwh,
        'battery_charge_kwh': interval.battery_charge_kwh,
        'battery_discharge_kwh': interval.battery_discharge_kwh,
    }


def _calculate_telemetry_summary(connection_id: int) -> dict:
    """Calculate summary statistics from telemetry data."""
    from sqlalchemy import func
    
    # Get aggregated stats
    stats = db.session.query(
        func.sum(EnphaseTelemetryInterval.production_kwh).label('total_production'),
        func.sum(EnphaseTelemetryInterval.consumption_kwh).label('total_consumption'),
        func.sum(EnphaseTelemetryInterval.grid_import_kwh).label('total_grid_import'),
        func.sum(EnphaseTelemetryInterval.grid_export_kwh).label('total_grid_export'),
        func.sum(EnphaseTelemetryInterval.battery_charge_kwh).label('total_battery_charge'),
        func.sum(EnphaseTelemetryInterval.battery_discharge_kwh).label('total_battery_discharge'),
        func.min(EnphaseTelemetryInterval.interval_start).label('data_start'),
        func.max(EnphaseTelemetryInterval.interval_end).label('data_end'),
        func.count(EnphaseTelemetryInterval.id).label('interval_count')
    ).filter_by(connection_id=connection_id).first()
    
    if not stats or not stats.interval_count:
        return None
    
    # Calculate days of data
    if stats.data_start and stats.data_end:
        days_covered = (stats.data_end - stats.data_start).days or 1
    else:
        days_covered = 1
    
    # Calculate self-consumption percentage
    total_production = stats.total_production or 0
    total_export = stats.total_grid_export or 0
    self_consumption_pct = ((total_production - total_export) / total_production * 100) if total_production > 0 else None
    
    return {
        'total_production_kwh': round(total_production, 2) if total_production else None,
        'total_consumption_kwh': round(stats.total_consumption, 2) if stats.total_consumption else None,
        'total_grid_import_kwh': round(stats.total_grid_import, 2) if stats.total_grid_import else None,
        'total_grid_export_kwh': round(total_export, 2) if total_export else None,
        'total_battery_charge_kwh': round(stats.total_battery_charge, 2) if stats.total_battery_charge else None,
        'total_battery_discharge_kwh': round(stats.total_battery_discharge, 2) if stats.total_battery_discharge else None,
        'avg_daily_production_kwh': round(total_production / days_covered, 2) if total_production else None,
        'self_consumption_percentage': round(self_consumption_pct, 1) if self_consumption_pct else None,
        'data_start': stats.data_start.isoformat() if stats.data_start else None,
        'data_end': stats.data_end.isoformat() if stats.data_end else None,
        'days_covered': days_covered,
        'interval_count': stats.interval_count
    }


def _aggregate_daily(query) -> list:
    """Aggregate intervals to daily totals."""
    from sqlalchemy import func, cast, Date
    
    results = db.session.query(
        cast(EnphaseTelemetryInterval.interval_start, Date).label('date'),
        func.sum(EnphaseTelemetryInterval.production_kwh).label('production_kwh'),
        func.sum(EnphaseTelemetryInterval.consumption_kwh).label('consumption_kwh'),
        func.sum(EnphaseTelemetryInterval.grid_import_kwh).label('grid_import_kwh'),
        func.sum(EnphaseTelemetryInterval.grid_export_kwh).label('grid_export_kwh'),
        func.sum(EnphaseTelemetryInterval.battery_charge_kwh).label('battery_charge_kwh'),
        func.sum(EnphaseTelemetryInterval.battery_discharge_kwh).label('battery_discharge_kwh'),
    ).filter(
        EnphaseTelemetryInterval.connection_id == query.whereclause.right.value
    ).group_by(
        cast(EnphaseTelemetryInterval.interval_start, Date)
    ).order_by('date').all()
    
    return [
        {
            'date': r.date.isoformat(),
            'production_kwh': round(r.production_kwh, 3) if r.production_kwh else None,
            'consumption_kwh': round(r.consumption_kwh, 3) if r.consumption_kwh else None,
            'grid_import_kwh': round(r.grid_import_kwh, 3) if r.grid_import_kwh else None,
            'grid_export_kwh': round(r.grid_export_kwh, 3) if r.grid_export_kwh else None,
            'battery_charge_kwh': round(r.battery_charge_kwh, 3) if r.battery_charge_kwh else None,
            'battery_discharge_kwh': round(r.battery_discharge_kwh, 3) if r.battery_discharge_kwh else None,
        }
        for r in results
    ]


def _aggregate_monthly(query) -> list:
    """Aggregate intervals to monthly totals."""
    from sqlalchemy import func, extract
    
    results = db.session.query(
        extract('year', EnphaseTelemetryInterval.interval_start).label('year'),
        extract('month', EnphaseTelemetryInterval.interval_start).label('month'),
        func.sum(EnphaseTelemetryInterval.production_kwh).label('production_kwh'),
        func.sum(EnphaseTelemetryInterval.consumption_kwh).label('consumption_kwh'),
        func.sum(EnphaseTelemetryInterval.grid_import_kwh).label('grid_import_kwh'),
        func.sum(EnphaseTelemetryInterval.grid_export_kwh).label('grid_export_kwh'),
        func.sum(EnphaseTelemetryInterval.battery_charge_kwh).label('battery_charge_kwh'),
        func.sum(EnphaseTelemetryInterval.battery_discharge_kwh).label('battery_discharge_kwh'),
    ).filter(
        EnphaseTelemetryInterval.connection_id == query.whereclause.right.value
    ).group_by('year', 'month').order_by('year', 'month').all()
    
    return [
        {
            'year': int(r.year),
            'month': int(r.month),
            'production_kwh': round(r.production_kwh, 2) if r.production_kwh else None,
            'consumption_kwh': round(r.consumption_kwh, 2) if r.consumption_kwh else None,
            'grid_import_kwh': round(r.grid_import_kwh, 2) if r.grid_import_kwh else None,
            'grid_export_kwh': round(r.grid_export_kwh, 2) if r.grid_export_kwh else None,
            'battery_charge_kwh': round(r.battery_charge_kwh, 2) if r.battery_charge_kwh else None,
            'battery_discharge_kwh': round(r.battery_discharge_kwh, 2) if r.battery_discharge_kwh else None,
        }
        for r in results
    ]
