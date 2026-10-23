# services/match_service.py
import uuid
from datetime import datetime
from db import get_db
from firebase_admin import firestore as fs
import logging

logger = logging.getLogger(__name__)

POINTS_PER_WIN = 100

def _normalize_dt(dt):
    if dt is None:
        return None
    if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt

def create_match(match_type, players, metadata=None):
    """
    Create a match and record results.
    players: list of {user_id, score, is_winner}
    Returns: (success, data_or_error)
    """
    if not players or len(players) < 1:
        return False, {'error': 'At least one player required'}

    try:
        db = get_db()
        match_id = str(uuid.uuid4())
        now = datetime.utcnow()

        # Create match document
        db.collection('matches').document(match_id).set({
            'match_type': match_type,
            'metadata': metadata,
            'finished_at': now,
            'created_at': now
        })

        # Process each player
        for player in players:
            user_id = player.get('user_id')
            score = player.get('score', 0)
            is_winner = player.get('is_winner', False)

            if not user_id:
                return False, {'error': 'user_id required for all players'}

            # Create match_player record
            mp_id = str(uuid.uuid4())
            db.collection('match_players').document(mp_id).set({
                'match_id': match_id,
                'user_id': user_id,
                'score': score,
                'is_winner': is_winner,
                'created_at': now
            })

            user_ref = db.collection('users').document(user_id)
            user_doc = user_ref.get()
            if not user_doc.exists:
                return False, {'error': f'User {user_id} not found'}

            # Increment total_matches and wins using atomic increment
            update_data = {
                'total_matches': fs.Increment(1),
                'updated_at': now
            }
            if is_winner:
                update_data['wins'] = fs.Increment(1)
                update_data['points'] = fs.Increment(POINTS_PER_WIN)

                # Record points transaction
                pt_id = str(uuid.uuid4())
                db.collection('points_transactions').document(pt_id).set({
                    'user_id': user_id,
                    'amount': POINTS_PER_WIN,
                    'type': 'match_win',
                    'reference_id': match_id,
                    'created_at': now
                })

            user_ref.update(update_data)

        return True, {
            'match_id': match_id,
            'created_at': now.isoformat(),
            'points_awarded': POINTS_PER_WIN
        }

    except Exception as e:
        logger.error(f"Error creating match: {e}")
        return False, {'error': 'Failed to create match'}

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

        return [
            {
                'rank': idx + 1,
                'user_id': doc.id,
                'display_name': doc.get('display_name') or 'Anonymous',
                'points': doc.get('points', 0),
                'total_matches': doc.get('total_matches', 0),
                'wins': doc.get('wins', 0),
                'win_rate': round(
                    doc.get('wins', 0) / doc.get('total_matches', 1) * 100, 1
                ) if doc.get('total_matches', 0) > 0 else 0
            }
            for idx, doc in enumerate(users)
        ]

    except Exception as e:
        logger.error(f"Error getting leaderboard: {e}")
        return []
