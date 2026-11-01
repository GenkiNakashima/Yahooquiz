# services/match_service.py
import uuid
from datetime import datetime
from db import get_db
from services import quiz_set_service
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

    def __init__(self, payload, status=400):
        super().__init__(payload.get('error'))
        self.payload = payload
        self.status = status


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

    return _write_match(transaction, db, match_type, list(zip(players, user_refs)), metadata)


def _write_match(transaction, db, match_type, players_with_refs, metadata):
    """
    matches / match_players の作成、勝者へのポイント付与と points_transactions の記録、
    各ユーザーの対戦数・勝利数の更新をトランザクションに書き込む（読み取りは呼び出し側で済ませておく）
    """
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
    for player, user_ref in players_with_refs:
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


def record_game_result(user_id, quiz_set_id):
    """
    遊び終えたゲーム（quiz_sets）の結果を、user_id の AI 対戦結果として記録する。
    勝敗・スコアはサーバー側に記録した各ラウンドの回答結果から確定する（クライアントの申告は使わない）。
    記録と「記録済み」の印付けを同じトランザクションで行うため、1ゲームにつき1回しか記録できない。
    Returns: (success, data_or_error, http_status)
    """
    try:
        db = get_db()
        result = _record_game_in_transaction(
            db.transaction(max_attempts=TRANSACTION_MAX_ATTEMPTS), db, user_id, quiz_set_id)
        return True, result, 201
    except _MatchRejected as e:
        return False, e.payload, e.status
    except Exception as e:
        logger.error(f"Error recording game result: {e}")
        return False, {'error': 'Failed to create match'}, 500


@fs.transactional
def _record_game_in_transaction(transaction, db, user_id, quiz_set_id):
    # Firestore のトランザクションでは、読み取りをすべて書き込みより先に行う必要がある
    quiz_set_ref = db.collection(quiz_set_service.COLLECTION).document(quiz_set_id)
    quiz_set_doc = quiz_set_ref.get(transaction=transaction)
    if not quiz_set_doc.exists:
        raise _MatchRejected({'error': 'Game not found'}, 404)
    quiz_set = quiz_set_doc.to_dict() or {}
    if quiz_set_service.is_expired(quiz_set):
        raise _MatchRejected({'error': 'Game not found'}, 404)
    if quiz_set.get('match_id'):
        raise _MatchRejected({'error': 'This game has already been recorded',
                              'match_id': quiz_set['match_id']}, 409)

    results = quiz_set.get('results') or {}
    total_rounds = len(quiz_set.get('quizzes') or [])
    if total_rounds == 0 or len(results) < total_rounds:
        raise _MatchRejected({'error': 'Game is not finished'}, 400)

    user_ref = db.collection('users').document(user_id)
    if not user_ref.get(transaction=transaction).exists:
        raise _MatchRejected({'error': 'User not found'}, 404)

    score = quiz_set_service.score_from_results(results)
    is_winner = score['player'] > score['ai']
    metadata = {**(quiz_set.get('metadata') or {}), 'ai_score': score['ai'], 'quiz_set_id': quiz_set_id}

    result = _write_match(transaction, db, '1v1_ai',
                          [({'score': score['player'], 'is_winner': is_winner}, user_ref)], metadata)
    transaction.update(quiz_set_ref, {'match_id': result['match_id'], 'recorded_by': user_id})

    return {
        **result,
        'is_winner': is_winner,
        'player_score': score['player'],
        'ai_score': score['ai'],
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
