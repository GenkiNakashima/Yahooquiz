"""本番以外の環境で本番の Firestore に接続しないためのガード（db.check_firestore_target / db.init_db）のテスト"""
import socket

import pytest

import db


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ('K_SERVICE', 'FIRESTORE_EMULATOR_HOST', 'GOOGLE_CLOUD_PROJECT'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(db, '_db', None)


@pytest.fixture
def listening_port():
    """接続できるポート（エミュレーターの代わり）"""
    server = socket.socket()
    server.bind(('127.0.0.1', 0))
    server.listen(1)
    yield server.getsockname()[1]
    server.close()


def _closed_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_local_without_emulator_is_rejected():
    with pytest.raises(db.UnsafeFirestoreTargetError, match='FIRESTORE_EMULATOR_HOST'):
        db.check_firestore_target()


def test_local_with_unreachable_emulator_is_rejected(monkeypatch):
    monkeypatch.setenv('FIRESTORE_EMULATOR_HOST', f'127.0.0.1:{_closed_port()}')

    with pytest.raises(db.UnsafeFirestoreTargetError, match='接続できません'):
        db.check_firestore_target()


def test_local_with_running_emulator_is_allowed(monkeypatch, listening_port):
    monkeypatch.setenv('FIRESTORE_EMULATOR_HOST', f'127.0.0.1:{listening_port}')

    db.check_firestore_target()


def test_production_is_detected_by_k_service(monkeypatch):
    monkeypatch.setenv('K_SERVICE', 'yahooquiz')

    assert db.is_production()
    db.check_firestore_target()   # 本番ではエミュレーター不要


def test_init_db_refuses_real_firestore_outside_production(monkeypatch):
    initialized = []
    monkeypatch.setattr(db.firebase_admin, 'initialize_app', lambda *a, **k: initialized.append(True))

    with pytest.raises(db.UnsafeFirestoreTargetError):
        db.init_db()
    assert initialized == []


def test_init_db_uses_emulator_with_demo_project_and_no_credentials(monkeypatch):
    monkeypatch.setenv('FIRESTORE_EMULATOR_HOST', 'localhost:8085')
    monkeypatch.setattr(db.firebase_admin, 'initialize_app',
                        lambda *a, **k: pytest.fail('エミュレーター使用時に本番用の初期化をしてはいけない'))

    client = db.init_db()

    assert client._emulator_host == 'localhost:8085'
    assert client.project == db.DEFAULT_EMULATOR_PROJECT
