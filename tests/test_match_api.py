"""
POST /api/matches のテスト。
対戦結果はクライアントから受け取らず、セッションのゲームをサーバー側の記録から確定して登録することを確認する。
記録処理（match_service.record_game_result）はモックに差し替える。
"""
import importlib

import pytest

from services.auth import create_access_token

USER_ID = '11111111-1111-1111-1111-111111111111'


@pytest.fixture(scope='module')
def app_module():
    mp = pytest.MonkeyPatch()
    mp.setenv('CEREBRAS_API_KEY', 'test-key')
    mp.setenv('CEREBRAS_BASE_URL', 'https://cerebras.test/v1')
    mp.setenv('CEREBRAS_MODEL', 'test-model')
    module = importlib.import_module('app')
    module.app.config['TESTING'] = True
    yield module
    mp.undo()


@pytest.fixture
def api(app_module, monkeypatch):
    import api_routes

    state = {'calls': [], 'response': (True, {'match_id': 'm1', 'points_awarded': 100, 'is_winner': True}, 201)}

    def record_game_result(user_id, quiz_set_id):
        state['calls'].append((user_id, quiz_set_id))
        return state['response']

    monkeypatch.setattr(api_routes, 'record_game_result', record_game_result)
    state['client'] = app_module.app.test_client()
    state['auth'] = {'Authorization': f'Bearer {create_access_token(USER_ID, "user@example.com")}'}
    return state


def _start_game(client, quiz_set_id='qs-1'):
    with client.session_transaction() as sess:
        sess['quiz_set_id'] = quiz_set_id
        sess['round'] = 5


def test_requires_authentication(api):
    _start_game(api['client'])

    response = api['client'].post('/api/matches', json={})

    assert response.status_code == 401
    assert api['calls'] == []


def test_records_the_session_game_for_the_logged_in_user(api):
    _start_game(api['client'], 'qs-42')

    response = api['client'].post('/api/matches', json={}, headers=api['auth'])

    assert response.status_code == 201
    assert response.get_json()['points_awarded'] == 100
    assert api['calls'] == [(USER_ID, 'qs-42')]


def test_body_is_optional(api):
    _start_game(api['client'])

    response = api['client'].post('/api/matches', headers=api['auth'])

    assert response.status_code == 201
    assert len(api['calls']) == 1


@pytest.mark.parametrize('body', [
    {'players': [{'user_id': USER_ID, 'score': 5, 'is_winner': True}]},
    {'match_type': '1v1', 'players': [{'user_id': '22222222-2222-2222-2222-222222222222', 'is_winner': True}]},
])
def test_client_supplied_players_are_rejected(api, body):
    _start_game(api['client'])

    response = api['client'].post('/api/matches', json=body, headers=api['auth'])

    assert response.status_code == 400
    assert api['calls'] == []


def test_no_game_in_session(api):
    response = api['client'].post('/api/matches', json={}, headers=api['auth'])

    assert response.status_code == 400
    assert api['calls'] == []


@pytest.mark.parametrize('status, payload', [
    (409, {'error': 'This game has already been recorded', 'match_id': 'm1'}),
    (400, {'error': 'Game is not finished'}),
    (404, {'error': 'Game not found'}),
])
def test_rejections_are_passed_through(api, status, payload):
    _start_game(api['client'])
    api['response'] = (False, payload, status)

    response = api['client'].post('/api/matches', json={}, headers=api['auth'])

    assert response.status_code == status
    assert response.get_json() == payload
