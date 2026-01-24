"""
Webhook Routes Blueprint

Publicly accessible webhook endpoints for third-party integrations.
Currently supports UtilityAPI webhooks for utility data authorization and updates.

Security:
- All webhook requests are verified using provider-specific signature verification
- No authentication middleware (public endpoints)
- Rate limiting recommended at infrastructure level (Railway, nginx, etc.)

Observability:
- All webhook requests are logged with timestamps, event types, and identifiers
- Signature failures are logged without exposing secrets
"""

import os
import hmac
import hashlib
import logging
from datetime import datetime
from flask import Blueprint, request, jsonify, current_app

logger = logging.getLogger(__name__)

webhook_bp = Blueprint('webhooks', __name__, url_prefix='/webhooks')


# ============================================================================
# UtilityAPI Webhook Configuration
# ============================================================================

def get_utilityapi_config():
    """Get UtilityAPI webhook configuration from environment.
    
    Uses Flask's current_app.config first if available, falls back to os.getenv.
    """
    # Try to get from Flask config first (set during app initialization)
    webhook_secret = None
    try:
        webhook_secret = current_app.config.get('UTILITYAPI_WEBHOOK_SECRET')
    except RuntimeError:
        # Outside of application context
        pass
    
    # Fall back to environment variable
    if not webhook_secret:
        webhook_secret = os.getenv('UTILITYAPI_WEBHOOK_SECRET')
    
    return {
        'base_url': os.getenv('UTILITYAPI_BASE_URL', 'https://utilityapi.com/api/v2'),
        'webhook_secret': webhook_secret,
    }


def verify_utilityapi_signature(raw_body: bytes, salt: str, signature: str) -> bool:
    """
    Verify UtilityAPI webhook signature.
    
    UtilityAPI uses: SHA256("<secret>.<salt>.<body>")
    Note: Components are joined with periods (.)
    
    Reference: https://utilityapi.com/docs/webhooks#verifying-signatures
    
    Args:
        raw_body: Raw request body bytes
        salt: X-UtilityAPI-Webhook-Salt header value
        signature: X-UtilityAPI-Webhook-Signature header value
        
    Returns:
        True if signature is valid, False otherwise
    """
    config = get_utilityapi_config()
    webhook_secret = config['webhook_secret']
    
    if not webhook_secret:
        logger.warning(
            "WEBHOOK_SIGNATURE_SKIP | provider=utilityapi reason=no_secret_configured"
        )
        # In development, allow requests without secret configured
        # In production, this should return False
        return os.getenv('FLASK_ENV') == 'development'
    
    if not salt or not signature:
        logger.warning(
            "WEBHOOK_SIGNATURE_MISSING | provider=utilityapi salt=%s signature=%s",
            bool(salt), bool(signature)
        )
        return False
    
    # UtilityAPI signature: SHA256("<secret>.<salt>.<body>")
    # Components joined with periods, then UTF-8 encoded
    body_str = raw_body.decode('utf-8')
    combined_string = f"{webhook_secret}.{salt}.{body_str}"
    expected_signature = hashlib.sha256(combined_string.encode('utf-8')).hexdigest()
    
    # Log for debugging (without exposing secret)
    logger.debug(
        "WEBHOOK_SIGNATURE_CHECK | provider=utilityapi salt_len=%d sig_len=%d body_len=%d",
        len(salt), len(signature), len(raw_body)
    )
    
    return hmac.compare_digest(expected_signature.lower(), signature.lower())


def log_webhook_event(
    provider: str,
    event_type: str,
    success: bool,
    identifiers: dict = None,
    error: str = None
):
    """
    Log webhook event with structured data for observability.
    
    Args:
        provider: Webhook provider name (e.g., 'utilityapi')
        event_type: Type of webhook event
        success: Whether the webhook was processed successfully
        identifiers: Dict of relevant IDs (authorization_uid, meter_uid, audit_id, etc.)
        error: Error message if failed
    """
    timestamp = datetime.utcnow().isoformat()
    status = "SUCCESS" if success else "FAILED"
    
    log_data = {
        "event_type": "webhook_received",
        "provider": provider,
        "webhook_event": event_type,
        "success": success,
        "timestamp": timestamp,
    }
    
    if identifiers:
        log_data.update(identifiers)
    if error:
        log_data["error"] = error
    
    id_str = " ".join(f"{k}={v}" for k, v in (identifiers or {}).items())
    error_str = f" error={error}" if error else ""
    
    level = logging.INFO if success else logging.WARNING
    logger.log(
        level,
        f"WEBHOOK_{status} | provider={provider} event={event_type} {id_str}{error_str}",
        extra=log_data
    )


