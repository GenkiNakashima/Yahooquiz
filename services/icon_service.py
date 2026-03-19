# services/icon_service.py
from datetime import datetime
from db import get_db_connection
import logging

logger = logging.getLogger(__name__)

def get_available_icons():
    """Get all available icons"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        i.id,
                        i.name,
                        i.description,
                        i.price,
                        i.is_limited,
                        i.total_supply,
                        COUNT(ui.id) as purchased_count
                    FROM icons i
                    LEFT JOIN user_icons ui ON i.id = ui.icon_id
                    GROUP BY i.id
                    ORDER BY i.price ASC
                """)

                icons = cur.fetchall()

                return [
                    {
                        'id': str(icon['id']),
                        'name': icon['name'],
                        'description': icon['description'],
                        'price': icon['price'],
                        'is_limited': icon['is_limited'],
                        'total_supply': icon['total_supply'],
                        'purchased_count': icon['purchased_count'],
                        'available': not icon['is_limited'] or (
                            icon['total_supply'] and icon['purchased_count'] < icon['total_supply']
                        )
                    }
                    for icon in icons
                ]

    except Exception as e:
        logger.error(f"Error getting icons: {e}")
        return []

def purchase_icon(user_id, icon_id):
    """
    Purchase an icon with points.
    Uses transaction to ensure atomic operation.
    Returns: (success, data_or_error)
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Start transaction
                conn.autocommit = False

                # Lock user row and get current points
                cur.execute("""
                    SELECT points FROM users WHERE id = %s FOR UPDATE
                """, (user_id,))

                user = cur.fetchone()
                if not user:
                    conn.rollback()
                    return False, {'error': 'User not found'}

                # Lock icon row and get info
                cur.execute("""
                    SELECT id, name, price, is_limited, total_supply
                    FROM icons
                    WHERE id = %s
                    FOR UPDATE
                """, (icon_id,))

                icon = cur.fetchone()
                if not icon:
                    conn.rollback()
                    return False, {'error': 'Icon not found'}

                # Check if user already owns this icon
                cur.execute("""
                    SELECT id FROM user_icons
                    WHERE user_id = %s AND icon_id = %s
                """, (user_id, icon_id))

                if cur.fetchone():
                    conn.rollback()
                    return False, {'error': 'You already own this icon'}

                # Check if limited icon is still available
                if icon['is_limited'] and icon['total_supply']:
                    cur.execute("""
                        SELECT COUNT(*) as count
                        FROM user_icons
                        WHERE icon_id = %s
                    """, (icon_id,))

                    purchased = cur.fetchone()
                    if purchased['count'] >= icon['total_supply']:
                        conn.rollback()
                        return False, {'error': 'This limited icon is sold out'}

                # Check if user has enough points
                price = icon['price']
                if user['points'] < price:
                    conn.rollback()
                    return False, {
                        'error': 'Insufficient points',
                        'required': price,
                        'current': user['points']
                    }

                # Deduct points
                cur.execute("""
                    UPDATE users
                    SET points = points - %s, updated_at = now()
                    WHERE id = %s
                """, (price, user_id))

                # Record points transaction
                cur.execute("""
                    INSERT INTO points_transactions (user_id, amount, type, reference_id)
                    VALUES (%s, %s, %s, %s)
                """, (user_id, -price, 'icon_purchase', icon_id))

                # Add icon to user's collection
                cur.execute("""
                    INSERT INTO user_icons (user_id, icon_id, acquired_at)
                    VALUES (%s, %s, %s)
                    RETURNING id, acquired_at
                """, (user_id, icon_id, datetime.utcnow()))

                user_icon = cur.fetchone()

                # Get updated points
                cur.execute("""
                    SELECT points FROM users WHERE id = %s
                """, (user_id,))

                updated_user = cur.fetchone()

                # Commit transaction
                conn.commit()

                return True, {
                    'status': 'purchased',
                    'icon': {
                        'id': str(icon['id']),
                        'name': icon['name']
                    },
                    'remaining_points': updated_user['points'],
                    'acquired_at': user_icon['acquired_at'].isoformat()
                }

    except Exception as e:
        logger.error(f"Error purchasing icon: {e}")
        return False, {'error': 'Failed to purchase icon'}

def get_user_icons(user_id):
    """Get all icons owned by a user"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        i.id,
                        i.name,
                        i.description,
                        i.price,
                        i.is_limited,
                        ui.acquired_at
                    FROM user_icons ui
                    JOIN icons i ON ui.icon_id = i.id
                    WHERE ui.user_id = %s
                    ORDER BY ui.acquired_at DESC
                """, (user_id,))

                icons = cur.fetchall()

                return [
                    {
                        'id': str(icon['id']),
                        'name': icon['name'],
                        'description': icon['description'],
                        'price': icon['price'],
                        'is_limited': icon['is_limited'],
                        'acquired_at': icon['acquired_at'].isoformat()
                    }
                    for icon in icons
                ]

    except Exception as e:
        logger.error(f"Error getting user icons: {e}")
        return []

def get_points_transactions(user_id, limit=50):
    """Get points transaction history for a user"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        id,
                        amount,
                        type,
                        reference_id,
                        created_at
                    FROM points_transactions
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                """, (user_id, limit))

                transactions = cur.fetchall()

                return [
                    {
                        'id': str(tx['id']),
                        'amount': tx['amount'],
                        'type': tx['type'],
                        'reference_id': str(tx['reference_id']) if tx['reference_id'] else None,
                        'created_at': tx['created_at'].isoformat()
                    }
                    for tx in transactions
                ]

    except Exception as e:
        logger.error(f"Error getting points transactions: {e}")
        return []
