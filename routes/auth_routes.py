# routes/auth_routes.py
from flask import Blueprint, request, jsonify, g
from auth import supabase, require_auth
from models import User, db
import logging

bp = Blueprint('auth', __name__)


def _ensure_local_user(user_id: str, email: str) -> User:
    """
    Ensure a local User record exists for the given Supabase user.
    Creates the record if it doesn't exist, returns existing if it does.
    This is idempotent and safe to call multiple times.
    """
    try:
        user = User.query.get(user_id)
        if not user:
            user = User(id=user_id, email=email)
            db.session.add(user)
            db.session.commit()
            logging.info(f"Created local user record for {email} (id: {user_id})")
        return user
    except Exception as e:
        db.session.rollback()
        logging.error(f"Failed to ensure local user record: {e}")
        raise


@bp.route('/auth/login', methods=['POST'])
def login():
    """
    Authenticate user with email and password via Supabase Auth.
    Returns access token on success.
    Also ensures user exists in local database.
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
        
        # Ensure user exists in local database (handles first login after email confirmation)
        try:
            _ensure_local_user(response.user.id, response.user.email)
        except Exception as e:
            logging.warning(f"Could not sync local user on login: {e}")
            # Don't fail login - user can still authenticate, sync will happen on next API call
            
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
    Creates local user record immediately.
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
        
        # Create local user record immediately
        try:
            _ensure_local_user(response.user.id, response.user.email)
        except Exception as e:
            logging.warning(f"Could not create local user record during signup: {e}")
            # Don't fail signup - Supabase user exists, local record will sync on login
            
        return jsonify({
            'message': 'Registration successful',
            'user': {
                'id': response.user.id,
                'email': response.user.email
            }
        }), 201
        
    except Exception as e:
        logging.error(f"Signup error: {e}")
        # Check for common Supabase errors
        error_msg = str(e).lower()
        if 'already registered' in error_msg or 'already exists' in error_msg:
            return jsonify({'error': 'An account with this email already exists'}), 400
        return jsonify({'error': 'Registration failed'}), 400


@bp.route('/auth/sync-user', methods=['POST'])
@require_auth
def sync_user():
    """
    Ensure authenticated user exists in local database.
    Called after email confirmation to sync user record.
    """
    try:
        user_info = g.current_user
        # User already synced by require_auth/validate_token decorator
        return jsonify({
            'synced': True,
            'user': {
                'id': user_info['id'],
                'email': user_info['email']
            }
        }), 200
    except Exception as e:
        logging.error(f"User sync error: {e}")
        return jsonify({'error': 'Failed to sync user'}), 500