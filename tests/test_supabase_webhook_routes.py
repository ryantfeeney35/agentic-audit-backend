# test_supabase_webhook_routes.py
"""Tests for Supabase Auth webhook routes"""
import pytest
import json
import hmac
import hashlib
from unittest.mock import patch, MagicMock
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

# Create a minimal test DB and User model to avoid JSONB/SQLite incompatibility
test_db = SQLAlchemy()

class TestUser(test_db.Model):
    """Minimal User model for webhook tests"""
    __tablename__ = 'users'
    id = test_db.Column(test_db.String, primary_key=True)
    email = test_db.Column(test_db.String(255), unique=True, nullable=False)
    created_at = test_db.Column(test_db.DateTime, default=datetime.utcnow)
    updated_at = test_db.Column(test_db.DateTime, default=datetime.utcnow)


class TestSupabaseWebhookRoutes:
    @pytest.fixture
    def app(self):
        """Create test Flask app with webhook routes"""
        app = Flask(__name__)
        app.config['TESTING'] = True
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
        
        test_db.init_app(app)
        
        # Import and register blueprint with mocked User model
        with patch('routes.supabase_webhook_routes.db', test_db):
            with patch('routes.supabase_webhook_routes.User', TestUser):
                from routes.supabase_webhook_routes import supabase_webhook_bp
                app.register_blueprint(supabase_webhook_bp)
        
        with app.app_context():
            test_db.create_all()
            
        return app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    def _generate_signature(self, payload: bytes, secret: str) -> str:
        """Generate valid webhook signature"""
        return hmac.new(
            secret.encode('utf-8'),
            payload,
            hashlib.sha256
        ).hexdigest()

    @patch('routes.supabase_webhook_routes.SUPABASE_WEBHOOK_SECRET', 'test-secret')
    def test_webhook_creates_user_on_user_created(self, client, app):
        """Test webhook creates local user on user.created event"""
        payload = {
            'type': 'user.created',
            'table': 'users',
            'record': {
                'id': 'webhook-user-123',
                'email': 'webhook@example.com',
                'email_confirmed_at': None
            }
        }
        payload_bytes = json.dumps(payload).encode('utf-8')
        signature = self._generate_signature(payload_bytes, 'test-secret')
        
        response = client.post(
            '/webhooks/supabase/auth',
            data=payload_bytes,
            content_type='application/json',
            headers={'x-supabase-signature': signature}
        )
        
        assert response.status_code == 200
        assert response.json['status'] == 'success'
        
        # Verify user was created
        with app.app_context():
            user = TestUser.query.get('webhook-user-123')
            assert user is not None
            assert user.email == 'webhook@example.com'

    @patch('routes.supabase_webhook_routes.SUPABASE_WEBHOOK_SECRET', 'test-secret')
    def test_webhook_updates_existing_user(self, client, app):
        """Test webhook updates existing user on user.updated event"""
        # Create existing user
        with app.app_context():
            user = TestUser(id='existing-user-456', email='old@example.com')
            test_db.session.add(user)
            test_db.session.commit()
        
        # Send update event with new email
        payload = {
            'type': 'user.updated',
            'table': 'users',
            'record': {
                'id': 'existing-user-456',
                'email': 'new@example.com',
                'email_confirmed_at': '2025-01-31T00:00:00Z'
            }
        }
        payload_bytes = json.dumps(payload).encode('utf-8')
        signature = self._generate_signature(payload_bytes, 'test-secret')
        
        response = client.post(
            '/webhooks/supabase/auth',
            data=payload_bytes,
            content_type='application/json',
            headers={'x-supabase-signature': signature}
        )
        
        assert response.status_code == 200
        
        # Verify user email was updated
        with app.app_context():
            user = TestUser.query.get('existing-user-456')
            assert user.email == 'new@example.com'

    @patch('routes.supabase_webhook_routes.SUPABASE_WEBHOOK_SECRET', 'test-secret')
    def test_webhook_invalid_signature(self, client):
        """Test webhook rejects invalid signature"""
        payload = {'type': 'user.created', 'record': {'id': '123', 'email': 'test@example.com'}}
        
        response = client.post(
            '/webhooks/supabase/auth',
            json=payload,
            headers={'x-supabase-signature': 'invalid-signature'}
        )
        
        assert response.status_code == 401
        assert 'Invalid signature' in response.json['error']

    @patch('routes.supabase_webhook_routes.SUPABASE_WEBHOOK_SECRET', None)
    def test_webhook_allows_without_secret_in_dev(self, client, app):
        """Test webhook allows requests when secret not configured (dev mode)"""
        payload = {
            'type': 'user.created',
            'record': {
                'id': 'dev-user-789',
                'email': 'dev@example.com'
            }
        }
        
        response = client.post(
            '/webhooks/supabase/auth',
            json=payload
        )
        
        # Should succeed (with warning logged)
        assert response.status_code == 200

    @patch('routes.supabase_webhook_routes.SUPABASE_WEBHOOK_SECRET', 'test-secret')
    def test_webhook_handles_insert_event_type(self, client, app):
        """Test webhook handles INSERT event type (Supabase database webhook format)"""
        payload = {
            'type': 'INSERT',
            'table': 'users',
            'record': {
                'id': 'insert-user-101',
                'email': 'insert@example.com'
            }
        }
        payload_bytes = json.dumps(payload).encode('utf-8')
        signature = self._generate_signature(payload_bytes, 'test-secret')
        
        response = client.post(
            '/webhooks/supabase/auth',
            data=payload_bytes,
            content_type='application/json',
            headers={'x-supabase-signature': signature}
        )
        
        assert response.status_code == 200
        
        with app.app_context():
            user = TestUser.query.get('insert-user-101')
            assert user is not None

    @patch('routes.supabase_webhook_routes.SUPABASE_WEBHOOK_SECRET', 'test-secret')
    def test_webhook_ignores_unknown_event(self, client):
        """Test webhook ignores unknown event types gracefully"""
        payload = {
            'type': 'user.deleted',
            'record': {'id': '123', 'email': 'test@example.com'}
        }
        payload_bytes = json.dumps(payload).encode('utf-8')
        signature = self._generate_signature(payload_bytes, 'test-secret')
        
        response = client.post(
            '/webhooks/supabase/auth',
            data=payload_bytes,
            content_type='application/json',
            headers={'x-supabase-signature': signature}
        )
        
        assert response.status_code == 200
        assert response.json['status'] == 'ignored'

    def test_webhook_health_check(self, client):
        """Test webhook health check endpoint"""
        response = client.get('/webhooks/supabase/auth/health')
        
        assert response.status_code == 200
        assert response.json['status'] == 'ok'

    @patch('routes.supabase_webhook_routes.SUPABASE_WEBHOOK_SECRET', 'test-secret')
    def test_webhook_idempotent_user_creation(self, client, app):
        """Test webhook handles duplicate user creation gracefully"""
        # Create user first
        with app.app_context():
            user = TestUser(id='idempotent-user', email='idem@example.com')
            test_db.session.add(user)
            test_db.session.commit()
        
        # Send create event for same user
        payload = {
            'type': 'user.created',
            'record': {
                'id': 'idempotent-user',
                'email': 'idem@example.com'
            }
        }
        payload_bytes = json.dumps(payload).encode('utf-8')
        signature = self._generate_signature(payload_bytes, 'test-secret')
        
        response = client.post(
            '/webhooks/supabase/auth',
            data=payload_bytes,
            content_type='application/json',
            headers={'x-supabase-signature': signature}
        )
        
        # Should succeed (user already exists)
        assert response.status_code == 200
        assert response.json['message'] == 'User already exists'


if __name__ == '__main__':
    pytest.main([__file__])
