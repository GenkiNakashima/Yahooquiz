# api_routes.py
from flask import Blueprint, request, jsonify, session
from services.auth import require_auth
from services.user_service import register_user, login_user, get_user_by_id, refresh_access_token
from services.match_service import record_game_result, get_user_match_history, get_leaderboard
from services.icon_service import get_available_icons, purchase_icon, get_user_icons, get_points_transactions
from middleware.security import validate_json, sanitize_input, validate_uuid
import logging

logger = logging.getLogger(__name__)

# Create blueprint for API routes
api = Blueprint('api', __name__, url_prefix='/api')

# ============================================================================
# Authentication Routes
# ============================================================================

@api.route('/auth/register', methods=['POST'])
@validate_json
def api_register():
    """Register a new user"""
    data = request.get_json()

    email = sanitize_input(data.get('email', ''), 255)
    password = data.get('password', '')
    display_name = sanitize_input(data.get('display_name', ''), 50) if data.get('display_name') else None

    success, result = register_user(email, password, display_name)

    if success:
        return jsonify(result), 201
    else:
        return jsonify(result), 400

@api.route('/auth/login', methods=['POST'])
@validate_json
def api_login():
    """Login and receive tokens"""
    data = request.get_json()

    email = sanitize_input(data.get('email', ''), 255)
    password = data.get('password', '')

    success, result = login_user(email, password)

    if success:
        # Set refresh token as HttpOnly cookie
        response = jsonify({
            'access_token': result['access_token'],
            'token_type': result['token_type'],
            'expires_in': result['expires_in'],
            'user': result['user']
        })
        response.set_cookie(
            'refresh_token',
            result['refresh_token'],
            httponly=True,
            secure=request.is_secure,  # Only HTTPS in production
            samesite='Strict',
            max_age=14*24*60*60  # 14 days
        )
        return response, 200
    else:
        return jsonify(result), 401

@api.route('/auth/refresh', methods=['POST'])
def api_refresh_token():
    """Refresh access token using refresh token from cookie"""
    refresh_token = request.cookies.get('refresh_token')

    if not refresh_token:
        # Also check in request body for non-cookie clients
        data = request.get_json() if request.is_json else {}
        refresh_token = data.get('refresh_token')

    if not refresh_token:
        return jsonify({'error': 'Refresh token required'}), 401

    success, result = refresh_access_token(refresh_token)

    if success:
        return jsonify(result), 200
    else:
        return jsonify(result), 401

# ============================================================================
# User Routes
# ============================================================================

@api.route('/users/me', methods=['GET'])
@require_auth
def api_get_current_user():
    """Get current authenticated user info"""
    user_id = request.current_user['id']
    user = get_user_by_id(user_id)

    if user:
        return jsonify(user), 200
    else:
        return jsonify({'error': 'User not found'}), 404

@api.route('/users/me/icons', methods=['GET'])
@require_auth
def api_get_my_icons():
    """Get icons owned by current user"""
    user_id = request.current_user['id']
    icons = get_user_icons(user_id)
    return jsonify({'icons': icons}), 200

@api.route('/users/me/transactions', methods=['GET'])
@require_auth
def api_get_my_transactions():
    """Get points transaction history for current user"""
    user_id = request.current_user['id']
    limit = request.args.get('limit', 50, type=int)
    transactions = get_points_transactions(user_id, limit)
    return jsonify({'transactions': transactions}), 200

@api.route('/users/me/matches', methods=['GET'])
@require_auth
def api_get_my_matches():
    """Get match history for current user"""
    user_id = request.current_user['id']
    limit = request.args.get('limit', 10, type=int)
    matches = get_user_match_history(user_id, limit)
    return jsonify({'matches': matches}), 200

# ============================================================================
# Match Routes
# ============================================================================

@api.route('/matches', methods=['POST'])
@require_auth
def api_create_match():
    """
    現在のセッションで遊び終えたゲームの結果を、ログイン中のユーザーの対戦結果として記録する。
    勝敗・スコアはサーバー側に記録した回答結果から確定する（クライアントからは指定できない）。
    1ゲームにつき1回だけ記録できる。
    """
    data = request.get_json(silent=True) or {}
    if 'players' in data:
        return jsonify({'error': 'players cannot be specified; match results are determined by the server'}), 400

    quiz_set_id = session.get('quiz_set_id')
    if not quiz_set_id:
        return jsonify({'error': 'No game to record'}), 400

    _, result, status = record_game_result(request.current_user['id'], quiz_set_id)
    return jsonify(result), status

# ============================================================================
# Icon Routes
# ============================================================================

@api.route('/icons', methods=['GET'])
def api_get_icons():
    """Get all available icons"""
    icons = get_available_icons()
    return jsonify({'icons': icons}), 200

@api.route('/icons/<icon_id>/purchase', methods=['POST'])
@require_auth
def api_purchase_icon(icon_id):
    """Purchase an icon with points"""
    if not validate_uuid(icon_id):
        return jsonify({'error': 'Invalid icon ID format'}), 400

    user_id = request.current_user['id']

    success, result = purchase_icon(user_id, icon_id)

    if success:
        return jsonify(result), 200
    else:
        return jsonify(result), 400

# ============================================================================
# Leaderboard Routes
# ============================================================================

@api.route('/leaderboard', methods=['GET'])
def api_get_leaderboard():
    """Get leaderboard of top players"""
    limit = request.args.get('limit', 100, type=int)
    leaderboard = get_leaderboard(limit)
    return jsonify({'leaderboard': leaderboard}), 200

# ============================================================================
# Health Check
# ============================================================================

@api.route('/health', methods=['GET'])
def api_health():
    """Health check endpoint"""
    from db import check_db_health

    db_healthy = check_db_health()

    return jsonify({
        'status': 'healthy' if db_healthy else 'unhealthy',
        'database': 'firestore connected' if db_healthy else 'firestore disconnected'
    }), 200 if db_healthy else 503
