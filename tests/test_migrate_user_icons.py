"""scripts/migrate_user_icons.py のテスト（Firestore エミュレータ上で実行。未設定ならスキップ）"""
import uuid

from scripts.migrate_user_icons import migrate


def _ids(db, collection):
    return {d.id for d in db.collection(collection).stream()}


def _seed(db):
    """旧形式（ランダムな ID）の所持データと、purchased_count の無い限定アイコン"""
    limited, normal = str(uuid.uuid4()), str(uuid.uuid4())
    db.collection('icons').document(limited).set({'name': 'l', 'price': 100, 'is_limited': True, 'total_supply': 5})
    db.collection('icons').document(normal).set({'name': 'n', 'price': 100, 'is_limited': False, 'total_supply': None})
    users = ['u1', 'u2', 'u3']
    old = {}
    for user, icon in [('u1', limited), ('u2', limited), ('u1', normal)]:
        doc_id = str(uuid.uuid4())
        db.collection('user_icons').document(doc_id).set({'user_id': user, 'icon_id': icon, 'acquired_at': 'ts'})
        old[doc_id] = (user, icon)
    # 移行済みの形式のデータも混ざっている
    db.collection('user_icons').document(f'u3_{limited}').set({'user_id': 'u3', 'icon_id': limited})
    return limited, normal, users, old


def test_dry_run_writes_nothing(firestore_emulator):
    db = firestore_emulator
    limited, _, _, old = _seed(db)
    before = _ids(db, 'user_icons')

    stats = migrate(db, apply=False)

    assert stats['user_icons_moved'] == 3
    assert stats['user_icons_already_migrated'] == 1
    assert stats['purchased_count_updated'] == 1
    assert _ids(db, 'user_icons') == before
    assert 'purchased_count' not in db.collection('icons').document(limited).get().to_dict()


def test_apply_moves_documents_and_sets_purchased_count(firestore_emulator):
    db = firestore_emulator
    limited, normal, _, old = _seed(db)

    stats = migrate(db, apply=True)

    assert stats['user_icons_moved'] == 3
    expected = {f'u1_{limited}', f'u2_{limited}', f'u1_{normal}', f'u3_{limited}'}
    assert _ids(db, 'user_icons') == expected
    # 中身はそのまま
    moved = db.collection('user_icons').document(f'u1_{limited}').get().to_dict()
    assert moved == {'user_id': 'u1', 'icon_id': limited, 'acquired_at': 'ts'}
    assert db.collection('icons').document(limited).get().to_dict()['purchased_count'] == 3
    assert 'purchased_count' not in db.collection('icons').document(normal).get().to_dict()


def test_apply_is_idempotent(firestore_emulator):
    db = firestore_emulator
    limited, _, _, _ = _seed(db)
    migrate(db, apply=True)
    after_first = _ids(db, 'user_icons')

    stats = migrate(db, apply=True)

    assert stats['user_icons_moved'] == 0
    assert stats['purchased_count_updated'] == 0
    assert stats['user_icons_already_migrated'] == 4
    assert _ids(db, 'user_icons') == after_first
    assert db.collection('icons').document(limited).get().to_dict()['purchased_count'] == 3


def test_duplicate_purchase_is_collapsed(firestore_emulator):
    db = firestore_emulator
    icon = str(uuid.uuid4())
    db.collection('icons').document(icon).set({'name': 'l', 'price': 1, 'is_limited': True, 'total_supply': 9})
    for _ in range(2):
        db.collection('user_icons').document(str(uuid.uuid4())).set({'user_id': 'u1', 'icon_id': icon})

    stats = migrate(db, apply=True)

    assert stats['user_icons_moved'] == 1
    assert stats['user_icons_duplicates_removed'] == 1
    assert _ids(db, 'user_icons') == {f'u1_{icon}'}
    assert db.collection('icons').document(icon).get().to_dict()['purchased_count'] == 1


def test_documents_without_ids_are_left_alone(firestore_emulator):
    db = firestore_emulator
    db.collection('user_icons').document('broken').set({'user_id': 'u1'})

    stats = migrate(db, apply=True)

    assert stats['user_icons_skipped_invalid'] == 1
    assert _ids(db, 'user_icons') == {'broken'}
