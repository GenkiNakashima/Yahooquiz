# services/quiz_set_service.py
"""
1ゲーム分のクイズを Firestore（quiz_sets コレクション）に保存・取得する。
記事本文を含むとセッション Cookie の上限（約4KB）を超えるため、Cookie にはドキュメント ID だけを入れる。

各ラウンドの回答結果（results）もこのドキュメントに記録する。得点はこの記録から計算するため、
セッション Cookie を送り直しても回答し直すことはできない。対戦結果の記録（match_service）もこの記録を使う。

古いデータは Firestore の TTL ポリシーで expires_at を過ぎたら自動削除する（設定は README 参照）。
TTL による削除は期限から遅れることがあるため、読み込み時にも期限を確認する。
"""
import uuid
from datetime import datetime, timedelta, timezone

from firebase_admin import firestore as fs

from db import get_db

COLLECTION = 'quiz_sets'
RETENTION = timedelta(days=1)


# ラウンドの回答結果
PLAYER_CORRECT = 'correct'
PLAYER_WRONG = 'wrong'
AI_CORRECT = 'ai_correct'
AI_WRONG = 'ai_wrong'
RESULTS = (PLAYER_CORRECT, PLAYER_WRONG, AI_CORRECT, AI_WRONG)


class QuizSetNotFound(Exception):
    """クイズセットが存在しない、または期限切れ"""


def is_expired(data):
    expires_at = data.get('expires_at')
    return expires_at is not None and expires_at <= datetime.now(timezone.utc)


def score_from_results(results):
    """回答結果の記録から得点を計算する"""
    values = list((results or {}).values())
    return {'player': values.count(PLAYER_CORRECT), 'ai': values.count(AI_CORRECT)}


def save_quiz_set(quizzes, metadata=None):
    """クイズのリストを保存し、ID を返す。metadata（AIレベル・ジャンルなど）は対戦結果の記録に使う"""
    now = datetime.now(timezone.utc)
    quiz_set_id = str(uuid.uuid4())
    get_db().collection(COLLECTION).document(quiz_set_id).set({
        'quizzes': quizzes,
        'metadata': metadata or {},
        'results': {},
        'created_at': now,
        'expires_at': now + RETENTION,
    })
    return quiz_set_id


def record_answer(quiz_set_id, index, result):
    """
    index 番目（0始まり）のラウンドの回答結果を記録し、(採用された結果, 全ラウンドの結果) を返す。
    すでに記録済みのラウンドは最初の結果を採用する（Cookie を送り直して回答し直すことはできない）。
    Raises:
        QuizSetNotFound: クイズセットが存在しない、または期限切れ
        ValueError: index・result が不正
    """
    if result not in RESULTS:
        raise ValueError(f"invalid result: {result}")
    db = get_db()
    return _record_answer_in_transaction(db.transaction(), db.collection(COLLECTION).document(quiz_set_id), index, result)


@fs.transactional
def _record_answer_in_transaction(transaction, ref, index, result):
    doc = ref.get(transaction=transaction)
    if not doc.exists:
        raise QuizSetNotFound(ref.id)
    data = doc.to_dict() or {}
    if is_expired(data):
        raise QuizSetNotFound(ref.id)
    if not 0 <= index < len(data.get('quizzes') or []):
        raise ValueError(f"invalid round index: {index}")

    results = dict(data.get('results') or {})
    key = str(index)
    if key in results:
        return results[key], results
    results[key] = result
    transaction.update(ref, {'results': results})
    return result, results


def get_quiz(quiz_set_id, index):
    """index 番目（0始まり）のクイズを返す。見つからない（期限切れ・範囲外など）場合は None"""
    doc = get_db().collection(COLLECTION).document(quiz_set_id).get()
    if not doc.exists:
        return None
    data = doc.to_dict() or {}
    if is_expired(data):
        return None

    quizzes = data.get('quizzes') or []
    if not 0 <= index < len(quizzes):
        return None
    return quizzes[index]
