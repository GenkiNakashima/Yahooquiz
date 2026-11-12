# services/icon_service.py
import uuid
from datetime import datetime
from db import get_db
from firebase_admin import firestore as fs
from google.api_core import exceptions as gexc
import logging

logger = logging.getLogger(__name__)

# 同じドキュメントへの同時更新でトランザクションが競合したときの最大試行回数（ライブラリの既定は5回）。
# 競合して再試行するトランザクションは順番待ちの位置を保つため、同時アクセス数ぶんの回数があれば成功する。
TRANSACTION_MAX_ATTEMPTS = 10

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

def user_icon_doc_id(user_id, icon_id):
    """user_icons のドキュメント ID。ユーザーとアイコンの組で1つに決まるため、同じアイコンを2つ持つことはない"""
    return f'{user_id}_{icon_id}'


PURCHASE_CONFLICT_ERROR = 'The purchase conflicted with another request. Please try again.'


def _is_contention(e):
    """
    同時アクセスによる競合か。読み取り時のロック待ちの打ち切りは Aborted がそのまま、
    コミットの再試行を max_attempts 回使い切った場合は Aborted を原因とする ValueError が送出される。
    """
    return isinstance(e, gexc.Aborted) or (isinstance(e, ValueError) and isinstance(e.__cause__, gexc.Aborted))


class _PurchaseRejected(Exception):
    """購入できない（残高不足・所持済み・売り切れなど）。トランザクションを中断し、書き込みは行わない"""

    def __init__(self, payload):
        super().__init__(payload.get('error'))
        self.payload = payload


def purchase_icon(user_id, icon_id):
    """
    Purchase an icon with points.
    残高チェック → ポイント消費 → user_icons 登録 → ポイント履歴の記録 を1つのトランザクションで行う。
    Returns: (success, data_or_error)
        失敗時の data_or_error に 'conflict': True が含まれる場合は、同時アクセスによる競合（再試行すれば成功しうる）
    """
    try:
        db = get_db()
        return True, _purchase_in_transaction(db.transaction(max_attempts=TRANSACTION_MAX_ATTEMPTS), db, user_id, icon_id)
    except _PurchaseRejected as e:
        return False, e.payload
    except gexc.AlreadyExists:
        # 同じユーザーが同じアイコンを同時に購入し、user_icons の作成が衝突した（通常は読み取りの段階で弾かれる）
        return False, {'error': 'You already own this icon'}
    except Exception as e:
        if _is_contention(e):
            logger.warning(f"Icon purchase conflicted: {type(e).__name__}: {e}")
            return False, {'error': PURCHASE_CONFLICT_ERROR, 'conflict': True}
        logger.error(f"Error purchasing icon: {e}")
        return False, {'error': 'Failed to purchase icon'}


@fs.transactional
def _purchase_in_transaction(transaction, db, user_id, icon_id):
    """
    同じユーザーの購入どうしは users ドキュメント、同じ限定アイコンの購入どうしは icons ドキュメントへの
    書き込みで競合するため、Firestore がトランザクションを直列化（再試行）する。これにより、同時に購入
    されても二重購入・残高のマイナス・限定数の超過は起きない。

    トランザクション内ではクエリを使わず、ID を指定したドキュメントの読み書きだけを行う。クエリがどの範囲を
    ロックするかは公式には明記されておらず、エミュレーターではコレクション全体への挿入と競合するため
    （別ユーザーの購入どうしでも再試行が起きる）、ロックの粒度に依存しない形にしている。
    Firestore のトランザクションでは、読み取りをすべて書き込みより先に行う必要がある。
    """
    user_ref = db.collection('users').document(user_id)
    icon_ref = db.collection('icons').document(icon_id)
    owned_ref = db.collection('user_icons').document(user_icon_doc_id(user_id, icon_id))

    user_doc = user_ref.get(transaction=transaction)
    if not user_doc.exists:
        raise _PurchaseRejected({'error': 'User not found'})
    user = user_doc.to_dict()

    icon_doc = icon_ref.get(transaction=transaction)
    if not icon_doc.exists:
        raise _PurchaseRejected({'error': 'Icon not found'})
    icon = icon_doc.to_dict()

    # Check if user already owns this icon（ユーザーとアイコンの組で ID が決まるので、1件読むだけでよい）
    if owned_ref.get(transaction=transaction).exists:
        raise _PurchaseRejected({'error': 'You already own this icon'})

    # Check if limited icon is still available
    # 販売数は icons/{icon_id}.purchased_count で管理する（既存データは scripts/migrate_user_icons.py で設定済みにする）
    is_limited = bool(icon.get('is_limited') and icon.get('total_supply'))
    if is_limited:
        purchased_count = icon.get('purchased_count', 0)
        if purchased_count >= icon['total_supply']:
            raise _PurchaseRejected({'error': 'This limited icon is sold out'})

    # Check if user has enough points
    price = icon['price']
    current_points = user.get('points', 0)
    if current_points < price:
        raise _PurchaseRejected({
            'error': 'Insufficient points',
            'required': price,
            'current': current_points
        })

    now = datetime.utcnow()
    remaining_points = current_points - price

    transaction.update(user_ref, {
        'points': remaining_points,
        'updated_at': now
    })

    # Record points transaction
    transaction.set(db.collection('points_transactions').document(str(uuid.uuid4())), {
        'user_id': user_id,
        'amount': -price,
        'type': 'icon_purchase',
        'reference_id': icon_id,
        'created_at': now
    })

    # Add icon to user's collection（既に存在する場合はコミットが AlreadyExists で失敗する）
    transaction.create(owned_ref, {
        'user_id': user_id,
        'icon_id': icon_id,
        'acquired_at': now
    })

    if is_limited:
        # 限定アイコンは、別ユーザーによる同時購入も直列化して在庫数を守るため、アイコン側も更新する
        transaction.update(icon_ref, {'purchased_count': purchased_count + 1, 'updated_at': now})

    return {
        'status': 'purchased',
        'icon': {
            'id': icon_id,
            'name': icon['name']
        },
        'remaining_points': remaining_points,
        'acquired_at': now.isoformat()
    }

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