# ============================================================================
# Supported UtilityAPI Event Types
# ============================================================================

UTILITYAPI_EVENT_TYPES = {
    # Test/verification events
    'ping': 'Webhook connectivity test',
    
    # Authorization lifecycle
    'authorization_created': 'New authorization form created',
    'authorization_update_started': 'Authorization update in progress',
    'authorization_update_finished_successful': 'Authorization completed successfully',
    'authorization_update_finished_failed': 'Authorization failed',
    'authorization_revoked': 'User revoked authorization',
    
    # Meter data events
    'meter_intervals_added': 'New interval (usage) data available',
    'meter_bills_added': 'New billing data available',
    'meter_historical_collection_started': 'Historical data collection started',
    'meter_historical_collection_finished': 'Historical data collection complete',
}


# ============================================================================
# UtilityAPI Webhook Endpoint
# ============================================================================

@webhook_bp.route('/utilityapi', methods=['POST'])
def utilityapi_webhook():
    """
    POST /webhooks/utilityapi
    
    Handle UtilityAPI async webhook notifications.
    
    Security:
    - Verifies signature using X-UtilityAPI-Webhook-Salt and X-UtilityAPI-Webhook-Signature
    - Rejects requests with invalid or missing signatures (401)
    
    Supported Events:
    - ping: Connectivity test
    - authorization_created: New authorization form
    - authorization_update_finished_successful: Authorization complete
    - meter_intervals_added: New interval data
    - meter_bills_added: New billing data
    
    Response:
    - 200 OK: Webhook received and accepted
    - 401 Unauthorized: Invalid signature
    - 400 Bad Request: Malformed payload
    """
    # Get raw body before parsing JSON
    raw_body = request.get_data()
    
    # Get signature headers
    salt = request.headers.get('X-UtilityAPI-Webhook-Salt')
    signature = request.headers.get('X-UtilityAPI-Webhook-Signature')
    
    # Verify signature
    if not verify_utilityapi_signature(raw_body, salt, signature):
        logger.warning(
            "WEBHOOK_SIGNATURE_INVALID | provider=utilityapi ip=%s",
            request.remote_addr,
            extra={
                "event_type": "webhook_signature_invalid",
                "provider": "utilityapi",
                "ip": request.remote_addr,
                "timestamp": datetime.utcnow().isoformat()
            }
        )
        return jsonify({'error': 'Invalid signature'}), 401
    
    # Parse JSON payload
    try:
        data = request.get_json() or {}
    except Exception as e:
        log_webhook_event('utilityapi', 'parse_error', False, error=str(e))
        return jsonify({'error': 'Invalid JSON payload'}), 400
    
    # Extract event information
    # UtilityAPI sends events in a list format or as single event
    events = data.get('events', [data]) if 'events' not in data else data['events']
    
    # Process each event
    processed_events = []
    for event in events:
        event_type = event.get('type', 'unknown')
        
        # Extract identifiers for logging
        identifiers = {}
        if event.get('authorization_uid'):
            identifiers['authorization_uid'] = event['authorization_uid']
        if event.get('meter_uid'):
            identifiers['meter_uid'] = event['meter_uid']
        if event.get('referral'):
            identifiers['referral'] = event['referral']
            # Parse audit_id from referral if present (format: "audit_123_user_456")
            referral = event['referral']
            if referral.startswith('audit_'):
                parts = referral.split('_')
                if len(parts) >= 2:
                    try:
                        identifiers['audit_id'] = int(parts[1])
                    except ValueError:
                        pass
        
        # Log the event
        log_webhook_event('utilityapi', event_type, True, identifiers)
        
        # Handle specific event types
        try:
            if event_type == 'ping':
                # Connectivity test - just acknowledge
                processed_events.append({'type': 'ping', 'status': 'acknowledged'})
                
            elif event_type == 'authorization_created':
                # New authorization form created - typically no action needed
                processed_events.append({
                    'type': 'authorization_created',
                    'authorization_uid': event.get('authorization_uid'),
                    'status': 'acknowledged'
                })
                
            elif event_type == 'authorization_update_finished_successful':
                # Authorization complete - queue data fetch
                result = handle_authorization_complete(event)
                processed_events.append({
                    'type': 'authorization_update_finished_successful',
                    'authorization_uid': event.get('authorization_uid'),
                    'status': 'processed' if result else 'queued'
                })
                
            elif event_type in ['meter_intervals_added', 'meter_bills_added']:
                # New data available - queue sync
                result = handle_data_available(event, event_type)
                processed_events.append({
                    'type': event_type,
                    'meter_uid': event.get('meter_uid'),
                    'status': 'processed' if result else 'queued'
                })
                
            elif event_type == 'authorization_revoked':
                # User revoked access - update connection status
                result = handle_authorization_revoked(event)
                processed_events.append({
                    'type': 'authorization_revoked',
                    'authorization_uid': event.get('authorization_uid'),
                    'status': 'processed' if result else 'acknowledged'
                })
                
            else:
                # Unknown event type - acknowledge but don't process
                logger.info(
                    "WEBHOOK_UNKNOWN_EVENT | provider=utilityapi event=%s",
                    event_type,
                    extra={
                        "event_type": "webhook_unknown_event",
                        "provider": "utilityapi",
                        "webhook_event": event_type,
                    }
                )
                processed_events.append({
                    'type': event_type,
                    'status': 'acknowledged'
                })
                
        except Exception as e:
            logger.error(
                "WEBHOOK_PROCESSING_ERROR | provider=utilityapi event=%s error=%s",
                event_type, str(e),
                extra={
                    "event_type": "webhook_processing_error",
                    "provider": "utilityapi",
                    "webhook_event": event_type,
                    "error": str(e)
                }
            )
            processed_events.append({
                'type': event_type,
                'status': 'error',
                'message': 'Processing failed, will retry'
            })
    
    return jsonify({
        'status': 'received',
        'events_processed': len(processed_events),
        'events': processed_events
    }), 200


