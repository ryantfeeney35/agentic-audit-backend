"""
Tests for Webhook Routes Blueprint

Tests the UtilityAPI webhook endpoint including:
- Signature verification
- Event type handling (ping, authorization, meter data)
- Idempotency and error handling
- Observability logging
"""

import json
import hmac
import hashlib
import os
import sys
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Load webhook module directly to avoid routes/__init__.py dependencies
def _load_webhook_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "webhook_routes", 
        os.path.join(os.path.dirname(__file__), '..', 'routes', 'webhook_routes.py')
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

_webhook_module = _load_webhook_module()


@pytest.fixture
def minimal_app():
    """Create minimal test Flask application without full DB."""
    from flask import Flask
    
    app = Flask(__name__)
    app.config['TESTING'] = True
    
    # Register only the webhook blueprint
    app.register_blueprint(_webhook_module.webhook_bp)
    
    return app


@pytest.fixture
def webhook_module():
    """Return the webhook module for patching."""
    return _webhook_module


@pytest.fixture
def client(minimal_app):
    """Create test client."""
    return minimal_app.test_client()


@pytest.fixture
def webhook_secret():
    """Test webhook secret."""
    return 'test-webhook-secret-123'


def generate_signature(secret: str, salt: str, body: bytes) -> str:
    """Generate UtilityAPI-style signature.
    
    Format: SHA256("<secret>.<salt>.<body>")
    """
    body_str = body.decode('utf-8')
    combined_string = f"{secret}.{salt}.{body_str}"
    return hashlib.sha256(combined_string.encode('utf-8')).hexdigest()


class TestWebhookSignatureVerification:
    """Test webhook signature verification."""
    
    def test_valid_signature_accepted(self, client, webhook_secret):
        """Valid signature should be accepted."""
        with patch.dict('os.environ', {'UTILITYAPI_WEBHOOK_SECRET': webhook_secret}):
            payload = json.dumps({'type': 'ping'}).encode()
            salt = 'random-salt-12345'
            signature = generate_signature(webhook_secret, salt, payload)
            
            response = client.post(
                '/webhooks/utilityapi',
                data=payload,
                content_type='application/json',
                headers={
                    'X-UtilityAPI-Webhook-Salt': salt,
                    'X-UtilityAPI-Webhook-Signature': signature
                }
            )
            
            assert response.status_code == 200
    
    def test_invalid_signature_rejected(self, client, webhook_secret):
        """Invalid signature should be rejected with 401."""
        with patch.dict('os.environ', {'UTILITYAPI_WEBHOOK_SECRET': webhook_secret}):
            payload = json.dumps({'type': 'ping'}).encode()
            salt = 'random-salt-12345'
            
            response = client.post(
                '/webhooks/utilityapi',
                data=payload,
                content_type='application/json',
                headers={
                    'X-UtilityAPI-Webhook-Salt': salt,
                    'X-UtilityAPI-Webhook-Signature': 'invalid-signature'
                }
            )
            
            assert response.status_code == 401
            assert 'Invalid signature' in response.get_json()['error']
    
    def test_missing_salt_rejected(self, client, webhook_secret):
        """Missing salt header should be rejected."""
        with patch.dict('os.environ', {'UTILITYAPI_WEBHOOK_SECRET': webhook_secret}):
            payload = json.dumps({'type': 'ping'}).encode()
            
            response = client.post(
                '/webhooks/utilityapi',
                data=payload,
                content_type='application/json',
                headers={
                    'X-UtilityAPI-Webhook-Signature': 'some-signature'
                }
            )
            
            assert response.status_code == 401
    
    def test_missing_signature_rejected(self, client, webhook_secret):
        """Missing signature header should be rejected."""
        with patch.dict('os.environ', {'UTILITYAPI_WEBHOOK_SECRET': webhook_secret}):
            payload = json.dumps({'type': 'ping'}).encode()
            
            response = client.post(
                '/webhooks/utilityapi',
                data=payload,
                content_type='application/json',
                headers={
                    'X-UtilityAPI-Webhook-Salt': 'some-salt'
                }
            )
            
            assert response.status_code == 401
    
    def test_no_secret_configured_development_mode(self, client):
        """Without secret in development mode, should allow (for testing)."""
        with patch.dict('os.environ', {
            'UTILITYAPI_WEBHOOK_SECRET': '',
            'FLASK_ENV': 'development'
        }):
            payload = json.dumps({'type': 'ping'}).encode()
            
            response = client.post(
                '/webhooks/utilityapi',
                data=payload,
                content_type='application/json'
            )
            
            # In development without secret, should pass
            assert response.status_code == 200


