# routes/auth_routes.py
from flask import Blueprint, request, jsonify, g
from auth import supabase, require_auth
import logging

bp = Blueprint('auth', __name__)

@bp.route('/auth/login', methods=['POST'])
def login():
    """
    Authenticate user with email and password via Supabase Auth.
    Returns access token on success.
    """
    try:
        data = request.get_json()
        email = data.get('email')
        password = data.get('password')
        
        if not email or not password:
            return jsonify({'error': 'Email and password required'}), 400
        
        # Authenticate with Supabase
        response = supabase.auth.sign_in_with_password({
            "email": email,
            "password": password
        })
        
        if response.user is None:
            return jsonify({'error': 'Invalid credentials'}), 401
            
        return jsonify({
            'access_token': response.session.access_token,
            'refresh_token': response.session.refresh_token,
            'user': {
                'id': response.user.id,
                'email': response.user.email
            }
        }), 200
        
    except Exception as e:
        logging.error(f"Login error: {e}")
        return jsonify({'error': 'Authentication failed'}), 401

@bp.route('/auth/logout', methods=['POST'])
@require_auth
def logout():
    """
    Logout user by invalidating token.
    """
    try:
        # Get token from Authorization header
        auth_header = request.headers.get('Authorization')
        token = auth_header.split(' ')[1] if auth_header else None
        
        if token:
            # Sign out from Supabase
            supabase.auth.sign_out()
            
        return jsonify({'message': 'Logged out successfully'}), 200
        
    except Exception as e:
        logging.error(f"Logout error: {e}")
        return jsonify({'error': 'Logout failed'}), 500

@bp.route('/auth/me', methods=['GET'])
@require_auth
def get_current_user():
    """
    Return current authenticated user information.
    """
    try:
        user_info = g.current_user
        return jsonify({
            'user': {
                'id': user_info['id'],
                'email': user_info['email']
            }
        }), 200
        
    except Exception as e:
        logging.error(f"Get current user error: {e}")
        return jsonify({'error': 'Failed to get user info'}), 500

@bp.route('/auth/signup', methods=['POST'])
def signup():
    """
    Register new user with email and password via Supabase Auth.
    """
    try:
        data = request.get_json()
        email = data.get('email')
        password = data.get('password')
        
        if not email or not password:
            return jsonify({'error': 'Email and password required'}), 400
        
        if len(password) < 6:
            return jsonify({'error': 'Password must be at least 6 characters'}), 400
        
        # Create user with Supabase
        response = supabase.auth.sign_up({
            "email": email,
            "password": password
        })
        
        if response.user is None:
            return jsonify({'error': 'Registration failed'}), 400
            
        return jsonify({
            'message': 'Registration successful',
            'user': {
                'id': response.user.id,
                'email': response.user.email
            }
        }), 201
        
    except Exception as e:
        logging.error(f"Signup error: {e}")
        return jsonify({'error': 'Registration failed'}), 400