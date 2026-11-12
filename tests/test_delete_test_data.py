"""scripts/delete_test_data.py のテスト（Firestore エミュレータ上で実行。未設定ならスキップ）"""
import uuid

import pytest

from scripts.delete_test_data import TARGET_COLLECTIONS, delete_test_data
from scripts.migrate_user_icons import migrate


def _seed(db):
    for name in TARGET_COLLECTIONS:
        for _ in range(2):
            db.collection(name).document(str(uuid.uuid4())).set({'test': True})
    limited = str(uuid.uuid4())
    db.collection('icons').document(limited).set(
        {'name': 'l', 'price': 100, 'is_limited': True, 'total_supply': 5, 'purchased_count': 2})
    db.collection('icons').document(str(uuid.uuid4())).set({'name': 'n', 'price': 100, 'is_limited': False})
    # サブコレクションも削除対象（親ドキュメントの無いサブコレクションも含む。件数には数えない）
    db.collection('users').document('with-sub').collection('history').document('h1').set({'x': 1})
    return limited


def _count(db, name):
    return len(list(db.collection(name).stream()))


def test_dry_run_deletes_nothing(firestore_emulator):
    db = firestore_emulator
    _seed(db)

    stats = delete_test_data(db, apply=False)

    assert stats == {name: 2 for name in TARGET_COLLECTIONS}
    assert all(_count(db, name) for name in TARGET_COLLECTIONS)


def test_apply_deletes_targets_but_never_icons(firestore_emulator):
    db = firestore_emulator
    _seed(db)

    delete_test_data(db, apply=True)

    assert all(_count(db, name) == 0 for name in TARGET_COLLECTIONS)
    assert list(db.collection('users').document('with-sub').collection('history').stream()) == []
    assert _count(db, 'icons') == 2


def test_icons_cannot_be_targeted(firestore_emulator):
    db = firestore_emulator
    _seed(db)

    with pytest.raises(ValueError):
        delete_test_data(db, apply=True, collections=('users', 'icons'))

    assert _count(db, 'icons') == 2
    assert _count(db, 'users') == 2   # 拒否した場合は何も削除しない


def test_delete_then_migrate_resets_purchased_count(firestore_emulator):
    """実行の順番（削除 → 移行）で、限定アイコンの purchased_count が 0 になる"""
    db = firestore_emulator
    limited = _seed(db)

    delete_test_data(db, apply=True)
    stats = migrate(db, apply=True)

    assert stats['user_icons_total'] == 0
    assert db.collection('icons').document(limited).get().to_dict()['purchased_count'] == 0
