import os

import pytest
import requests

# services.auth は import 時に JWT_SECRET_KEY を必須とするため、テスト用の値を入れておく
# （テストモジュールの import より先に conftest が読み込まれる）
os.environ.setdefault('JWT_SECRET_KEY', 'test-jwt-secret-key')


@pytest.fixture
def firestore_emulator():
    """
    Firestore エミュレータに接続するクライアント（FIRESTORE_EMULATOR_HOST 未設定ならスキップ）。
    テストごとにエミュレータのデータを全削除する。
    """
    host = os.environ.get('FIRESTORE_EMULATOR_HOST')
    if not host:
        pytest.skip('FIRESTORE_EMULATOR_HOST が未設定')

    from google.auth.credentials import AnonymousCredentials
    from google.cloud import firestore

    project = os.environ.get('GOOGLE_CLOUD_PROJECT', 'demo-yahooquiz')
    requests.delete(
        f'http://{host}/emulator/v1/projects/{project}/databases/(default)/documents', timeout=10
    ).raise_for_status()
    # firebase_admin.initialize_app() はエミュレータ利用時も認証情報を要求するため、
    # テストではエミュレータに接続する Firestore クライアントを直接使う
    return firestore.Client(project=project, credentials=AnonymousCredentials())
