# routes/supabase_webhook_routes.py
"""
Supabase Auth Webhook Routes

Handles webhook events from Supabase Auth to keep local user records in sync.
This provides a safety net for user creation, ensuring users are never in a
"limbo" state where they exist in Supabase but not in the local database.

Webhook Events Handled:
- user.created: When a new user signs up (before email confirmation)
- user.updated: When user data changes (including email confirmation)

Security:
- Webhooks are verified using the SUPABASE_WEBHOOK_SECRET
- All requests without valid signatures are rejected

Setup in Supabase Dashboard:
1. Go to Project Settings > Webhooks
2. Create new webhook for Auth events
3. URL: https://your-api.com/webhooks/supabase/auth
4. Events: user.created, user.updated
5. Set webhook secret and add to your env as SUPABASE_WEBHOOK_SECRET
"""
import os
import hmac
import hashlib
import logging
from datetime import datetime
from flask import Blueprint, request, jsonify
from models import User, db

supabase_webhook_bp = Blueprint('supabase_webhooks', __name__, url_prefix='/webhooks/supabase')

# Webhook secret for verifying Supabase webhook signatures
SUPABASE_WEBHOOK_SECRET = os.getenv('SUPABASE_WEBHOOK_SECRET')


def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """
    Verify the webhook signature from Supabase.
    
    Args:
        payload: Raw request body bytes
        signature: Signature from x-supabase-signature header
        
    Returns:
        True if signature is valid, False otherwise
    """
    if not SUPABASE_WEBHOOK_SECRET:
        logging.warning("SUPABASE_WEBHOOK_SECRET not configured, skipping signature verification")
        return True  # Allow in development, but log warning
    
    if not signature:
        return False
    
    try:
        expected_signature = hmac.new(
            SUPABASE_WEBHOOK_SECRET.encode('utf-8'),
            payload,
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(signature, expected_signature)
    except Exception as e:
        logging.error(f"Webhook signature verification failed: {e}")
        return False


def sync_user_from_webhook(user_data: dict) -> tuple[bool, str]:
    """
    Create or update a local User record from Supabase webhook data.
    
    Args:
        user_data: User object from Supabase webhook payload
        
    Returns:
        Tuple of (success: bool, message: str)
    """
    try:
        user_id = user_data.get('id')
        email = user_data.get('email')
        
        if not user_id or not email:
            return False, "Missing user id or email in webhook payload"
        
        # Check if user already exists
        existing_user = User.query.get(user_id)
        
        if existing_user:
            # Update email if changed
            if existing_user.email != email:
                existing_user.email = email
                existing_user.updated_at = datetime.utcnow()
                db.session.commit()
                logging.info(f"Updated user email via webhook: {user_id}")
                return True, "User updated"
            return True, "User already exists"
        
        # Create new user
        new_user = User(id=user_id, email=email)
        db.session.add(new_user)
        db.session.commit()
        logging.info(f"Created user via webhook: {email} (id: {user_id})")
        return True, "User created"
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Failed to sync user from webhook: {e}")
        return False, str(e)


@supabase_webhook_bp.route('/auth', methods=['POST'])
def handle_auth_webhook():
    """
    Handle Supabase Auth webhook events.
    
    Supported events:
    - user.created: New user registered
    - user.updated: User data changed (including email confirmation)
    
    Payload structure:
    {
        "type": "user.created" | "user.updated",
        "table": "users",
        "record": {
            "id": "uuid",
            "email": "user@example.com",
            "email_confirmed_at": "timestamp or null",
            ...
        },
        "old_record": { ... }  // Only for updates
    }
    """
    try:
        # Get raw payload for signature verification
        payload = request.get_data()
        signature = request.headers.get('x-supabase-signature', '')
        
        # Verify webhook signature
        if not verify_webhook_signature(payload, signature):
            logging.warning("Invalid webhook signature received")
            return jsonify({'error': 'Invalid signature'}), 401
        
        # Parse webhook payload
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Empty payload'}), 400
        
        event_type = data.get('type')
        record = data.get('record', {})
        
        logging.info(f"Received Supabase auth webhook: {event_type}")
        
        # Handle supported events
        if event_type in ('user.created', 'user.updated', 'INSERT', 'UPDATE'):
            success, message = sync_user_from_webhook(record)
            
            if success:
                return jsonify({
                    'status': 'success',
                    'message': message,
                    'user_id': record.get('id')
                }), 200
            else:
                return jsonify({
                    'status': 'error',
                    'message': message
                }), 500
        
        # Unknown event type - acknowledge but don't process
        logging.info(f"Ignoring unhandled webhook event type: {event_type}")
        return jsonify({
            'status': 'ignored',
            'message': f'Event type {event_type} not handled'
        }), 200
        
    except Exception as e:
        logging.error(f"Webhook handler error: {e}")
        return jsonify({'error': 'Internal server error'}), 500


@supabase_webhook_bp.route('/auth/health', methods=['GET'])
def webhook_health():
    """Health check endpoint for webhook configuration verification."""
    return jsonify({
        'status': 'ok',
        'webhook_secret_configured': bool(SUPABASE_WEBHOOK_SECRET),
        'timestamp': datetime.utcnow().isoformat()
    }), 200
