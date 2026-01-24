"""
Utility Routes Blueprint

Routes for utility connection management, OAuth callbacks, 
data synchronization, and usage summary retrieval.
"""

from flask import Blueprint, request, jsonify, g, redirect, url_for
from auth import require_auth
from models import db, UtilityConnection, UtilityUsageData, UtilityUsageSummary, Audit
from utils.providers.registry import registry
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

utility_bp = Blueprint('utility', __name__, url_prefix='/api/utility')


@utility_bp.route('/providers', methods=['GET'])
@require_auth
def get_providers():
    """
    GET /api/utility/providers
    
    Returns available utility providers and their waterfall chains.
    """
    try:
        providers_info = registry.get_available_providers()
        waterfall_chains = registry.get_waterfall_chains()
        supported_utilities = registry.get_supported_utilities()
        
        return jsonify({
            'providers': providers_info,
            'waterfall_chains': waterfall_chains,
            'supported_utilities': supported_utilities
        }), 200
        
    except Exception as e:
        logger.error(f"Error getting providers: {e}")
        return jsonify({'error': str(e)}), 500


@utility_bp.route('/connect', methods=['POST'])
@require_auth
def connect_utility():
    """
    POST /api/utility/connect
    
    Initiate utility connection using provider waterfall.
    
    Body:
    {
        "audit_id": 123,
        "utility_name": "SDGE",
        "data_scope": "electric",  // optional, defaults to "electric"
        "preferred_provider": "sdge_cmd"  // optional, skip waterfall
    }
    
    Response:
    {
        "auth_url": "https://...",
        "state": "...",
        "provider_used": "sdge_cmd",
        "connection_id": 456,
        "requires_redirect": true
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
            return jsonify({'error': 'Audit not found or access denied'}), 404
        
        # Check for existing active connection
        existing = UtilityConnection.query.filter_by(
            audit_id=audit_id,
            user_id=user_id
        ).filter(UtilityConnection.connection_status.in_([
            'connected', 'pending_authorization', 'sync_in_progress'
        ])).first()
        
        if existing:
            return jsonify({
                'error': 'Active utility connection already exists for this audit',
                'existing_connection_id': existing.id,
                'existing_status': existing.connection_status
            }), 409
        
        utility_name = data.get('utility_name', 'SDGE').upper()
        data_scope = data.get('data_scope', 'electric')
        preferred_provider = data.get('preferred_provider')
        
        # Validate data_scope
        if data_scope not in ['electric', 'gas', 'both']:
            return jsonify({'error': 'data_scope must be "electric", "gas", or "both"'}), 400
        
        # Use preferred provider if specified, otherwise use waterfall
        if preferred_provider:
            provider = registry.get_provider(preferred_provider)
            if not provider:
                return jsonify({'error': f'Unknown provider: {preferred_provider}'}), 400
            if not provider.is_available():
                return jsonify({'error': f'Provider {preferred_provider} is not available'}), 400
            
            result = provider.connect(audit_id, user_id, data_scope)
            provider_used = preferred_provider
        else:
            # Use waterfall
            result = registry.connect(audit_id, user_id, utility_name, data_scope)
            provider_used = result.provider_used if hasattr(result, 'provider_used') else None
        
        if not result.success:
            return jsonify({
                'error': result.error or 'Connection failed',
                'connection_id': result.connection_id
            }), 400
        
        response = {
            'auth_url': result.auth_url,
            'state': result.state,
            'connection_id': result.connection_id,
            'requires_redirect': result.auth_url is not None
        }
        
        # Add provider_used if available
        if provider_used:
            response['provider_used'] = provider_used
        elif result.connection_id:
            conn = UtilityConnection.query.get(result.connection_id)
            if conn:
                response['provider_used'] = conn.provider_name
        
        return jsonify(response), 200
        
    except Exception as e:
        logger.error(f"Error initiating utility connection: {e}")
        return jsonify({'error': str(e)}), 500


@utility_bp.route('/callback', methods=['GET'])
def oauth_callback():
    """
    GET /api/utility/callback
    
    Handle OAuth callback from utility provider.
    The state parameter encodes the provider and connection info.
    
    Query params: code, state, error, error_description
    
    Redirects to frontend with status.
    """
    try:
        code = request.args.get('code')
        state = request.args.get('state')
        error = request.args.get('error')
        error_description = request.args.get('error_description')
        
        if error:
            logger.error(f"OAuth error: {error} - {error_description}")
            # Redirect to frontend with error
            return redirect(f"/utility/callback?error={error}&description={error_description}")
        
        if not code or not state:
            return jsonify({'error': 'Missing code or state parameter'}), 400
        
        # Delegate to registry which routes to appropriate provider
        result = registry.handle_callback(code, state)
        
        if not result.success:
            logger.error(f"Callback handling failed: {result.error}")
            return redirect(f"/utility/callback?error=callback_failed&description={result.error}")
        
        # Trigger async data sync if connected
        if result.connection_id:
            conn = UtilityConnection.query.get(result.connection_id)
            if conn and conn.connection_status == 'connected':
                # Optionally trigger sync here or let frontend trigger it
                pass
        
        # Redirect to frontend success page
        return redirect(f"/utility/callback?success=true&connection_id={result.connection_id}")
        
    except Exception as e:
        logger.error(f"Error handling OAuth callback: {e}")
        return redirect(f"/utility/callback?error=internal_error&description={str(e)}")


@utility_bp.route('/webhook', methods=['POST'])
def utilityapi_webhook():
    """
    POST /api/utility/webhook
    
    Handle UtilityAPI async webhook notifications.
    Called when authorization completes or data becomes available.
    
    Body (from UtilityAPI):
    {
        "type": "authorization",
        "uid": "...",
        "utility": "SDGE",
        "referral": "audit_123_user_456"
    }
    """
    try:
        # Verify webhook signature
        signature = request.headers.get('X-UtilityAPI-Signature')
        provider = registry.get_provider('utilityapi')
        
        if provider and hasattr(provider, 'verify_webhook_signature'):
            raw_body = request.get_data()
            if not provider.verify_webhook_signature(raw_body, signature):
                logger.warning("Invalid webhook signature")
                return jsonify({'error': 'Invalid signature'}), 401
        
        data = request.get_json() or {}
        event_type = data.get('type')
        
        logger.info(f"UtilityAPI webhook received: {event_type}")
        
        if event_type == 'authorization':
            # Authorization complete - handle callback
            uid = data.get('uid')
            referral = data.get('referral', '')
            
            if provider and hasattr(provider, 'handle_callback'):
                result = provider.handle_callback(
                    code=uid,  # UtilityAPI uses uid as the authorization identifier
                    state=referral
                )
                
                if not result.success:
                    logger.error(f"Webhook authorization handling failed: {result.error}")
                    return jsonify({'status': 'error', 'message': result.error}), 400
                
        elif event_type in ['bills_added', 'intervals_added', 'historical_data']:
            # New data available - trigger sync
            referral = data.get('referral', '')
            if referral:
                # Parse audit_id from referral
                parts = referral.split('_')
                if len(parts) >= 2 and parts[0] == 'audit':
                    audit_id = int(parts[1])
                    conn = UtilityConnection.query.filter_by(
                        audit_id=audit_id,
                        provider_name='utilityapi',
                        connection_status='connected'
                    ).first()
                    
                    if conn:
                        result = provider.sync_usage(conn.id)
                        logger.info(f"Webhook-triggered sync: {result.success}, records: {result.records_imported}")
        
        return jsonify({'status': 'received'}), 200
        
    except Exception as e:
        logger.error(f"Error handling webhook: {e}")
        return jsonify({'error': str(e)}), 500


# Audit-scoped utility routes
audit_utility_bp = Blueprint('audit_utility', __name__, url_prefix='/api/audits/<int:audit_id>')


@audit_utility_bp.route('/utility-connection', methods=['GET'])
@require_auth
def get_utility_connection(audit_id):
    """
    GET /api/audits/<audit_id>/utility-connection
    
    Get current utility connection status and metadata for an audit.
    """
    try:
        user_id = g.current_user['id']
        
        # Verify audit access
        audit = Audit.query.filter_by(id=audit_id, user_id=user_id).first()
        if not audit:
            return jsonify({'error': 'Audit not found or access denied'}), 404
        
        # Get most recent connection for this audit
        connection = UtilityConnection.query.filter_by(
            audit_id=audit_id,
            user_id=user_id
        ).order_by(UtilityConnection.created_at.desc()).first()
        
        if not connection:
            return jsonify({
                'connected': False,
                'connection': None
            }), 200
        
        return jsonify({
            'connected': connection.connection_status == 'connected',
            'connection': {
                'id': connection.id,
                'provider_name': connection.provider_name,
                'provider_type': connection.provider_type,
                'utility_name': connection.utility_name,
                'connection_status': connection.connection_status,
                'data_scope': connection.data_scope,
                'last_sync_at': connection.last_sync_at.isoformat() if connection.last_sync_at else None,
                'error_message': connection.error_message,
                'fallback_attempted': connection.fallback_attempted,
                'fallback_provider': connection.fallback_provider,
                'created_at': connection.created_at.isoformat(),
                'updated_at': connection.updated_at.isoformat() if connection.updated_at else None
            }
        }), 200
        
    except Exception as e:
        logger.error(f"Error getting utility connection: {e}")
        return jsonify({'error': str(e)}), 500


@audit_utility_bp.route('/utility-data/sync', methods=['POST'])
@require_auth
def sync_utility_data(audit_id):
    """
    POST /api/audits/<audit_id>/utility-data/sync
    
    Trigger utility data synchronization for a connected utility.
    """
    try:
        user_id = g.current_user['id']
        
        # Verify audit access
        audit = Audit.query.filter_by(id=audit_id, user_id=user_id).first()
        if not audit:
            return jsonify({'error': 'Audit not found or access denied'}), 404
        
        # Get active connection
        connection = UtilityConnection.query.filter_by(
            audit_id=audit_id,
            user_id=user_id,
            connection_status='connected'
        ).first()
        
        if not connection:
            return jsonify({'error': 'No active utility connection for this audit'}), 404
        
        # Get provider and sync
        provider = registry.get_provider(connection.provider_name)
        if not provider:
            return jsonify({'error': f'Provider {connection.provider_name} not found'}), 500
        
        # Update status
        connection.connection_status = 'sync_in_progress'
        db.session.commit()
        
        try:
            result = provider.sync_usage(connection.id)
            
            if result.success:
                connection.connection_status = 'connected'
                connection.last_sync_at = datetime.utcnow()
                connection.error_message = None
            else:
                connection.connection_status = 'connected'  # Keep connected, just note sync failure
                connection.error_message = result.error
            
            db.session.commit()
            
            return jsonify({
                'success': result.success,
                'records_imported': result.records_imported,
                'date_range_start': result.date_range_start,
                'date_range_end': result.date_range_end,
                'error': result.error
            }), 200 if result.success else 400
            
        except Exception as sync_error:
            connection.connection_status = 'connected'
            connection.error_message = str(sync_error)
            db.session.commit()
            raise
        
    except Exception as e:
        logger.error(f"Error syncing utility data: {e}")
        return jsonify({'error': str(e)}), 500


@audit_utility_bp.route('/utility-summary', methods=['GET'])
@require_auth
def get_utility_summary(audit_id):
    """
    GET /api/audits/<audit_id>/utility-summary
    
    Get normalized utility usage summary for an audit.
    """
    try:
        user_id = g.current_user['id']
        
        # Verify audit access
        audit = Audit.query.filter_by(id=audit_id, user_id=user_id).first()
        if not audit:
            return jsonify({'error': 'Audit not found or access denied'}), 404
        
        # Get active connection
        connection = UtilityConnection.query.filter_by(
            audit_id=audit_id,
            user_id=user_id
        ).filter(UtilityConnection.connection_status.in_(['connected', 'sync_in_progress'])).first()
        
        if not connection:
            return jsonify({
                'connected': False,
                'connection': None,
                'summary': None
            }), 200
        
        # Get provider and fetch normalized usage
        provider = registry.get_provider(connection.provider_name)
        
        # Get stored summary
        summary = UtilityUsageSummary.query.filter_by(
            audit_id=audit_id,
            utility_connection_id=connection.id
        ).order_by(UtilityUsageSummary.created_at.desc()).first()
        
        summary_data = None
        if summary:
            summary_data = {
                'fuel_type': summary.fuel_type,
                'start_date': summary.start_date.isoformat() if summary.start_date else None,
                'end_date': summary.end_date.isoformat() if summary.end_date else None,
                'annual_usage_kwh': summary.annual_usage_kwh,
                'annual_cost_usd': summary.annual_cost_usd,
                'monthly_breakdown': summary.monthly_breakdown,
                'seasonal_pattern': summary.seasonal_pattern,
                'tou_data': summary.tou_data,
                'data_quality_flags': summary.data_quality_flags
            }
        
        return jsonify({
            'connected': connection.connection_status == 'connected',
            'connection': {
                'id': connection.id,
                'provider_name': connection.provider_name,
                'utility_name': connection.utility_name,
                'status': connection.connection_status,
                'last_sync': connection.last_sync_at.isoformat() if connection.last_sync_at else None
            },
            'summary': summary_data
        }), 200
        
    except Exception as e:
        logger.error(f"Error getting utility summary: {e}")
        return jsonify({'error': str(e)}), 500


@audit_utility_bp.route('/utility-data/manual', methods=['POST'])
@require_auth
def submit_manual_utility_data(audit_id):
    """
    POST /api/audits/<audit_id>/utility-data/manual
    
    Submit manual utility usage data entry.
    
    Body:
    {
        "utility_name": "SDGE",
        "data_scope": "electric",
        "monthly_data": [
            {"month": "2024-01", "usage_kwh": 650, "cost_usd": 130},
            {"month": "2024-02", "usage_kwh": 600, "cost_usd": 120},
            ...
        ]
    }
    """
    try:
        user_id = g.current_user['id']
        
        # Verify audit access
        audit = Audit.query.filter_by(id=audit_id, user_id=user_id).first()
        if not audit:
            return jsonify({'error': 'Audit not found or access denied'}), 404
        
        data = request.get_json() or {}
        
        # Validate required fields
        monthly_data = data.get('monthly_data', [])
        if not monthly_data:
            return jsonify({'error': 'monthly_data is required'}), 400
        
        utility_name = data.get('utility_name', 'UNKNOWN')
        data_scope = data.get('data_scope', 'electric')
        
        # Get manual provider
        manual_provider = registry.get_provider('manual')
        if not manual_provider:
            return jsonify({'error': 'Manual entry provider not available'}), 500
        
        # Create connection if needed
        existing = UtilityConnection.query.filter_by(
            audit_id=audit_id,
            user_id=user_id,
            provider_name='manual'
        ).first()
        
        if existing:
            connection = existing
            connection.connection_status = 'connected'
            connection.utility_name = utility_name
            connection.data_scope = data_scope
        else:
            connection = UtilityConnection(
                user_id=user_id,
                audit_id=audit_id,
                provider_name='manual',
                provider_type='manual',
                utility_name=utility_name,
                connection_status='connected',
                data_scope=data_scope
            )
            db.session.add(connection)
        
        db.session.flush()  # Get connection.id
        
        # Store raw data
        raw_record = UtilityUsageData(
            user_id=user_id,
            audit_id=audit_id,
            utility_connection_id=connection.id,
            raw_data={'monthly_data': monthly_data, 'source': 'manual_entry'},
            data_format='manual_entry'
        )
        db.session.add(raw_record)
        
        # Calculate summary
        from utils.green_button import normalize_usage_to_summary
        summary_input = {
            'intervals': [],
            'billing_periods': [
                {
                    'start': f"{item['month']}-01",
                    'end': f"{item['month']}-28",  # Approximate
                    'usage': item.get('usage_kwh', 0),
                    'cost': item.get('cost_usd', 0)
                }
                for item in monthly_data
            ]
        }
        
        summary_data = normalize_usage_to_summary(summary_input, data_scope, 'manual_entry')
        
        # Store or update summary
        existing_summary = UtilityUsageSummary.query.filter_by(
            audit_id=audit_id,
            utility_connection_id=connection.id
        ).first()
        
        if existing_summary:
            summary = existing_summary
        else:
            summary = UtilityUsageSummary(
                user_id=user_id,
                audit_id=audit_id,
                utility_connection_id=connection.id
            )
            db.session.add(summary)
        
        summary.fuel_type = data_scope
        summary.start_date = summary_data.get('start_date')
        summary.end_date = summary_data.get('end_date')
        summary.annual_usage_kwh = summary_data.get('annual_usage')
        summary.annual_cost_usd = summary_data.get('annual_cost')
        summary.monthly_breakdown = summary_data.get('monthly_breakdown', [])
        summary.seasonal_pattern = summary_data.get('seasonal_pattern', {})
        summary.data_quality_flags = summary_data.get('data_quality_flags', [])
        
        connection.last_sync_at = datetime.utcnow()
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'connection_id': connection.id,
            'summary': {
                'annual_usage_kwh': summary.annual_usage_kwh,
                'annual_cost_usd': summary.annual_cost_usd,
                'months_covered': len(monthly_data)
            }
        }), 200
        
    except Exception as e:
        logger.error(f"Error submitting manual utility data: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@audit_utility_bp.route('/utility-connection', methods=['DELETE'])
@require_auth
def delete_utility_connection(audit_id):
    """
    DELETE /api/audits/<audit_id>/utility-connection
    
    Revoke utility connection and optionally delete associated data.
    
    Query params:
        delete_data: bool - whether to delete usage data (default: false)
    """
    try:
        user_id = g.current_user['id']
        delete_data = request.args.get('delete_data', 'false').lower() == 'true'
        
        # Verify audit access
        audit = Audit.query.filter_by(id=audit_id, user_id=user_id).first()
        if not audit:
            return jsonify({'error': 'Audit not found or access denied'}), 404
        
        # Get connection
        connection = UtilityConnection.query.filter_by(
            audit_id=audit_id,
            user_id=user_id
        ).filter(UtilityConnection.connection_status != 'revoked').first()
        
        if not connection:
            return jsonify({'error': 'No active utility connection for this audit'}), 404
        
        # Get provider and revoke
        provider = registry.get_provider(connection.provider_name)
        if provider:
            try:
                provider.revoke(connection.id)
            except Exception as revoke_error:
                logger.warning(f"Provider revoke failed: {revoke_error}")
        
        # Update connection status
        connection.connection_status = 'revoked'
        connection.access_token_encrypted = None
        connection.refresh_token_encrypted = None
        
        # Optionally delete data
        if delete_data:
            UtilityUsageData.query.filter_by(utility_connection_id=connection.id).delete()
            UtilityUsageSummary.query.filter_by(utility_connection_id=connection.id).delete()
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'connection_id': connection.id,
            'data_deleted': delete_data
        }), 200
        
    except Exception as e:
        logger.error(f"Error revoking utility connection: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
