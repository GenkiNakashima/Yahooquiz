# services/user_service.py
from datetime import datetime, timedelta
from db import get_db_cursor
from services.auth import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    hash_token,
    validate_email,
    validate_password,
    validate_display_name
)
import logging

logger = logging.getLogger(__name__)

def register_user(email, password, display_name=None):
    """
    Register a new user
    Returns: (success, data_or_error)
    """
    # Validate input
    if not validate_email(email):
        return False, {'error': 'Invalid email format'}

    is_valid, msg = validate_password(password)
    if not is_valid:
        return False, {'error': msg}

    if display_name and not validate_display_name(display_name):
        return False, {'error': 'Display name must be between 1 and 50 characters'}

    try:
        with get_db_cursor() as cur:
            # Check if user already exists
            cur.execute("SELECT id FROM users WHERE email = %s", (email,))
            if cur.fetchone():
                return False, {'error': 'Email already registered'}

            # Hash password
            password_hash = hash_password(password)

            # Insert user
            cur.execute("""
                INSERT INTO users (email, password_hash, display_name, points)
                VALUES (%s, %s, %s, %s)
                RETURNING id, email, display_name, points, created_at
            """, (email, password_hash, display_name, 0))

            user = cur.fetchone()

            return True, {
                'id': str(user['id']),
                'email': user['email'],
                'display_name': user['display_name'],
                'points': user['points'],
                'created_at': user['created_at'].isoformat()
            }

    except Exception as e:
        logger.error(f"Error registering user: {e}")
        return False, {'error': 'Failed to register user'}

def login_user(email, password):
    """
    Authenticate user and create tokens
    Returns: (success, data_or_error)
    """
    if not validate_email(email):
        return False, {'error': 'Invalid email format'}

    if not password:
        return False, {'error': 'Password is required'}

    try:
        with get_db_cursor() as cur:
            # Get user by email
            cur.execute("""
                SELECT id, email, password_hash, display_name, points
                FROM users
                WHERE email = %s
            """, (email,))

            user = cur.fetchone()

            if not user:
                return False, {'error': 'Invalid email or password'}

            # Verify password
            if not verify_password(password, user['password_hash']):
                return False, {'error': 'Invalid email or password'}

            user_id = str(user['id'])

            # Create tokens
            access_token = create_access_token(user_id, user['email'])
            refresh_token = create_refresh_token(user_id)

            # Store refresh token hash in database
            token_hash = hash_token(refresh_token)
            expires_at = datetime.utcnow() + timedelta(days=14)

            cur.execute("""
                INSERT INTO auth_refresh_tokens (user_id, token_hash, expires_at)
                VALUES (%s, %s, %s)
            """, (user['id'], token_hash, expires_at))

            return True, {
                'access_token': access_token,
                'refresh_token': refresh_token,
                'token_type': 'bearer',
                'expires_in': 900,  # 15 minutes in seconds
                'user': {
                    'id': user_id,
                    'email': user['email'],
                    'display_name': user['display_name'],
                    'points': user['points']
                }
            }

    except Exception as e:
        logger.error(f"Error logging in user: {e}")
        return False, {'error': 'Failed to login'}

def get_user_by_id(user_id):
    """Get user information by ID"""
    try:
        with get_db_cursor(commit=False) as cur:
            cur.execute("""
                SELECT id, email, display_name, points, created_at
                FROM users
                WHERE id = %s
            """, (user_id,))

            user = cur.fetchone()
            if not user:
                return None

            # Get user's icons
            cur.execute("""
                SELECT i.id, i.name, i.description, i.price
                FROM user_icons ui
                JOIN icons i ON ui.icon_id = i.id
                WHERE ui.user_id = %s
                ORDER BY ui.acquired_at DESC
            """, (user_id,))

            icons = cur.fetchall()

            return {
                'id': str(user['id']),
                'email': user['email'],
                'display_name': user['display_name'],
                'points': user['points'],
                'created_at': user['created_at'].isoformat(),
                'icons': [
                    {
                        'id': str(icon['id']),
                        'name': icon['name'],
                        'description': icon['description'],
                        'price': icon['price']
                    }
                    for icon in icons
                ]
            }

    except Exception as e:
        logger.error(f"Error getting user: {e}")
        return None

def refresh_access_token(refresh_token):
    """
    Create new access token using refresh token
    Returns: (success, data_or_error)
    """
    from services.auth import decode_refresh_token

    # Decode refresh token
    payload = decode_refresh_token(refresh_token)
    if not payload:
        return False, {'error': 'Invalid or expired refresh token'}

    user_id = payload['sub']
    token_hash = hash_token(refresh_token)

    try:
        with get_db_cursor() as cur:
            # Verify refresh token exists and is not revoked
            cur.execute("""
                SELECT user_id, expires_at, revoked
                FROM auth_refresh_tokens
                WHERE token_hash = %s AND user_id = %s
            """, (token_hash, user_id))

            token_record = cur.fetchone()

            if not token_record:
                return False, {'error': 'Invalid refresh token'}

            if token_record['revoked']:
                return False, {'error': 'Refresh token has been revoked'}

            if token_record['expires_at'] < datetime.utcnow():
                return False, {'error': 'Refresh token has expired'}

            # Get user info
            cur.execute("SELECT email FROM users WHERE id = %s", (user_id,))
            user = cur.fetchone()

            if not user:
                return False, {'error': 'User not found'}

            # Create new access token
            access_token = create_access_token(user_id, user['email'])

            return True, {
                'access_token': access_token,
                'token_type': 'bearer',
                'expires_in': 900
            }

    except Exception as e:
        logger.error(f"Error refreshing token: {e}")
        return False, {'error': 'Failed to refresh token'}
