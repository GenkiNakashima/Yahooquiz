# middleware/security.py
from flask import request, jsonify
from functools import wraps
import logging

logger = logging.getLogger(__name__)

# CORS allowed origins (configure based on your deployment)
ALLOWED_ORIGINS = [
    'http://localhost:3000',
    'http://localhost:5000',
    'https://your-app.web.app',
    'https://your-app.firebaseapp.com'
]

def setup_security_headers(app):
    """Setup security headers for all responses"""

    @app.after_request
    def add_security_headers(response):
        # XSS Protection
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['X-XSS-Protection'] = '1; mode=block'

        # Content Security Policy
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "font-src 'self'; "
            "connect-src 'self' https://generativelanguage.googleapis.com"
        )

        # HSTS (Strict-Transport-Security) - only for HTTPS
        if request.is_secure:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'

        return response

def validate_json(f):
    """Decorator to validate JSON request body"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if request.method in ['POST', 'PUT', 'PATCH']:
            if not request.is_json:
                return jsonify({'error': 'Content-Type must be application/json'}), 400

        return f(*args, **kwargs)

    return decorated_function

def rate_limit_check(user_id=None, endpoint=None):
    """
    Basic rate limiting check.
    In production, use Redis or similar for distributed rate limiting.
    """
    # TODO: Implement proper rate limiting with Redis
    # For now, this is a placeholder
    return True

def sanitize_input(text, max_length=1000):
    """
    Sanitize user input to prevent XSS attacks.
    Remove or escape potentially dangerous characters.
    """
    if not text:
        return text

    # Remove null bytes
    text = text.replace('\x00', '')

    # Truncate if too long
    if len(text) > max_length:
        text = text[:max_length]

    # Basic HTML escaping (Flask's jsonify handles this automatically)
    return text.strip()

def validate_uuid(uuid_string):
    """Validate UUID format"""
    import re
    uuid_pattern = re.compile(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        re.IGNORECASE
    )
    return bool(uuid_pattern.match(uuid_string))
