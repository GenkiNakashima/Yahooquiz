"""
quiz_set_service のテスト。

- 単体テスト: Firestore クライアントをメモリ上の偽物に差し替えて実行する（常に実行）
- 結合テスト: FIRESTORE_EMULATOR_HOST が設定されている場合のみ、Firestore エミュレータに対して実行する
"""
import os
from datetime import datetime, timedelta, timezone

import pytest

from services import quiz_set_service


def _quizzes():
    return [
        {'question': f'問題{i}', 'choices': ['あ', 'い', 'う', 'え'], 'answer_index': i % 4,
         'answer': ['あ', 'い', 'う', 'え'][i % 4], 'explanation': f'解説{i}',
         'article_content': '本文' * 1000, 'article_url': f'https://news.example/{i}',
         'article_title': f'タイトル{i}'}
        for i in range(5)
    ]


# ---------------------------------------------------------------------------
# 単体テスト（偽の Firestore）
# ---------------------------------------------------------------------------

class _FakeSnapshot:
    def __init__(self, data):
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class _FakeDocument:
    def __init__(self, store, doc_id):
        self._store, self._id = store, doc_id

    def set(self, data):
        self._store[self._id] = dict(data)

    def get(self):
        return _FakeSnapshot(self._store.get(self._id))


class _FakeDb:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        store = self.collections.setdefault(name, {})
        return type('Col', (), {'document': lambda _self, doc_id: _FakeDocument(store, doc_id)})()


@pytest.fixture
def fake_db(monkeypatch):
    db = _FakeDb()
    monkeypatch.setattr(quiz_set_service, 'get_db', lambda: db)
    return db


def test_save_and_get_each_round(fake_db):
    quizzes = _quizzes()

    quiz_set_id = quiz_set_service.save_quiz_set(quizzes)

    for i, expected in enumerate(quizzes):
        assert quiz_set_service.get_quiz(quiz_set_id, i) == expected
    assert quiz_set_service.get_quiz(quiz_set_id, 5) is None
    assert quiz_set_service.get_quiz(quiz_set_id, -1) is None


def test_saved_document_has_ttl_field(fake_db):
    before = datetime.now(timezone.utc)

    quiz_set_id = quiz_set_service.save_quiz_set(_quizzes())

    doc = fake_db.collections['quiz_sets'][quiz_set_id]
    assert doc['expires_at'] - doc['created_at'] == timedelta(days=1)
    assert doc['created_at'] >= before


def test_unknown_id_returns_none(fake_db):
    assert quiz_set_service.get_quiz('no-such-id', 0) is None


def test_expired_quiz_set_returns_none_even_before_ttl_deletion(fake_db):
    quiz_set_id = quiz_set_service.save_quiz_set(_quizzes())
    fake_db.collections['quiz_sets'][quiz_set_id]['expires_at'] = datetime.now(timezone.utc) - timedelta(seconds=1)

    assert quiz_set_service.get_quiz(quiz_set_id, 0) is None


# ---------------------------------------------------------------------------
# 結合テスト（Firestore エミュレータ）
# ---------------------------------------------------------------------------

pytest_emulator = pytest.mark.skipif(
    not os.environ.get('FIRESTORE_EMULATOR_HOST'), reason='FIRESTORE_EMULATOR_HOST が未設定')


@pytest.fixture
def emulator_db(monkeypatch):
    # firebase_admin.initialize_app() はエミュレータ利用時も認証情報を要求するため、
    # テストではエミュレータに接続する Firestore クライアントを直接渡す
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import firestore

    db = firestore.Client(project=os.environ.get('GOOGLE_CLOUD_PROJECT', 'demo-yahooquiz'),
                          credentials=AnonymousCredentials())
    monkeypatch.setattr(quiz_set_service, 'get_db', lambda: db)
    return db


@pytest_emulator
def test_round_trip_on_emulator(emulator_db):
    quizzes = _quizzes()

    quiz_set_id = quiz_set_service.save_quiz_set(quizzes)

    for i, expected in enumerate(quizzes):
        assert quiz_set_service.get_quiz(quiz_set_id, i) == expected
    assert quiz_set_service.get_quiz(quiz_set_id, 5) is None
    assert quiz_set_service.get_quiz('no-such-id', 0) is None


@pytest_emulator
def test_expired_on_emulator(emulator_db):
    quiz_set_id = quiz_set_service.save_quiz_set(_quizzes())
    emulator_db.collection('quiz_sets').document(quiz_set_id).update(
        {'expires_at': datetime.now(timezone.utc) - timedelta(minutes=1)})

    assert quiz_set_service.get_quiz(quiz_set_id, 0) is None
