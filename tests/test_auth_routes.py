# test_auth_routes.py
import pytest
from unittest.mock import patch, MagicMock
from flask import Flask
import sqlalchemy as sa

# Create a minimal User model for tests (avoids importing models with JSONB)
from flask_sqlalchemy import SQLAlchemy
test_db = SQLAlchemy()

class TestUser(test_db.Model):
    """Minimal User model for auth tests - avoids JSONB incompatibility with SQLite"""
    __tablename__ = 'users'
    id = test_db.Column(test_db.String, primary_key=True)
    email = test_db.Column(test_db.String(255), unique=True, nullable=False)


class TestAuthRoutes:
    @pytest.fixture
    def app(self):
        """Create test Flask app with auth routes"""
        app = Flask(__name__)
        app.config['TESTING'] = True
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
        
        test_db.init_app(app)
        
        # Import and register blueprint with mocked dependencies
        with patch('routes.auth_routes.db', test_db):
            with patch('routes.auth_routes.User', TestUser):
                from routes.auth_routes import bp as auth_bp
                app.register_blueprint(auth_bp, url_prefix='/api')
        
        with app.app_context():
            test_db.create_all()
            
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

    @patch('routes.auth_routes._ensure_local_user')
    @patch('routes.auth_routes.supabase')
    def test_signup_success_requires_confirmation(self, mock_supabase, mock_ensure_user, client):
        """Test successful signup with email confirmation required"""
        # Mock Supabase response - unconfirmed user (empty identities)
        mock_response = MagicMock()
        mock_response.user = MagicMock()
        mock_response.user.id = 'user-123'
        mock_response.user.email = 'newuser@example.com'
        mock_response.user.identities = []  # Empty = unconfirmed
        mock_response.user.confirmed_at = None
        
        mock_supabase.auth.sign_up.return_value = mock_response
        mock_ensure_user.return_value = MagicMock()
        
        signup_data = {
            'email': 'newuser@example.com',
            'password': 'password123'
        }
        
        response = client.post('/api/auth/signup', json=signup_data)
        
        assert response.status_code == 201
        assert 'check your email' in response.json['message'].lower()
        assert response.json['requires_confirmation'] == True
        assert response.json['user']['email'] == 'newuser@example.com'
        # Verify local user was created
        mock_ensure_user.assert_called_once_with('user-123', 'newuser@example.com')

    @patch('routes.auth_routes._ensure_local_user')
    @patch('routes.auth_routes.supabase')
    def test_signup_success_no_confirmation(self, mock_supabase, mock_ensure_user, client):
        """Test successful signup when email confirmation is disabled"""
        # Mock Supabase response - confirmed user
        mock_response = MagicMock()
        mock_response.user = MagicMock()
        mock_response.user.id = 'user-123'
        mock_response.user.email = 'newuser@example.com'
        mock_response.user.identities = [{'id': '123'}]  # Has identity = confirmed
        mock_response.user.confirmed_at = '2025-01-31T00:00:00Z'
        
        mock_supabase.auth.sign_up.return_value = mock_response
        mock_ensure_user.return_value = MagicMock()
        
        signup_data = {
            'email': 'newuser@example.com',
            'password': 'password123'
        }
        
        response = client.post('/api/auth/signup', json=signup_data)
        
        assert response.status_code == 201
        assert response.json['requires_confirmation'] == False

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
    def test_resend_confirmation(self, mock_supabase, client):
        """Test resend confirmation email endpoint"""
        mock_supabase.auth.resend.return_value = MagicMock()
        
        response = client.post('/api/auth/resend-confirmation', json={
            'email': 'test@example.com'
        })
        
        assert response.status_code == 200
        assert 'confirmation link has been sent' in response.json['message'].lower()

    @patch('routes.auth_routes.supabase')
    def test_resend_confirmation_missing_email(self, mock_supabase, client):
        """Test resend confirmation with missing email"""
        response = client.post('/api/auth/resend-confirmation', json={})
        
        assert response.status_code == 400
        assert 'Email is required' in response.json['error']

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
    def test_sync_user(self, mock_validate_token, mock_supabase, client):
        """Test user sync endpoint"""
        mock_validate_token.return_value = {
            'id': 'user-123',
            'email': 'test@example.com'
        }
        
        response = client.post('/api/auth/sync-user', headers={
            'Authorization': 'Bearer valid-token'
        })
        
        assert response.status_code == 200
        assert response.json['synced'] == True
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