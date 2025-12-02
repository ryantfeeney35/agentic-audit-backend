# test_auth_middleware.py
import pytest
import jwt
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from flask import Flask
from auth import require_auth, validate_token
from models import User, db


class TestAuthMiddleware:
    @pytest.fixture
    def app(self):
        """Create test Flask app"""
        app = Flask(__name__)
        app.config['TESTING'] = True
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
        
        db.init_app(app)
        
        @app.route('/protected')
        @require_auth
        def protected_route():
            return {'message': 'success'}
            
        @app.route('/unprotected')
        def unprotected_route():
            return {'message': 'no auth needed'}
        
        with app.app_context():
            db.create_all()
            
        return app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    @pytest.fixture
    def valid_token(self):
        """Create a valid JWT token for testing"""
        payload = {
            'sub': 'test-user-id',
            'email': 'test@example.com',
            'aud': 'authenticated',
            'exp': datetime.utcnow() + timedelta(hours=1)
        }
        return jwt.encode(payload, 'test-secret', algorithm='HS256')

    @pytest.fixture
    def expired_token(self):
        """Create an expired JWT token for testing"""
        payload = {
            'sub': 'test-user-id', 
            'email': 'test@example.com',
            'aud': 'authenticated',
            'exp': datetime.utcnow() - timedelta(hours=1)
        }
        return jwt.encode(payload, 'test-secret', algorithm='HS256')

    @patch('auth.SUPABASE_JWT_SECRET', 'test-secret')
    @patch('auth.db')
    def test_valid_token_allows_access(self, mock_db, client, valid_token):
        """Test that valid token allows access to protected route"""
        # Mock user lookup
        mock_user = User(id='test-user-id', email='test@example.com')
        mock_db.session.get.return_value = mock_user
        mock_db.session.add = MagicMock()
        mock_db.session.commit = MagicMock()
        
        response = client.get('/protected', headers={
            'Authorization': f'Bearer {valid_token}'
        })
        
        assert response.status_code == 200
        assert response.json['message'] == 'success'

    def test_missing_authorization_header(self, client):
        """Test that missing Authorization header returns 401"""
        response = client.get('/protected')
        assert response.status_code == 401
        assert 'Authorization header missing' in response.json['error']

    def test_invalid_authorization_header_format(self, client):
        """Test that invalid header format returns 401"""
        response = client.get('/protected', headers={
            'Authorization': 'InvalidFormat'
        })
        assert response.status_code == 401
        assert 'Invalid authorization header format' in response.json['error']

    @patch('auth.SUPABASE_JWT_SECRET', 'test-secret')
    def test_expired_token_denied(self, client, expired_token):
        """Test that expired token returns 401"""
        response = client.get('/protected', headers={
            'Authorization': f'Bearer {expired_token}'
        })
        assert response.status_code == 401
        assert 'Invalid or expired token' in response.json['error']

    @patch('auth.SUPABASE_JWT_SECRET', 'test-secret')
    def test_invalid_token_denied(self, client):
        """Test that invalid token returns 401"""
        response = client.get('/protected', headers={
            'Authorization': 'Bearer invalid-token'
        })
        assert response.status_code == 401
        assert 'Invalid or expired token' in response.json['error']

    def test_unprotected_route_accessible(self, client):
        """Test that unprotected routes work without token"""
        response = client.get('/unprotected')
        assert response.status_code == 200
        assert response.json['message'] == 'no auth needed'

    @patch('auth.SUPABASE_JWT_SECRET', 'test-secret')
    @patch('auth.db')
    def test_user_creation_on_first_login(self, mock_db, valid_token):
        """Test that user is created in database on first login"""
        # Mock user not found, then created
        mock_db.session.get.return_value = None
        mock_db.session.add = MagicMock()
        mock_db.session.commit = MagicMock()
        
        user_info = validate_token(valid_token)
        
        assert user_info is not None
        assert user_info['id'] == 'test-user-id'
        assert user_info['email'] == 'test@example.com'
        mock_db.session.add.assert_called_once()
        mock_db.session.commit.assert_called_once()

if __name__ == '__main__':
    pytest.main([__file__])