# test_auth_routes.py
import pytest
from unittest.mock import patch, MagicMock
from flask import Flask
from models import db
from routes.auth_routes import bp as auth_bp


class TestAuthRoutes:
    @pytest.fixture
    def app(self):
        """Create test Flask app with auth routes"""
        app = Flask(__name__)
        app.config['TESTING'] = True
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
        
        db.init_app(app)
        app.register_blueprint(auth_bp, url_prefix='/api')
        
        with app.app_context():
            db.create_all()
            
        return app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    @patch('routes.auth_routes.supabase')
    def test_login_success(self, mock_supabase, client):
        """Test successful login"""
        # Mock Supabase response
        mock_response = MagicMock()
        mock_response.user = MagicMock()
        mock_response.user.id = 'user-123'
        mock_response.user.email = 'test@example.com'
        mock_response.session = MagicMock()
        mock_response.session.access_token = 'access-token-123'
        mock_response.session.refresh_token = 'refresh-token-123'
        
        mock_supabase.auth.sign_in_with_password.return_value = mock_response
        
        login_data = {
            'email': 'test@example.com',
            'password': 'password123'
        }
        
        response = client.post('/api/auth/login', json=login_data)
        
        assert response.status_code == 200
        assert 'access_token' in response.json
        assert response.json['access_token'] == 'access-token-123'
        assert response.json['user']['email'] == 'test@example.com'

    @patch('routes.auth_routes.supabase')
    def test_login_invalid_credentials(self, mock_supabase, client):
        """Test login with invalid credentials"""
        # Mock Supabase error response
        mock_supabase.auth.sign_in_with_password.side_effect = Exception("Invalid credentials")
        
        login_data = {
            'email': 'test@example.com',
            'password': 'wrongpassword'
        }
        
        response = client.post('/api/auth/login', json=login_data)
        
        assert response.status_code == 401
        assert 'error' in response.json

    def test_login_missing_fields(self, client):
        """Test login with missing fields"""
        # Missing password
        response = client.post('/api/auth/login', json={'email': 'test@example.com'})
        assert response.status_code == 400
        assert 'Email and password required' in response.json['error']
        
        # Missing email
        response = client.post('/api/auth/login', json={'password': 'password123'})
        assert response.status_code == 400
        assert 'Email and password required' in response.json['error']

    @patch('routes.auth_routes.supabase')
    def test_signup_success(self, mock_supabase, client):
        """Test successful signup"""
        # Mock Supabase response
        mock_response = MagicMock()
        mock_response.user = MagicMock()
        mock_response.user.id = 'user-123'
        mock_response.user.email = 'newuser@example.com'
        
        mock_supabase.auth.sign_up.return_value = mock_response
        
        signup_data = {
            'email': 'newuser@example.com',
            'password': 'password123'
        }
        
        response = client.post('/api/auth/signup', json=signup_data)
        
        assert response.status_code == 201
        assert 'Registration successful' in response.json['message']
        assert response.json['user']['email'] == 'newuser@example.com'

    @patch('routes.auth_routes.supabase')
    def test_signup_weak_password(self, mock_supabase, client):
        """Test signup with weak password"""
        signup_data = {
            'email': 'newuser@example.com',
            'password': '123'  # Too short
        }
        
        response = client.post('/api/auth/signup', json=signup_data)
        
        assert response.status_code == 400
        assert 'Password must be at least 6 characters' in response.json['error']

    @patch('routes.auth_routes.supabase')
    @patch('auth.validate_token')
    def test_get_current_user(self, mock_validate_token, mock_supabase, client):
        """Test get current user endpoint"""
        # Mock token validation
        mock_validate_token.return_value = {
            'id': 'user-123',
            'email': 'test@example.com'
        }
        
        response = client.get('/api/auth/me', headers={
            'Authorization': 'Bearer valid-token'
        })
        
        assert response.status_code == 200
        assert response.json['user']['email'] == 'test@example.com'

    @patch('routes.auth_routes.supabase')
    @patch('auth.validate_token')
    def test_logout_success(self, mock_validate_token, mock_supabase, client):
        """Test successful logout"""
        # Mock token validation
        mock_validate_token.return_value = {
            'id': 'user-123',
            'email': 'test@example.com'
        }
        
        # Mock Supabase logout
        mock_supabase.auth.sign_out.return_value = None
        
        response = client.post('/api/auth/logout', headers={
            'Authorization': 'Bearer valid-token'
        })
        
        assert response.status_code == 200
        assert 'Logged out successfully' in response.json['message']

if __name__ == '__main__':
    pytest.main([__file__])