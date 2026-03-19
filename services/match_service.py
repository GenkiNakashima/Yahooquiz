# services/match_service.py
from datetime import datetime
from db import get_db_connection
from psycopg2.extras import Json
import logging

logger = logging.getLogger(__name__)

POINTS_PER_WIN = 100

def create_match(match_type, players, metadata=None):
    """
    Create a match and record results.
    players: list of {user_id, score, is_winner}
    Returns: (success, data_or_error)
    """
    if not players or len(players) < 1:
        return False, {'error': 'At least one player required'}

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Start transaction
                conn.autocommit = False

                # Create match
                cur.execute("""
                    INSERT INTO matches (match_type, metadata, finished_at)
                    VALUES (%s, %s, %s)
                    RETURNING id, created_at
                """, (match_type, Json(metadata) if metadata else None, datetime.utcnow()))

                match = cur.fetchone()
                match_id = match['id']

                # Process each player
                for player in players:
                    user_id = player.get('user_id')
                    score = player.get('score', 0)
                    is_winner = player.get('is_winner', False)

                    if not user_id:
                        conn.rollback()
                        return False, {'error': 'user_id required for all players'}

                    # Insert match player record
                    cur.execute("""
                        INSERT INTO match_players (match_id, user_id, score, is_winner)
                        VALUES (%s, %s, %s, %s)
                    """, (match_id, user_id, score, is_winner))

                    # Award points to winner
                    if is_winner:
                        # Lock user row for update
                        cur.execute("""
                            SELECT points FROM users WHERE id = %s FOR UPDATE
                        """, (user_id,))

                        user = cur.fetchone()
                        if not user:
                            conn.rollback()
                            return False, {'error': f'User {user_id} not found'}

                        # Add points transaction
                        cur.execute("""
                            INSERT INTO points_transactions (user_id, amount, type, reference_id)
                            VALUES (%s, %s, %s, %s)
                        """, (user_id, POINTS_PER_WIN, 'match_win', match_id))

                        # Update user points
                        cur.execute("""
                            UPDATE users
                            SET points = points + %s, updated_at = now()
                            WHERE id = %s
                        """, (POINTS_PER_WIN, user_id))

                # Commit transaction
                conn.commit()

                return True, {
                    'match_id': str(match_id),
                    'created_at': match['created_at'].isoformat(),
                    'points_awarded': POINTS_PER_WIN
                }

    except Exception as e:
        logger.error(f"Error creating match: {e}")
        return False, {'error': 'Failed to create match'}

def get_user_match_history(user_id, limit=10):
    """Get match history for a user"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        m.id,
                        m.match_type,
                        m.created_at,
                        mp.score,
                        mp.is_winner
                    FROM matches m
                    JOIN match_players mp ON m.id = mp.match_id
                    WHERE mp.user_id = %s
                    ORDER BY m.created_at DESC
                    LIMIT %s
                """, (user_id, limit))

                matches = cur.fetchall()

                return [
                    {
                        'match_id': str(match['id']),
                        'match_type': match['match_type'],
                        'created_at': match['created_at'].isoformat(),
                        'score': match['score'],
                        'is_winner': match['is_winner']
                    }
                    for match in matches
                ]

    except Exception as e:
        logger.error(f"Error getting match history: {e}")
        return []

def get_leaderboard(limit=100):
    """Get leaderboard of top players by points"""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        u.id,
                        u.display_name,
                        u.points,
                        COUNT(DISTINCT mp.match_id) as total_matches,
                        COUNT(DISTINCT CASE WHEN mp.is_winner THEN mp.match_id END) as wins
                    FROM users u
                    LEFT JOIN match_players mp ON u.id = mp.user_id
                    GROUP BY u.id, u.display_name, u.points
                    HAVING u.points > 0
                    ORDER BY u.points DESC, wins DESC
                    LIMIT %s
                """, (limit,))

                leaderboard = cur.fetchall()

                return [
                    {
                        'rank': idx + 1,
                        'user_id': str(player['id']),
                        'display_name': player['display_name'] or 'Anonymous',
                        'points': player['points'],
                        'total_matches': player['total_matches'],
                        'wins': player['wins'],
                        'win_rate': round(player['wins'] / player['total_matches'] * 100, 1) if player['total_matches'] > 0 else 0
                    }
                    for idx, player in enumerate(leaderboard)
                ]

    except Exception as e:
        logger.error(f"Error getting leaderboard: {e}")
        return []
