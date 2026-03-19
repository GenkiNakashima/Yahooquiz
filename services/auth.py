# services/auth.py
import os
import jwt
import bcrypt
import hashlib
from datetime import datetime, timedelta
from functools import wraps
from flask import request, jsonify
import logging

logger = logging.getLogger(__name__)

# JWT Configuration
JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY', 'your-secret-key-change-this')
JWT_ALGORITHM = 'HS256'
ACCESS_TOKEN_EXPIRES = timedelta(minutes=15)
REFRESH_TOKEN_EXPIRES_DAYS = 14

def hash_password(password):
    """Hash a password using bcrypt"""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def verify_password(password, password_hash):
    """Verify a password against a hash"""
    try:
        return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))
    except Exception as e:
        logger.error(f"Password verification error: {e}")
        return False

def create_access_token(user_id, email):
    """Create a JWT access token"""
    now = datetime.utcnow()
    payload = {
        'sub': str(user_id),
        'email': email,
        'iat': now,
        'exp': now + ACCESS_TOKEN_EXPIRES,
        'type': 'access'
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

def create_refresh_token(user_id):
    """Create a JWT refresh token"""
    now = datetime.utcnow()
    payload = {
        'sub': str(user_id),
        'iat': now,
        'exp': now + timedelta(days=REFRESH_TOKEN_EXPIRES_DAYS),
        'type': 'refresh'
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

def hash_token(token):
    """Hash a token for secure storage"""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()

def decode_access_token(token):
    """Decode and verify a JWT access token"""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        if payload.get('type') != 'access':
            raise ValueError('Invalid token type')
        return payload
    except jwt.ExpiredSignatureError:
        logger.warning("Token has expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid token: {e}")
        return None

def decode_refresh_token(token):
    """Decode and verify a JWT refresh token"""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        if payload.get('type') != 'refresh':
            raise ValueError('Invalid token type')
        return payload
    except jwt.ExpiredSignatureError:
        logger.warning("Refresh token has expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid refresh token: {e}")
        return None

def get_token_from_header():
    """Extract Bearer token from Authorization header"""
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return None

    parts = auth_header.split()
    if len(parts) != 2 or parts[0].lower() != 'bearer':
        return None

    return parts[1]

def require_auth(f):
    """
    Decorator to require authentication for routes.
    Usage:
        @app.route('/api/users/me')
        @require_auth
        def get_current_user(current_user):
            return jsonify(current_user)
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = get_token_from_header()
        if not token:
            return jsonify({'error': 'Missing authorization token'}), 401

        payload = decode_access_token(token)
        if not payload:
            return jsonify({'error': 'Invalid or expired token'}), 401

        # Pass user info to the route handler
        request.current_user = {
            'id': payload['sub'],
            'email': payload.get('email')
        }

        return f(*args, **kwargs)

    return decorated_function

def validate_email(email):
    """Basic email validation"""
    if not email or '@' not in email:
        return False
    # More thorough validation can be added here
    return len(email) >= 3 and len(email) <= 255

def validate_password(password):
    """
    Password validation.
    Requirements:
    - At least 8 characters
    - Contains at least one letter and one number
    """
    if not password or len(password) < 8:
        return False, "Password must be at least 8 characters long"

    has_letter = any(c.isalpha() for c in password)
    has_number = any(c.isdigit() for c in password)

    if not has_letter or not has_number:
        return False, "Password must contain both letters and numbers"

    return True, "Valid"

def validate_display_name(display_name):
    """Validate display name"""
    if not display_name:
        return True  # Optional field

    if len(display_name) < 1 or len(display_name) > 50:
        return False

    return True
