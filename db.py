# db.py
"""
Firestore クライアント。

- 本番（Cloud Run 上）: firebase_admin + サービスアカウント（Application Default Credentials）で本番の Firestore に接続する
- 本番以外（ローカル開発・Docker・テスト）: 必ず Firestore エミュレーターに接続する。
  FIRESTORE_EMULATOR_HOST が未設定なら起動時にエラーにして、手元の認証情報（ADC）で本番に書き込む事故を防ぐ

本番かどうかは、Cloud Run がすべてのコンテナに自動で設定する環境変数 K_SERVICE の有無で判別する
（設定し忘れることがなく、ローカルでは設定されない）。
"""
import logging
import os
import socket

import firebase_admin
from firebase_admin import firestore as fs

logger = logging.getLogger(__name__)

# エミュレーター用の既定のプロジェクト ID。"demo-" で始まる ID は実在するプロジェクトに対応しない
DEFAULT_EMULATOR_PROJECT = 'demo-yahooquiz'
EMULATOR_CONNECT_TIMEOUT_SECONDS = 2.0

_db = None


class UnsafeFirestoreTargetError(RuntimeError):
    """本番以外の環境で、エミュレーター以外の Firestore に接続しようとした"""


def is_production():
    return bool(os.environ.get('K_SERVICE', '').strip())


def _emulator_host():
    return os.environ.get('FIRESTORE_EMULATOR_HOST', '').strip() or None


def check_firestore_target():
    """
    本番以外では、エミュレーターが設定されていて接続できることを確認する。満たさなければ UnsafeFirestoreTargetError。
    （エミュレーターが起動していないと、最初のクエリが数分間応答しないため、起動時に短いタイムアウトで確認する）
    """
    if is_production():
        return
    host = _emulator_host()
    if not host:
        raise UnsafeFirestoreTargetError(
            "FIRESTORE_EMULATOR_HOST が設定されていません。本番以外の環境では、本番の Firestore に接続しないよう "
            "Firestore エミュレーターを使います。エミュレーターを起動し、.env に FIRESTORE_EMULATOR_HOST=localhost:8085 を"
            "設定してください（README の「ローカル開発」を参照）"
        )
    hostname, _, port = host.rpartition(':')
    try:
        with socket.create_connection((hostname or 'localhost', int(port)), timeout=EMULATOR_CONNECT_TIMEOUT_SECONDS):
            pass
    except (OSError, ValueError) as e:
        raise UnsafeFirestoreTargetError(
            f"Firestore エミュレーター（{host}）に接続できません: {type(e).__name__}。"
            "エミュレーターが起動しているか確認してください（README の「ローカル開発」を参照）"
        ) from None


def init_db():
    """Initialize Firestore client"""
    global _db
    try:
        host = _emulator_host()
        if host:
            # エミュレーターでは認証情報を使わない（ADC が無い環境でも動き、本番の認証情報を読み込まない）
            from google.auth.credentials import AnonymousCredentials
            from google.cloud import firestore

            project = os.environ.get('GOOGLE_CLOUD_PROJECT', '').strip() or DEFAULT_EMULATOR_PROJECT
            _db = firestore.Client(project=project, credentials=AnonymousCredentials())
            logger.info("Firestore client initialized (emulator: %s, project: %s)", host, project)
        else:
            if not is_production():
                raise UnsafeFirestoreTargetError("本番以外の環境ではエミュレーターを使ってください（FIRESTORE_EMULATOR_HOST）")
            if not firebase_admin._apps:
                firebase_admin.initialize_app()
            _db = fs.client()
            logger.info("Firestore client initialized")
        return _db
    except UnsafeFirestoreTargetError:
        raise
    except Exception as e:
        logger.error(f"Failed to initialize Firestore: {e}")
        return None

def get_db():
    """Get Firestore client"""
    if _db is None:
        init_db()
    if _db is None:
        raise Exception("Firestore client not available")
    return _db

def check_db_health():
    """Check if Firestore connection is healthy"""
    try:
        db = get_db()
        list(db.collection('_health').limit(1).stream())
        return True
    except Exception as e:
        logger.error(f"Firestore health check failed: {e}")
        return False
