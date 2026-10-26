# services/user_service.py
import uuid
from datetime import datetime, timedelta
from db import get_db
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

def _normalize_dt(dt):
    """Strip timezone info from datetime for comparison"""
    if dt is None:
        return None
    if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt

def register_user(email, password, display_name=None):
    """
    Register a new user
    Returns: (success, data_or_error)
    """
    if not validate_email(email):
        return False, {'error': 'Invalid email format'}

    is_valid, msg = validate_password(password)
    if not is_valid:
        return False, {'error': msg}

    if display_name and not validate_display_name(display_name):
        return False, {'error': 'Display name must be between 1 and 50 characters'}

    try:
        db = get_db()

        # Check if email already exists
        existing = list(db.collection('users').where('email', '==', email).limit(1).stream())
        if existing:
            return False, {'error': 'Email already registered'}

        # Create user
        user_id = str(uuid.uuid4())
        now = datetime.utcnow()
        user_data = {
            'email': email,
            'password_hash': hash_password(password),
            'display_name': display_name,
            'points': 0,
            'total_matches': 0,
            'wins': 0,
            'created_at': now,
            'updated_at': now
        }
        db.collection('users').document(user_id).set(user_data)

        return True, {
            'id': user_id,
            'email': email,
            'display_name': display_name,
            'points': 0,
            'created_at': now.isoformat()
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
        db = get_db()

        # Get user by email
        users = list(db.collection('users').where('email', '==', email).limit(1).stream())
        if not users:
            return False, {'error': 'Invalid email or password'}

        user_doc = users[0]
        user = user_doc.to_dict()
        user_id = user_doc.id

        if not verify_password(password, user['password_hash']):
            return False, {'error': 'Invalid email or password'}

        # Create tokens
        access_token = create_access_token(user_id, user['email'])
        refresh_token = create_refresh_token(user_id)

        # Store refresh token hash
        token_hash = hash_token(refresh_token)
        expires_at = datetime.utcnow() + timedelta(days=14)
        db.collection('auth_refresh_tokens').document(token_hash).set({
            'user_id': user_id,
            'token_hash': token_hash,
            'expires_at': expires_at,
            'revoked': False,
            'created_at': datetime.utcnow()
        })

        return True, {
            'access_token': access_token,
            'refresh_token': refresh_token,
            'token_type': 'bearer',
            'expires_in': 900,
            'user': {
                'id': user_id,
                'email': user['email'],
                'display_name': user.get('display_name'),
                'points': user.get('points', 0)
            }
        }

    except Exception as e:
        logger.error(f"Error logging in user: {e}")
        return False, {'error': 'Failed to login'}

def get_user_by_id(user_id):
    """Get user information by ID"""
    try:
        db = get_db()
        user_doc = db.collection('users').document(user_id).get()
        if not user_doc.exists:
            return None

        user = user_doc.to_dict()

        # Get user's icons
        user_icons = list(db.collection('user_icons').where('user_id', '==', user_id).stream())
        icons = []
        for ui_doc in user_icons:
            ui = ui_doc.to_dict()
            icon_doc = db.collection('icons').document(ui['icon_id']).get()
            if icon_doc.exists:
                icon = icon_doc.to_dict()
                icons.append({
                    'id': icon_doc.id,
                    'name': icon['name'],
                    'description': icon.get('description'),
                    'price': icon['price']
                })

        created_at = user.get('created_at')
        created_at_str = _normalize_dt(created_at).isoformat() if created_at else None

        return {
            'id': user_id,
            'email': user['email'],
            'display_name': user.get('display_name'),
            'points': user.get('points', 0),
            'created_at': created_at_str,
            'icons': icons
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

    payload = decode_refresh_token(refresh_token)
    if not payload:
        return False, {'error': 'Invalid or expired refresh token'}

    user_id = payload['sub']
    token_hash = hash_token(refresh_token)

    try:
        db = get_db()
        token_doc = db.collection('auth_refresh_tokens').document(token_hash).get()
        if not token_doc.exists:
            return False, {'error': 'Invalid refresh token'}

        token_record = token_doc.to_dict()

        if token_record.get('revoked'):
            return False, {'error': 'Refresh token has been revoked'}

        expires_at = _normalize_dt(token_record.get('expires_at'))
        if expires_at and expires_at < datetime.utcnow():
            return False, {'error': 'Refresh token has expired'}

        user_doc = db.collection('users').document(user_id).get()
        if not user_doc.exists:
            return False, {'error': 'User not found'}

        user = user_doc.to_dict()
        access_token = create_access_token(user_id, user['email'])

        return True, {
            'access_token': access_token,
            'token_type': 'bearer',
            'expires_in': 900
        }

    except Exception as e:
        logger.error(f"Error refreshing token: {e}")
        return False, {'error': 'Failed to refresh token'}