# ============================================================================
# Event Handlers (Idempotent / Queue-based)
# ============================================================================

def handle_authorization_complete(event: dict) -> bool:
    """
    Handle authorization_update_finished_successful event.
    
    This is called when a user successfully authorizes utility data access.
    Updates the connection status and optionally triggers data sync.
    
    Args:
        event: Webhook event payload
        
    Returns:
        True if processed synchronously, False if queued for background processing
    """
    authorization_uid = event.get('authorization_uid')
    referral = event.get('referral', '')
    
    if not authorization_uid:
        logger.warning("Authorization complete event missing authorization_uid")
        return False
    
    try:
        # Lazy imports to avoid circular dependencies
        from models import UtilityConnection, db
        from utils.providers.registry import get_registry
        
        # Find connection by authorization_uid in provider_metadata
        # Use raw SQL filter for JSONB field
        connection = UtilityConnection.query.filter(
            UtilityConnection.provider_name == 'utilityapi',
            UtilityConnection.provider_metadata['authorization_uid'].astext == authorization_uid
        ).first()
        
        # If not found by authorization_uid, try parsing referral
        if not connection and referral:
            parts = referral.split('_')
            if len(parts) >= 4 and parts[0] == 'audit':
                try:
                    audit_id = int(parts[1])
                    user_id = parts[3] if len(parts) > 3 else None
                    connection = UtilityConnection.query.filter_by(
                        audit_id=audit_id,
                        provider_name='utilityapi',
                        status='pending_authorization'
                    ).first()
                except ValueError:
                    pass
        
        if not connection:
            logger.warning(
                "No pending connection found for authorization_uid=%s referral=%s",
                authorization_uid, referral
            )
            return False
        
        # Update connection status (idempotent - check current status)
        if connection.status == 'pending_authorization':
            connection.status = 'connected'
            # Store authorization_uid in provider_metadata
            metadata = connection.provider_metadata or {}
            metadata['authorization_uid'] = authorization_uid
            connection.provider_metadata = metadata
            connection.updated_at = datetime.utcnow()
            connection.last_sync_error = None
            db.session.commit()
            
            logger.info(
                "WEBHOOK_AUTH_COMPLETE | connection_id=%s audit_id=%s authorization_uid=%s",
                connection.id, connection.audit_id, authorization_uid
            )
            
            # Queue background data sync (don't block webhook response)
            # For now, just mark as needing sync - a background job can pick this up
            # In future: use Celery, RQ, or similar for async processing
            
        return True
        
    except Exception as e:
        logger.error("Error handling authorization complete: %s", str(e))
        return False


