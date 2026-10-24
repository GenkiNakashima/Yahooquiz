"""
ポイントを動かす処理（対戦結果の登録・アイコン購入）の整合性テスト。
Firestore エミュレータ上で実行する（FIRESTORE_EMULATOR_HOST 未設定ならスキップ）。

並列リクエストでも、残高のマイナス・二重購入・限定数の超過・ポイントと履歴の不一致が
起きないこと、途中で失敗したときに半端なデータが残らないことを確認する。

並列テストについて: エミュレータは同じドキュメントのロック待ちを約2秒で打ち切る
（"Transaction lock timeout"）ため、本番より競合時の失敗が起きやすい。そのため
「何件成功したか」ではなく「どの結果でも整合性が崩れないこと」を主に確認する。
"""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from services import icon_service, match_service


@pytest.fixture
def db(firestore_emulator, monkeypatch):
    monkeypatch.setattr(icon_service, 'get_db', lambda: firestore_emulator)
    monkeypatch.setattr(match_service, 'get_db', lambda: firestore_emulator)
    return firestore_emulator


def _user(db, points=0, **extra):
    user_id = str(uuid.uuid4())
    db.collection('users').document(user_id).set({
        'email': f'{user_id}@example.com', 'display_name': 'tester', 'points': points, **extra,
    })
    return user_id


def _icon(db, price, is_limited=False, total_supply=None):
    icon_id = str(uuid.uuid4())
    db.collection('icons').document(icon_id).set({
        'name': f'icon-{icon_id[:8]}', 'description': 'テスト', 'price': price,
        'is_limited': is_limited, 'total_supply': total_supply,
    })
    return icon_id


def _points(db, user_id):
    return db.collection('users').document(user_id).get().to_dict().get('points')


def _count(db, collection, **filters):
    query = db.collection(collection)
    for field, value in filters.items():
        query = query.where(field, '==', value)
    return len(list(query.stream()))


def _run_in_parallel(calls):
    """calls を同時に開始して実行し、結果のリストを返す"""
    barrier = threading.Barrier(len(calls))

    def run(call):
        barrier.wait()
        return call()

    with ThreadPoolExecutor(max_workers=len(calls)) as executor:
        return list(executor.map(run, calls))


# ---------------------------------------------------------------------------
# アイコン購入
# ---------------------------------------------------------------------------

def test_purchase_success_updates_everything_together(db):
    user_id = _user(db, points=1000)
    icon_id = _icon(db, price=300)

    ok, result = icon_service.purchase_icon(user_id, icon_id)

    assert ok and result['remaining_points'] == 700
    assert _points(db, user_id) == 700
    assert _count(db, 'user_icons', user_id=user_id, icon_id=icon_id) == 1
    (tx,) = [d.to_dict() for d in db.collection('points_transactions').stream()]
    assert tx['user_id'] == user_id and tx['amount'] == -300
    assert tx['type'] == 'icon_purchase' and tx['reference_id'] == icon_id


@pytest.mark.parametrize('setup, expected_error', [
    ('insufficient', 'Insufficient points'),
    ('owned', 'You already own this icon'),
    ('sold_out', 'This limited icon is sold out'),
    ('no_user', 'User not found'),
    ('no_icon', 'Icon not found'),
])
def test_purchase_rejections_write_nothing(db, setup, expected_error):
    user_id = _user(db, points=50 if setup == 'insufficient' else 1000)
    icon_id = _icon(db, price=100, is_limited=(setup == 'sold_out'), total_supply=1)
    if setup == 'owned':
        db.collection('user_icons').document(str(uuid.uuid4())).set({'user_id': user_id, 'icon_id': icon_id})
    if setup == 'sold_out':
        db.collection('user_icons').document(str(uuid.uuid4())).set({'user_id': 'someone', 'icon_id': icon_id})
    if setup == 'no_user':
        user_id = 'no-such-user'
    if setup == 'no_icon':
        icon_id = 'no-such-icon'
    before_icons = _count(db, 'user_icons')

    ok, result = icon_service.purchase_icon(user_id, icon_id)

    assert not ok and result['error'] == expected_error
    assert _count(db, 'user_icons') == before_icons
    assert _count(db, 'points_transactions') == 0
    if setup != 'no_user':
        assert _points(db, user_id) == (50 if setup == 'insufficient' else 1000)


