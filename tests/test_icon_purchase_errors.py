"""
アイコン購入の失敗の種類と HTTP ステータスのテスト（Firestore には接続しない）。
同時アクセスによる競合は 409、それ以外の購入できない理由は 400 にする。
"""
from types import SimpleNamespace

import pytest
from flask import Flask
from google.api_core import exceptions as gexc

import api_routes
from services import icon_service
from services.auth import create_access_token

USER_ID = '11111111-1111-1111-1111-111111111111'
ICON_ID = '22222222-2222-2222-2222-222222222222'


def _aborted():
    return gexc.Aborted('Transaction lock timeout.')


def _exhausted():
    """コミットの再試行を使い切ったとき、ライブラリは Aborted を原因とする ValueError を送出する"""
    try:
        raise ValueError('Failed to commit transaction in 10 attempts.') from _aborted()
    except ValueError as e:
        return e


@pytest.fixture
def fail_with(monkeypatch):
    monkeypatch.setattr(icon_service, 'get_db', lambda: SimpleNamespace(transaction=lambda **kwargs: None))

    def setup(error):
        def raise_error(*args, **kwargs):
            raise error
        monkeypatch.setattr(icon_service, '_purchase_in_transaction', raise_error)
    return setup


@pytest.mark.parametrize('error', [_aborted(), _exhausted()], ids=['aborted', 'retries_exhausted'])
def test_contention_is_reported_as_conflict(fail_with, error):
    fail_with(error)

    ok, result = icon_service.purchase_icon(USER_ID, ICON_ID)

    assert not ok
    assert result == {'error': icon_service.PURCHASE_CONFLICT_ERROR, 'conflict': True}


def test_already_exists_on_commit_means_already_owned(fail_with):
    fail_with(gexc.AlreadyExists('Document already exists'))

    ok, result = icon_service.purchase_icon(USER_ID, ICON_ID)

    assert not ok and result == {'error': 'You already own this icon'}


@pytest.mark.parametrize('error', [ValueError('other'), RuntimeError('boom'), gexc.ServiceUnavailable('down')])
def test_other_errors_are_not_conflicts(fail_with, error):
    fail_with(error)

    ok, result = icon_service.purchase_icon(USER_ID, ICON_ID)

    assert not ok and result == {'error': 'Failed to purchase icon'}


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(api_routes.api)
    return app.test_client()


def _purchase(client):
    token = create_access_token(USER_ID, 'user@example.com')
    return client.post(f'/api/icons/{ICON_ID}/purchase', headers={'Authorization': f'Bearer {token}'})


@pytest.mark.parametrize('result, status', [
    ((True, {'status': 'purchased'}), 200),
    ((False, {'error': icon_service.PURCHASE_CONFLICT_ERROR, 'conflict': True}), 409),
    ((False, {'error': 'Insufficient points', 'required': 100, 'current': 50}), 400),
    ((False, {'error': 'You already own this icon'}), 400),
    ((False, {'error': 'Failed to purchase icon'}), 400),
])
def test_purchase_api_status_codes(client, monkeypatch, result, status):
    monkeypatch.setattr(api_routes, 'purchase_icon', lambda user_id, icon_id: result)

    response = _purchase(client)

    assert response.status_code == status
    assert response.get_json() == result[1]