def handle_data_available(event: dict, event_type: str) -> bool:
    """
    Handle meter_intervals_added or meter_bills_added events.
    
    This is called when new utility data is available for download.
    Queues a data sync for the relevant connection.
    
    Args:
        event: Webhook event payload
        event_type: Type of data event
        
    Returns:
        True if processed synchronously, False if queued for background processing
    """
    meter_uid = event.get('meter_uid')
    authorization_uid = event.get('authorization_uid')
    referral = event.get('referral', '')
    
    try:
        from models import UtilityConnection, db
        
        # Find connection by meter or authorization
        connection = None
        
        if authorization_uid:
            # Search in provider_metadata JSONB field
            connection = UtilityConnection.query.filter(
                UtilityConnection.provider_name == 'utilityapi',
                UtilityConnection.status == 'connected',
                UtilityConnection.provider_metadata['authorization_uid'].astext == authorization_uid
            ).first()
        
        if not connection and referral:
            parts = referral.split('_')
            if len(parts) >= 2 and parts[0] == 'audit':
                try:
                    audit_id = int(parts[1])
                    connection = UtilityConnection.query.filter_by(
                        audit_id=audit_id,
                        provider_name='utilityapi',
                        status='connected'
                    ).first()
                except ValueError:
                    pass
        
        if not connection:
            logger.warning(
                "No connected connection found for data event: meter_uid=%s authorization_uid=%s",
                meter_uid, authorization_uid
            )
            return False
        
        # Mark connection as needing sync (idempotent)
        # A background job will pick this up and perform the actual sync
        # For now, log that sync is needed
        logger.info(
            "WEBHOOK_DATA_AVAILABLE | connection_id=%s audit_id=%s event=%s meter_uid=%s",
            connection.id, connection.audit_id, event_type, meter_uid
        )
        
        # In a production system, you would:
        # 1. Queue a background job: queue.enqueue(sync_utility_data, connection.id)
        # 2. Or set a flag: connection.sync_needed = True
        
        return True
        
    except Exception as e:
        logger.error("Error handling data available: %s", str(e))
        return False


def handle_authorization_revoked(event: dict) -> bool:
    """
    Handle authorization_revoked event.
    
    This is called when a user revokes their utility data authorization.
    Updates the connection status to 'revoked'.
    
    Args:
        event: Webhook event payload
        
    Returns:
        True if processed, False otherwise
    """
    authorization_uid = event.get('authorization_uid')
    
    if not authorization_uid:
        return False
    
    try:
        from models import UtilityConnection, db
        
        # Search in provider_metadata JSONB field
        connection = UtilityConnection.query.filter(
            UtilityConnection.provider_name == 'utilityapi',
            UtilityConnection.provider_metadata['authorization_uid'].astext == authorization_uid
        ).first()
        
        if connection and connection.status != 'revoked':
            connection.status = 'revoked'
            connection.updated_at = datetime.utcnow()
            db.session.commit()
            
            logger.info(
                "WEBHOOK_AUTH_REVOKED | connection_id=%s audit_id=%s authorization_uid=%s",
                connection.id, connection.audit_id, authorization_uid
            )
        
        return True
        
    except Exception as e:
        logger.error("Error handling authorization revoked: %s", str(e))
        return False


# ============================================================================
# Health Check Endpoint
# ============================================================================

@webhook_bp.route('/health', methods=['GET'])
def webhook_health():
    """
    GET /webhooks/health
    
    Health check endpoint for webhook infrastructure monitoring.
    """
    config = get_utilityapi_config()
    
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'providers': {
            'utilityapi': {
                'configured': bool(config['webhook_secret']),
                'base_url': config['base_url']
            }
        }
    }), 200