def test_concurrent_purchases_of_same_icon_succeed_only_once(db):
    user_id = _user(db, points=10000)
    icon_id = _icon(db, price=100)

    results = _run_in_parallel([lambda: icon_service.purchase_icon(user_id, icon_id)] * 5)

    assert sum(ok for ok, _ in results) == 1
    assert _count(db, 'user_icons', user_id=user_id, icon_id=icon_id) == 1
    assert _points(db, user_id) == 9900
    assert _count(db, 'points_transactions', user_id=user_id) == 1


def test_concurrent_purchases_cannot_overdraw_points(db):
    user_id = _user(db, points=1000)
    icon_ids = [_icon(db, price=600) for _ in range(4)]

    results = _run_in_parallel([lambda i=i: icon_service.purchase_icon(user_id, i) for i in icon_ids])

    # 1000pt で 600pt のアイコンは1つしか買えない
    assert sum(ok for ok, _ in results) == 1
    assert _points(db, user_id) == 400
    assert _count(db, 'user_icons', user_id=user_id) == 1
    assert _count(db, 'points_transactions', user_id=user_id) == 1


def test_limited_icon_cannot_be_oversold(db):
    icon_id = _icon(db, price=100, is_limited=True, total_supply=2)
    user_ids = [_user(db, points=1000) for _ in range(5)]

    results = _run_in_parallel([lambda u=u: icon_service.purchase_icon(u, icon_id) for u in user_ids])

    sold = sum(ok for ok, _ in results)
    assert 1 <= sold <= 2
    assert _count(db, 'user_icons', icon_id=icon_id) == sold
    assert sum(_points(db, u) for u in user_ids) == 5 * 1000 - sold * 100
    assert _count(db, 'points_transactions') == sold


def test_limited_icon_sells_exactly_up_to_supply(db):
    icon_id = _icon(db, price=100, is_limited=True, total_supply=2)
    user_ids = [_user(db, points=1000) for _ in range(3)]

    results = [icon_service.purchase_icon(u, icon_id) for u in user_ids]

    assert [ok for ok, _ in results] == [True, True, False]
    assert results[2][1]['error'] == 'This limited icon is sold out'
    assert db.collection('icons').document(icon_id).get().to_dict()['purchased_count'] == 2


def test_concurrent_purchases_of_unlimited_icon_by_different_users(db):
    """通常のアイコンは、別ユーザーどうしの購入が互いを待たない（アイコン側を書き換えない）"""
    icon_id = _icon(db, price=100)
    user_ids = [_user(db, points=1000) for _ in range(5)]

    results = _run_in_parallel([lambda u=u: icon_service.purchase_icon(u, icon_id) for u in user_ids])

    assert all(ok for ok, _ in results)
    assert all(_points(db, u) == 900 for u in user_ids)
    assert 'purchased_count' not in db.collection('icons').document(icon_id).get().to_dict()


def test_existing_purchases_count_toward_limited_supply(db):
    """在庫数のカウンタが無い既存データでも、既存の購入数を数えて上限を守る"""
    icon_id = _icon(db, price=100, is_limited=True, total_supply=3)
    for _ in range(2):
        db.collection('user_icons').document(str(uuid.uuid4())).set({'user_id': str(uuid.uuid4()), 'icon_id': icon_id})
    first, second = _user(db, points=1000), _user(db, points=1000)

    assert icon_service.purchase_icon(first, icon_id)[0] is True
    ok, result = icon_service.purchase_icon(second, icon_id)

    assert not ok and result['error'] == 'This limited icon is sold out'
    assert _count(db, 'user_icons', icon_id=icon_id) == 3


