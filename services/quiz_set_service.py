# services/quiz_set_service.py
"""
1ゲーム分のクイズを Firestore（quiz_sets コレクション）に保存・取得する。
記事本文を含むとセッション Cookie の上限（約4KB）を超えるため、Cookie にはドキュメント ID だけを入れる。

古いデータは Firestore の TTL ポリシーで expires_at を過ぎたら自動削除する（設定は README 参照）。
TTL による削除は期限から遅れることがあるため、読み込み時にも期限を確認する。
"""
import uuid
from datetime import datetime, timedelta, timezone

from db import get_db

COLLECTION = 'quiz_sets'
RETENTION = timedelta(days=1)


def save_quiz_set(quizzes):
    """クイズのリストを保存し、ID を返す"""
    now = datetime.now(timezone.utc)
    quiz_set_id = str(uuid.uuid4())
    get_db().collection(COLLECTION).document(quiz_set_id).set({
        'quizzes': quizzes,
        'created_at': now,
        'expires_at': now + RETENTION,
    })
    return quiz_set_id


def get_quiz(quiz_set_id, index):
    """index 番目（0始まり）のクイズを返す。見つからない（期限切れ・範囲外など）場合は None"""
    doc = get_db().collection(COLLECTION).document(quiz_set_id).get()
    if not doc.exists:
        return None
    data = doc.to_dict() or {}

    expires_at = data.get('expires_at')
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        return None

    quizzes = data.get('quizzes') or []
    if not 0 <= index < len(quizzes):
        return None
    return quizzes[index]