class TestPingEvent:
    """Test ping event handling."""
    
    def test_ping_returns_200(self, client, webhook_secret):
        """Ping event should return 200 OK."""
        with patch.dict('os.environ', {'UTILITYAPI_WEBHOOK_SECRET': webhook_secret}):
            payload = json.dumps({'type': 'ping'}).encode()
            salt = 'test-salt'
            signature = generate_signature(webhook_secret, salt, payload)
            
            response = client.post(
                '/webhooks/utilityapi',
                data=payload,
                content_type='application/json',
                headers={
                    'X-UtilityAPI-Webhook-Salt': salt,
                    'X-UtilityAPI-Webhook-Signature': signature
                }
            )
            
            assert response.status_code == 200
            data = response.get_json()
            assert data['status'] == 'received'
            assert data['events_processed'] == 1
            assert data['events'][0]['type'] == 'ping'
            assert data['events'][0]['status'] == 'acknowledged'


class TestAuthorizationEvents:
    """Test authorization event handling."""
    
    def test_authorization_created_acknowledged(self, client, webhook_secret):
        """Authorization created event should be acknowledged."""
        with patch.dict('os.environ', {'UTILITYAPI_WEBHOOK_SECRET': webhook_secret}):
            payload = json.dumps({
                'type': 'authorization_created',
                'authorization_uid': 'auth-123',
                'referral': 'audit_1_user_abc'
            }).encode()
            salt = 'test-salt'
            signature = generate_signature(webhook_secret, salt, payload)
            
            response = client.post(
                '/webhooks/utilityapi',
                data=payload,
                content_type='application/json',
                headers={
                    'X-UtilityAPI-Webhook-Salt': salt,
                    'X-UtilityAPI-Webhook-Signature': signature
                }
            )
            
            assert response.status_code == 200
            data = response.get_json()
            assert data['events'][0]['type'] == 'authorization_created'
            assert data['events'][0]['status'] == 'acknowledged'
    
    def test_authorization_complete_acknowledged(self, client, webhook_secret, webhook_module):
        """Authorization complete event should be acknowledged (DB update tested separately)."""
        with patch.dict('os.environ', {'UTILITYAPI_WEBHOOK_SECRET': webhook_secret}):
            # Mock the handler to avoid DB dependency using the module reference
            with patch.object(webhook_module, 'handle_authorization_complete', return_value=True):
                payload = json.dumps({
                    'type': 'authorization_update_finished_successful',
                    'authorization_uid': 'auth-456',
                    'referral': 'audit_1_user_user-123'
                }).encode()
                salt = 'test-salt'
                signature = generate_signature(webhook_secret, salt, payload)
                
                response = client.post(
                    '/webhooks/utilityapi',
                    data=payload,
                    content_type='application/json',
                    headers={
                        'X-UtilityAPI-Webhook-Salt': salt,
                        'X-UtilityAPI-Webhook-Signature': signature
                    }
                )
                
                assert response.status_code == 200
                data = response.get_json()
                assert data['events'][0]['type'] == 'authorization_update_finished_successful'
    
    def test_authorization_revoked_acknowledged(self, client, webhook_secret, webhook_module):
        """Authorization revoked event should be acknowledged."""
        with patch.dict('os.environ', {'UTILITYAPI_WEBHOOK_SECRET': webhook_secret}):
            with patch.object(webhook_module, 'handle_authorization_revoked', return_value=True):
                payload = json.dumps({
                    'type': 'authorization_revoked',
                    'authorization_uid': 'auth-789'
                }).encode()
                salt = 'test-salt'
                signature = generate_signature(webhook_secret, salt, payload)
                
                response = client.post(
                    '/webhooks/utilityapi',
                    data=payload,
                    content_type='application/json',
                    headers={
                        'X-UtilityAPI-Webhook-Salt': salt,
                        'X-UtilityAPI-Webhook-Signature': signature
                    }
                )
                
                assert response.status_code == 200
                data = response.get_json()
                assert data['events'][0]['type'] == 'authorization_revoked'