# ---------------------------------------------------------------------------
# 対戦結果の登録
# ---------------------------------------------------------------------------

def test_match_win_updates_everything_together(db):
    winner, loser = _user(db, points=0), _user(db, points=0)

    ok, result = match_service.create_match('1v1', [
        {'user_id': winner, 'score': 5, 'is_winner': True},
        {'user_id': loser, 'score': 3, 'is_winner': False},
    ])

    assert ok and result['points_awarded'] == match_service.POINTS_PER_WIN
    match_id = result['match_id']
    assert db.collection('matches').document(match_id).get().exists
    assert _count(db, 'match_players', match_id=match_id) == 2
    w = db.collection('users').document(winner).get().to_dict()
    l = db.collection('users').document(loser).get().to_dict()
    assert (w['points'], w['wins'], w['total_matches']) == (100, 1, 1)
    assert (l['points'], l.get('wins', 0), l['total_matches']) == (0, 0, 1)
    (tx,) = [d.to_dict() for d in db.collection('points_transactions').stream()]
    assert (tx['user_id'], tx['amount'], tx['type'], tx['reference_id']) == (winner, 100, 'match_win', match_id)


def test_match_with_unknown_player_writes_nothing(db):
    winner = _user(db, points=0)

    ok, result = match_service.create_match('1v1', [
        {'user_id': winner, 'score': 5, 'is_winner': True},
        {'user_id': 'no-such-user', 'score': 3, 'is_winner': False},
    ])

    assert not ok and 'not found' in result['error']
    assert _count(db, 'matches') == 0
    assert _count(db, 'match_players') == 0
    assert _count(db, 'points_transactions') == 0
    assert _points(db, winner) == 0


def test_match_with_duplicate_player_is_rejected(db):
    user_id = _user(db, points=0)

    ok, result = match_service.create_match('1v1', [
        {'user_id': user_id, 'score': 5, 'is_winner': True},
        {'user_id': user_id, 'score': 5, 'is_winner': True},
    ])

    assert not ok
    assert _count(db, 'matches') == 0
    assert _points(db, user_id) == 0


def test_match_without_winner_awards_no_points(db):
    user_id = _user(db, points=0)

    ok, result = match_service.create_match('1v1_ai', [{'user_id': user_id, 'score': 2, 'is_winner': False}])

    assert ok and result['points_awarded'] == 0
    assert _points(db, user_id) == 0
    assert _count(db, 'points_transactions') == 0


def test_concurrent_wins_keep_points_and_history_consistent(db):
    user_id = _user(db, points=0)
    win = {'user_id': user_id, 'score': 5, 'is_winner': True}

    results = _run_in_parallel([lambda: match_service.create_match('1v1_ai', [dict(win)])] * 5)

    recorded = sum(ok for ok, _ in results)
    assert recorded >= 1
    user = db.collection('users').document(user_id).get().to_dict()
    assert (user['points'], user['wins'], user['total_matches']) == (100 * recorded, recorded, recorded)
    assert _count(db, 'points_transactions', user_id=user_id) == recorded
    assert _count(db, 'matches') == recorded
    assert _count(db, 'match_players') == recorded


def test_concurrent_win_and_purchase_keep_points_consistent(db):
    """勝利のポイント付与と購入のポイント消費が同時に起きても、残高と履歴の合計が一致する"""
    user_id = _user(db, points=500)
    icon_ids = [_icon(db, price=200) for _ in range(3)]
    win = {'user_id': user_id, 'score': 5, 'is_winner': True}

    calls = [lambda i=i: icon_service.purchase_icon(user_id, i) for i in icon_ids]
    calls += [lambda: match_service.create_match('1v1_ai', [dict(win)])] * 3
    _run_in_parallel(calls)

    history = sum(d.to_dict()['amount'] for d in db.collection('points_transactions').stream())
    assert _points(db, user_id) == 500 + history
    assert _points(db, user_id) >= 0
