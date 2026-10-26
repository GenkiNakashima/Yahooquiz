"""
回答結果のサーバー側記録（quiz_set_service.record_answer）と、
ゲーム結果の確定・記録（match_service.record_game_result）のテスト。
Firestore エミュレータ上で実行する（FIRESTORE_EMULATOR_HOST 未設定ならスキップ）。
"""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from services import match_service, quiz_set_service
from services.quiz_set_service import AI_CORRECT, AI_WRONG, PLAYER_CORRECT, PLAYER_WRONG, QuizSetNotFound


@pytest.fixture
def db(firestore_emulator, monkeypatch):
    monkeypatch.setattr(quiz_set_service, 'get_db', lambda: firestore_emulator)
    monkeypatch.setattr(match_service, 'get_db', lambda: firestore_emulator)
    return firestore_emulator


def _user(db, points=0):
    user_id = str(uuid.uuid4())
    db.collection('users').document(user_id).set({'email': f'{user_id}@example.com', 'points': points})
    return user_id


def _game(rounds=5):
    quizzes = [{'question': f'問題{i}', 'answer': f'正解{i}'} for i in range(rounds)]
    return quiz_set_service.save_quiz_set(quizzes, metadata={'ai_level': 'normal', 'genre': 'it'})


def _play(quiz_set_id, results):
    for i, result in enumerate(results):
        quiz_set_service.record_answer(quiz_set_id, i, result)


def _count(db, collection):
    return len(list(db.collection(collection).stream()))


def _points(db, user_id):
    return db.collection('users').document(user_id).get().to_dict().get('points')


# ---------------------------------------------------------------------------
# 回答結果の記録
# ---------------------------------------------------------------------------

def test_first_answer_of_a_round_wins(db):
    quiz_set_id = _game()

    first = quiz_set_service.record_answer(quiz_set_id, 0, PLAYER_WRONG)
    second = quiz_set_service.record_answer(quiz_set_id, 0, PLAYER_CORRECT)

    assert first == (PLAYER_WRONG, {'0': PLAYER_WRONG})
    assert second == (PLAYER_WRONG, {'0': PLAYER_WRONG})


def test_concurrent_answers_to_the_same_round_keep_one_result(db):
    quiz_set_id = _game()
    barrier = threading.Barrier(4)

    def answer(result):
        barrier.wait()
        try:
            return quiz_set_service.record_answer(quiz_set_id, 0, result)[0]
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=4) as executor:
        adopted = list(executor.map(answer, [PLAYER_CORRECT, PLAYER_WRONG, PLAYER_CORRECT, PLAYER_WRONG]))

    stored = db.collection('quiz_sets').document(quiz_set_id).get().to_dict()['results']
    assert list(stored) == ['0']
    assert {a for a in adopted if a is not None} == {stored['0']}


@pytest.mark.parametrize('index, result', [(5, PLAYER_CORRECT), (-1, PLAYER_CORRECT), (0, 'cheat')])
def test_invalid_round_or_result_is_rejected(db, index, result):
    quiz_set_id = _game()

    with pytest.raises(ValueError):
        quiz_set_service.record_answer(quiz_set_id, index, result)


def test_answer_to_unknown_or_expired_game_is_rejected(db):
    with pytest.raises(QuizSetNotFound):
        quiz_set_service.record_answer('no-such-game', 0, PLAYER_CORRECT)

    quiz_set_id = _game()
    db.collection('quiz_sets').document(quiz_set_id).update(
        {'expires_at': datetime.now(timezone.utc) - timedelta(minutes=1)})
    with pytest.raises(QuizSetNotFound):
        quiz_set_service.record_answer(quiz_set_id, 0, PLAYER_CORRECT)


# ---------------------------------------------------------------------------
# ゲーム結果の確定・記録
# ---------------------------------------------------------------------------