class TestMeterDataEvents:
    """Test meter data event handling."""
    
    def test_meter_intervals_added_acknowledged(self, client, webhook_secret, webhook_module):
        """Meter intervals added should be acknowledged."""
        with patch.dict('os.environ', {'UTILITYAPI_WEBHOOK_SECRET': webhook_secret}):
            with patch.object(webhook_module, 'handle_data_available', return_value=True):
                payload = json.dumps({
                    'type': 'meter_intervals_added',
                    'meter_uid': 'meter-123',
                    'authorization_uid': 'auth-456'
                }).encode()
                salt = 'test-salt'
                signature = generate_signature(webhook_secret, salt, payload)
                
                response = client.post(
                    '/webhooks/utilityapi',
                    data=payload,
                    content_type='application/json',
                    headers={
                        'X-UtilityAPI-Webhook-Salt': salt,
                        'X-UtilityAPI-Webhook-Signature': signature
                    }
                )
                
                assert response.status_code == 200
                data = response.get_json()
                assert data['events'][0]['type'] == 'meter_intervals_added'
    
    def test_meter_bills_added_acknowledged(self, client, webhook_secret, webhook_module):
        """Meter bills added should be acknowledged."""
        with patch.dict('os.environ', {'UTILITYAPI_WEBHOOK_SECRET': webhook_secret}):
            with patch.object(webhook_module, 'handle_data_available', return_value=True):
                payload = json.dumps({
                    'type': 'meter_bills_added',
                    'meter_uid': 'meter-123',
                    'authorization_uid': 'auth-456'
                }).encode()
                salt = 'test-salt'
                signature = generate_signature(webhook_secret, salt, payload)
                
                response = client.post(
                    '/webhooks/utilityapi',
                    data=payload,
                    content_type='application/json',
                    headers={
                        'X-UtilityAPI-Webhook-Salt': salt,
                        'X-UtilityAPI-Webhook-Signature': signature
                    }
                )
                
                assert response.status_code == 200
                data = response.get_json()
                assert data['events'][0]['type'] == 'meter_bills_added'


class TestMultipleEvents:
    """Test handling multiple events in a single webhook."""
    
    def test_multiple_events_processed(self, client, webhook_secret, webhook_module):
        """Multiple events in single webhook should all be processed."""
        with patch.dict('os.environ', {'UTILITYAPI_WEBHOOK_SECRET': webhook_secret}):
            payload = json.dumps({
                'events': [
                    {'type': 'ping'},
                    {'type': 'authorization_created', 'authorization_uid': 'auth-1'},
                    {'type': 'meter_intervals_added', 'meter_uid': 'meter-1'}
                ]
            }).encode()
            salt = 'test-salt'
            signature = generate_signature(webhook_secret, salt, payload)
            
            # Mock handlers to avoid DB
            with patch.object(webhook_module, 'handle_data_available', return_value=True):
                response = client.post(
                    '/webhooks/utilityapi',
                    data=payload,
                    content_type='application/json',
                    headers={
                        'X-UtilityAPI-Webhook-Salt': salt,
                        'X-UtilityAPI-Webhook-Signature': signature
                    }
                )
            
            assert response.status_code == 200
            data = response.get_json()
            assert data['events_processed'] == 3


