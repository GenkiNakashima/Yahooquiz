# services/icon_service.py
import uuid
from datetime import datetime
from db import get_db
from firebase_admin import firestore as fs
import logging

logger = logging.getLogger(__name__)

def _normalize_dt(dt):
    if dt is None:
        return None
    if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt

def get_available_icons():
    """Get all available icons"""
    try:
        db = get_db()
        icon_docs = list(db.collection('icons').order_by('price').stream())

        results = []
        for icon_doc in icon_docs:
            icon = icon_doc.to_dict()
            purchased_count = len(
                list(db.collection('user_icons').where('icon_id', '==', icon_doc.id).stream())
            )
            total_supply = icon.get('total_supply')
            is_limited = icon.get('is_limited', False)

            results.append({
                'id': icon_doc.id,
                'name': icon['name'],
                'description': icon.get('description'),
                'price': icon['price'],
                'is_limited': is_limited,
                'total_supply': total_supply,
                'purchased_count': purchased_count,
                'available': not is_limited or (
                    total_supply and purchased_count < total_supply
                )
            })

        return results

    except Exception as e:
        logger.error(f"Error getting icons: {e}")
        return []

def purchase_icon(user_id, icon_id):
    """
    Purchase an icon with points.
    Returns: (success, data_or_error)
    """
    try:
        db = get_db()

        # Get user
        user_ref = db.collection('users').document(user_id)
        user_doc = user_ref.get()
        if not user_doc.exists:
            return False, {'error': 'User not found'}
        user = user_doc.to_dict()

        # Get icon
        icon_ref = db.collection('icons').document(icon_id)
        icon_doc = icon_ref.get()
        if not icon_doc.exists:
            return False, {'error': 'Icon not found'}
        icon = icon_doc.to_dict()

        # Check if user already owns this icon
        existing = list(
            db.collection('user_icons')
            .where('user_id', '==', user_id)
            .where('icon_id', '==', icon_id)
            .limit(1)
            .stream()
        )
        if existing:
            return False, {'error': 'You already own this icon'}

        # Check if limited icon is still available
        if icon.get('is_limited') and icon.get('total_supply'):
            purchased_count = len(
                list(db.collection('user_icons').where('icon_id', '==', icon_id).stream())
            )
            if purchased_count >= icon['total_supply']:
                return False, {'error': 'This limited icon is sold out'}

        # Check if user has enough points
        price = icon['price']
        current_points = user.get('points', 0)
        if current_points < price:
            return False, {
                'error': 'Insufficient points',
                'required': price,
                'current': current_points
            }

        now = datetime.utcnow()

        # Deduct points atomically
        user_ref.update({
            'points': fs.Increment(-price),
            'updated_at': now
        })

        # Record points transaction
        pt_id = str(uuid.uuid4())
        db.collection('points_transactions').document(pt_id).set({
            'user_id': user_id,
            'amount': -price,
            'type': 'icon_purchase',
            'reference_id': icon_id,
            'created_at': now
        })

        # Add icon to user's collection
        ui_id = str(uuid.uuid4())
        db.collection('user_icons').document(ui_id).set({
            'user_id': user_id,
            'icon_id': icon_id,
            'acquired_at': now
        })

        # Get updated points
        updated_user = db.collection('users').document(user_id).get()
        remaining_points = updated_user.to_dict().get('points', current_points - price)

        return True, {
            'status': 'purchased',
            'icon': {
                'id': icon_id,
                'name': icon['name']
            },
            'remaining_points': remaining_points,
            'acquired_at': now.isoformat()
        }

    except Exception as e:
        logger.error(f"Error purchasing icon: {e}")
        return False, {'error': 'Failed to purchase icon'}

def get_user_icons(user_id):
    """Get all icons owned by a user"""
    try:
        db = get_db()
        user_icons = list(db.collection('user_icons').where('user_id', '==', user_id).stream())

        results = []
        for ui_doc in user_icons:
            ui = ui_doc.to_dict()
            icon_doc = db.collection('icons').document(ui['icon_id']).get()
            if icon_doc.exists:
                icon = icon_doc.to_dict()
                acquired_at = _normalize_dt(ui.get('acquired_at'))
                results.append({
                    'id': icon_doc.id,
                    'name': icon['name'],
                    'description': icon.get('description'),
                    'price': icon['price'],
                    'is_limited': icon.get('is_limited', False),
                    'acquired_at': acquired_at.isoformat() if acquired_at else None
                })

        results.sort(key=lambda x: x['acquired_at'] or '', reverse=True)
        return results

    except Exception as e:
        logger.error(f"Error getting user icons: {e}")
        return []

def get_points_transactions(user_id, limit=50):
    """Get points transaction history for a user"""
    try:
        db = get_db()
        tx_docs = list(db.collection('points_transactions').where('user_id', '==', user_id).stream())

        results = []
        for tx_doc in tx_docs:
            tx = tx_doc.to_dict()
            created_at = _normalize_dt(tx.get('created_at'))
            results.append({
                'id': tx_doc.id,
                'amount': tx.get('amount'),
                'type': tx.get('type'),
                'reference_id': tx.get('reference_id'),
                'created_at': created_at.isoformat() if created_at else None
            })

        results.sort(key=lambda x: x['created_at'] or '', reverse=True)
        return results[:limit]

    except Exception as e:
        logger.error(f"Error getting points transactions: {e}")
        return []
