# auth.py
import os
import jwt
from functools import wraps
from flask import request, jsonify, g
from supabase import create_client, Client
from models import User, db
import logging

# Initialize Supabase client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")  
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")

if not all([SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_JWT_SECRET]):
    raise ValueError("Missing required Supabase environment variables")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

def validate_token(token):
    """
    Validate JWT token from Supabase Auth and return user info.
    Returns user dict or None if invalid.
    """
    try:
        # Decode JWT token using Supabase JWT secret
        payload = jwt.decode(
            token, 
            SUPABASE_JWT_SECRET, 
            algorithms=['HS256'],
            audience="authenticated"
        )
        
        user_id = payload.get('sub')
        email = payload.get('email')
        
        if not user_id or not email:
            return None
            
        # Ensure user exists in our local database
        user = User.query.get(user_id)
        if not user:
            # Create user record if doesn't exist
            user = User(id=user_id, email=email)
            db.session.add(user)
            db.session.commit()
            
        return {
            'id': user_id,
            'email': email,
            'user': user
        }
        
    except jwt.ExpiredSignatureError:
        logging.warning("JWT token expired")
        return None
    except jwt.InvalidTokenError:
        logging.warning("JWT token invalid")
        return None
    except Exception as e:
        logging.error(f"Token validation error: {e}")
        return None

def require_auth(f):
    """
    Decorator to require authentication for endpoints.
    Sets g.current_user with validated user info.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        
        if not auth_header:
            return jsonify({'error': 'Authorization header missing'}), 401
            
        # Extract Bearer token
        try:
            token = auth_header.split(' ')[1]  # "Bearer <token>"
        except IndexError:
            return jsonify({'error': 'Invalid authorization header format'}), 401
        
        # Validate token
        user_info = validate_token(token)
        if not user_info:
            return jsonify({'error': 'Invalid or expired token'}), 401
        
        # Set current user in Flask g object
        g.current_user = user_info
        
        return f(*args, **kwargs)
    
    return decorated_function