class TestUnknownEvents:
    """Test handling unknown event types."""
    
    def test_unknown_event_acknowledged_without_error(self, client, webhook_secret):
        """Unknown event types should be acknowledged without causing errors."""
        with patch.dict('os.environ', {'UTILITYAPI_WEBHOOK_SECRET': webhook_secret}):
            payload = json.dumps({
                'type': 'future_event_type_v2',
                'some_data': 'value'
            }).encode()
            salt = 'test-salt'
            signature = generate_signature(webhook_secret, salt, payload)
            
            response = client.post(
                '/webhooks/utilityapi',
                data=payload,
                content_type='application/json',
                headers={
                    'X-UtilityAPI-Webhook-Salt': salt,
                    'X-UtilityAPI-Webhook-Signature': signature
                }
            )
            
            assert response.status_code == 200
            data = response.get_json()
            assert data['events'][0]['status'] == 'acknowledged'


class TestIdempotency:
    """Test webhook idempotency."""
    
    def test_duplicate_ping_is_idempotent(self, client, webhook_secret):
        """Duplicate ping events should not cause errors."""
        with patch.dict('os.environ', {'UTILITYAPI_WEBHOOK_SECRET': webhook_secret}):
            payload = json.dumps({'type': 'ping'}).encode()
            salt = 'test-salt'
            signature = generate_signature(webhook_secret, salt, payload)
            
            # Send the same webhook twice
            response1 = client.post(
                '/webhooks/utilityapi',
                data=payload,
                content_type='application/json',
                headers={
                    'X-UtilityAPI-Webhook-Salt': salt,
                    'X-UtilityAPI-Webhook-Signature': signature
                }
            )
            
            response2 = client.post(
                '/webhooks/utilityapi',
                data=payload,
                content_type='application/json',
                headers={
                    'X-UtilityAPI-Webhook-Salt': salt,
                    'X-UtilityAPI-Webhook-Signature': signature
                }
            )
            
            # Both should succeed
            assert response1.status_code == 200
            assert response2.status_code == 200


class TestMalformedPayloads:
    """Test handling of malformed payloads."""
    
    def test_invalid_json_returns_400(self, client, webhook_secret):
        """Invalid JSON should return 400."""
        with patch.dict('os.environ', {'UTILITYAPI_WEBHOOK_SECRET': webhook_secret}):
            payload = b'not valid json {'
            salt = 'test-salt'
            signature = generate_signature(webhook_secret, salt, payload)
            
            response = client.post(
                '/webhooks/utilityapi',
                data=payload,
                content_type='application/json',
                headers={
                    'X-UtilityAPI-Webhook-Salt': salt,
                    'X-UtilityAPI-Webhook-Signature': signature
                }
            )
            
            assert response.status_code == 400
    
    def test_empty_body_handled(self, client, webhook_secret):
        """Empty body should be handled gracefully."""
        with patch.dict('os.environ', {'UTILITYAPI_WEBHOOK_SECRET': webhook_secret}):
            payload = b'{}'
            salt = 'test-salt'
            signature = generate_signature(webhook_secret, salt, payload)
            
            response = client.post(
                '/webhooks/utilityapi',
                data=payload,
                content_type='application/json',
                headers={
                    'X-UtilityAPI-Webhook-Salt': salt,
                    'X-UtilityAPI-Webhook-Signature': signature
                }
            )
            
            # Should handle gracefully (unknown event type)
            assert response.status_code == 200


class TestHealthEndpoint:
    """Test webhook health check endpoint."""
    
    def test_health_returns_200(self, client):
        """Health endpoint should return 200."""
        response = client.get('/webhooks/health')
        
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'healthy'
        assert 'timestamp' in data
        assert 'utilityapi' in data['providers']
    
    def test_health_shows_configuration_status(self, client, webhook_secret):
        """Health endpoint should show if webhook secret is configured."""
        with patch.dict('os.environ', {'UTILITYAPI_WEBHOOK_SECRET': webhook_secret}):
            response = client.get('/webhooks/health')
            
            data = response.get_json()
            assert data['providers']['utilityapi']['configured'] == True


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