def test_winning_game_is_recorded_from_server_side_results(db):
    user_id = _user(db)
    quiz_set_id = _game()
    _play(quiz_set_id, [PLAYER_CORRECT, PLAYER_CORRECT, AI_CORRECT, PLAYER_WRONG, AI_WRONG])

    ok, result, status = match_service.record_game_result(user_id, quiz_set_id)

    assert ok and status == 201
    assert (result['is_winner'], result['player_score'], result['ai_score'], result['points_awarded']) == (True, 2, 1, 100)
    assert _points(db, user_id) == 100
    match = db.collection('matches').document(result['match_id']).get().to_dict()
    assert match['match_type'] == '1v1_ai'
    assert match['metadata'] == {'ai_level': 'normal', 'genre': 'it', 'ai_score': 1, 'quiz_set_id': quiz_set_id}
    (mp,) = [d.to_dict() for d in db.collection('match_players').stream()]
    assert (mp['user_id'], mp['score'], mp['is_winner']) == (user_id, 2, True)
    assert db.collection('quiz_sets').document(quiz_set_id).get().to_dict()['match_id'] == result['match_id']


@pytest.mark.parametrize('results', [
    [AI_CORRECT, AI_CORRECT, PLAYER_CORRECT, PLAYER_WRONG, AI_WRONG],   # 負け
    [PLAYER_CORRECT, AI_CORRECT, PLAYER_WRONG, AI_WRONG, AI_WRONG],     # 引き分け
])
def test_losing_or_drawn_game_awards_no_points(db, results):
    user_id = _user(db)
    quiz_set_id = _game()
    _play(quiz_set_id, results)

    ok, result, status = match_service.record_game_result(user_id, quiz_set_id)

    assert ok and status == 201
    assert result['is_winner'] is False and result['points_awarded'] == 0
    assert _points(db, user_id) == 0
    assert _count(db, 'points_transactions') == 0


def test_game_can_be_recorded_only_once(db):
    user_id, other_user = _user(db), _user(db)
    quiz_set_id = _game()
    _play(quiz_set_id, [PLAYER_CORRECT] * 5)

    first = match_service.record_game_result(user_id, quiz_set_id)
    again = match_service.record_game_result(user_id, quiz_set_id)
    by_other = match_service.record_game_result(other_user, quiz_set_id)

    assert first[0] is True
    assert again[0] is False and again[2] == 409 and again[1]['match_id'] == first[1]['match_id']
    assert by_other[0] is False and by_other[2] == 409
    assert _points(db, user_id) == 100 and _points(db, other_user) == 0
    assert _count(db, 'matches') == 1 and _count(db, 'points_transactions') == 1


def test_concurrent_recording_of_the_same_game_awards_points_once(db):
    user_id = _user(db)
    quiz_set_id = _game()
    _play(quiz_set_id, [PLAYER_CORRECT] * 5)
    barrier = threading.Barrier(5)

    def record(_):
        barrier.wait()
        return match_service.record_game_result(user_id, quiz_set_id)

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(record, range(5)))

    assert sum(ok for ok, _, _ in results) == 1
    assert _points(db, user_id) == 100
    assert _count(db, 'matches') == 1 and _count(db, 'points_transactions') == 1


def test_unfinished_game_is_not_recorded(db):
    user_id = _user(db)
    quiz_set_id = _game()
    _play(quiz_set_id, [PLAYER_CORRECT] * 4)

    ok, result, status = match_service.record_game_result(user_id, quiz_set_id)

    assert not ok and status == 400 and result['error'] == 'Game is not finished'
    assert _count(db, 'matches') == 0 and _points(db, user_id) == 0
    assert 'match_id' not in db.collection('quiz_sets').document(quiz_set_id).get().to_dict()


def test_unknown_or_expired_game_is_not_recorded(db):
    user_id = _user(db)
    assert match_service.record_game_result(user_id, 'no-such-game')[2] == 404

    quiz_set_id = _game()
    _play(quiz_set_id, [PLAYER_CORRECT] * 5)
    db.collection('quiz_sets').document(quiz_set_id).update(
        {'expires_at': datetime.now(timezone.utc) - timedelta(minutes=1)})

    ok, _, status = match_service.record_game_result(user_id, quiz_set_id)

    assert not ok and status == 404
    assert _count(db, 'matches') == 0


def test_unknown_user_is_not_recorded(db):
    quiz_set_id = _game()
    _play(quiz_set_id, [PLAYER_CORRECT] * 5)

    ok, _, status = match_service.record_game_result('no-such-user', quiz_set_id)

    assert not ok and status == 404
    assert _count(db, 'matches') == 0
    assert 'match_id' not in db.collection('quiz_sets').document(quiz_set_id).get().to_dict()
