# services/match_service.py
import uuid
from datetime import datetime
from db import get_db
from firebase_admin import firestore as fs
import logging

logger = logging.getLogger(__name__)

# 同じドキュメントへの同時更新でトランザクションが競合したときの最大試行回数（ライブラリの既定は5回）。
# 競合して再試行するトランザクションは順番待ちの位置を保つため、同時アクセス数ぶんの回数があれば成功する。
TRANSACTION_MAX_ATTEMPTS = 10

POINTS_PER_WIN = 100

def _normalize_dt(dt):
    if dt is None:
        return None
    if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt

class _MatchRejected(Exception):
    """対戦結果を登録できない。トランザクションを中断し、書き込みは行わない"""

    def __init__(self, payload):
        super().__init__(payload.get('error'))
        self.payload = payload


def create_match(match_type, players, metadata=None):
    """
    Create a match and record results.
    players: list of {user_id, score, is_winner}
    matches / match_players の作成、勝者へのポイント付与と points_transactions の記録、
    各ユーザーの対戦数・勝利数の更新を1つのトランザクションで行う（途中で失敗したら何も書き込まない）。
    Returns: (success, data_or_error)
    """
    if not players or len(players) < 1:
        return False, {'error': 'At least one player required'}

    user_ids = [player.get('user_id') for player in players]
    if not all(user_ids):
        return False, {'error': 'user_id required for all players'}
    if len(set(user_ids)) != len(user_ids):
        return False, {'error': 'Duplicate user_id in players'}

    try:
        db = get_db()
        return True, _create_match_in_transaction(db.transaction(max_attempts=TRANSACTION_MAX_ATTEMPTS), db, match_type, players, metadata)
    except _MatchRejected as e:
        return False, e.payload
    except Exception as e:
        logger.error(f"Error creating match: {e}")
        return False, {'error': 'Failed to create match'}


@fs.transactional
def _create_match_in_transaction(transaction, db, match_type, players, metadata):
    # Firestore のトランザクションでは、読み取りをすべて書き込みより先に行う必要がある
    user_refs = [db.collection('users').document(player['user_id']) for player in players]
    for user_ref in user_refs:
        if not user_ref.get(transaction=transaction).exists:
            raise _MatchRejected({'error': f'User {user_ref.id} not found'})

    match_id = str(uuid.uuid4())
    now = datetime.utcnow()

    # Create match document
    transaction.set(db.collection('matches').document(match_id), {
        'match_type': match_type,
        'metadata': metadata,
        'finished_at': now,
        'created_at': now
    })

    points_awarded = 0
    for player, user_ref in zip(players, user_refs):
        is_winner = bool(player.get('is_winner', False))

        # Create match_player record
        transaction.set(db.collection('match_players').document(str(uuid.uuid4())), {
            'match_id': match_id,
            'user_id': user_ref.id,
            'score': player.get('score', 0),
            'is_winner': is_winner,
            'created_at': now
        })

        update_data = {
            'total_matches': fs.Increment(1),
            'updated_at': now
        }
        if is_winner:
            update_data['wins'] = fs.Increment(1)
            update_data['points'] = fs.Increment(POINTS_PER_WIN)
            points_awarded = POINTS_PER_WIN

            # Record points transaction
            transaction.set(db.collection('points_transactions').document(str(uuid.uuid4())), {
                'user_id': user_ref.id,
                'amount': POINTS_PER_WIN,
                'type': 'match_win',
                'reference_id': match_id,
                'created_at': now
            })

        transaction.update(user_ref, update_data)

    return {
        'match_id': match_id,
        'created_at': now.isoformat(),
        # 勝者1人あたりに付与したポイント（勝者がいなければ 0）
        'points_awarded': points_awarded
    }

def get_user_match_history(user_id, limit=10):
    """Get match history for a user"""
    try:
        db = get_db()
        mp_docs = list(db.collection('match_players').where('user_id', '==', user_id).stream())

        results = []
        for mp_doc in mp_docs:
            mp = mp_doc.to_dict()
            match_doc = db.collection('matches').document(mp['match_id']).get()
            if match_doc.exists:
                match = match_doc.to_dict()
                created_at = _normalize_dt(match.get('created_at'))
                results.append({
                    'match_id': mp['match_id'],
                    'match_type': match.get('match_type'),
                    'created_at': created_at.isoformat() if created_at else None,
                    'score': mp.get('score', 0),
                    'is_winner': mp.get('is_winner', False)
                })

        # Sort by created_at descending and apply limit
        results.sort(key=lambda x: x['created_at'] or '', reverse=True)
        return results[:limit]

    except Exception as e:
        logger.error(f"Error getting match history: {e}")
        return []

def get_leaderboard(limit=100):
    """Get leaderboard of top players by points"""
    try:
        db = get_db()
        users = list(
            db.collection('users')
            .where('points', '>', 0)
            .order_by('points', direction=fs.Query.DESCENDING)
            .limit(limit)
            .stream()
        )

        result = []
        for idx, doc in enumerate(users):
            data = doc.to_dict()
            total_matches = data.get('total_matches', 0)
            wins = data.get('wins', 0)
            result.append({
                'rank': idx + 1,
                'user_id': doc.id,
                'display_name': data.get('display_name') or 'Anonymous',
                'points': data.get('points', 0),
                'total_matches': total_matches,
                'wins': wins,
                'win_rate': round(wins / total_matches * 100, 1) if total_matches > 0 else 0
            })
        return result

    except Exception as e:
        logger.error(f"Error getting leaderboard: {e}")
        return []
