# llm_client.py
"""
クイズ生成用の LLM 呼び出しを一か所にまとめたモジュール。

- メイン: Cerebras（OpenAI 互換 API）
- フォールバック: OpenRouter の無料モデル（`models` 配列で OpenRouter 側の自動フォールバックを利用）

呼び出し側は generate_quizzes() だけを使う（1ゲーム分の問題を1回の呼び出しで生成する）。プロバイダ・モデル名はすべて環境変数で指定する。
"""
import json
import logging
import os

import openai
from openai import OpenAI

logger = logging.getLogger(__name__)

# 大きすぎるとレート制限のクォータ計算で不利になるため、必要十分な値にとどめる
MAX_TOKENS = 4000
DEFAULT_TIMEOUT_SECONDS = 30.0

SYSTEM_PROMPT = (
    "あなたは日本語の時事クイズ作成者です。"
    "必ず指定された JSON オブジェクトのみを出力し、前置き・説明文・コードブロックは出力しないでください。"
)

USER_PROMPT_TEMPLATE = """以下の {n} 件のニュース記事から、4択クイズを記事ごとに1問ずつ、合計 {n} 問作成してください。

# 条件
- 1問目は記事1、2問目は記事2…のように、各問題は対応する記事の内容から作り、記事の順番どおりに並べる
- 同じ内容の記事が複数ある場合も、問題同士は重複させない
- 問題文は記事を読めば答えられる内容にする
- 選択肢はちょうど4つ。正解は1つだけで、選択肢同士は重複させない
- 選択肢はボタンに表示するため、それぞれ短い単語・語句にする（目安：15文字以内。記号や補足説明を含めない）
- 不正解の選択肢も正解と同じ種類の語句にする（人名なら人名、数値なら数値）
- answer_index は正解の選択肢の位置（0〜3）
- 解説は記事の内容に基づいて1〜2文で書く

# 出力形式（この JSON オブジェクトのみを出力。quizzes はちょうど {n} 要素）
{{"quizzes": [{{"question": "問題文", "choices": ["選択肢1", "選択肢2", "選択肢3", "選択肢4"], "answer_index": 0, "explanation": "解説"}}]}}

# 記事
"""


def build_user_prompt(article_texts):
    articles = '\n\n'.join(
        f"## 記事{i}\n{text}" for i, text in enumerate(article_texts, start=1)
    )
    return USER_PROMPT_TEMPLATE.format(n=len(article_texts)) + articles


class QuizGenerationError(Exception):
    """すべてのプロバイダでクイズ生成に失敗した（呼び出し側でユーザー向けエラーに変換する）"""


class _ProviderFailure(Exception):
    """1プロバイダの失敗。フォールバックの判断とログ用に理由を持つ"""

    def __init__(self, reason, log_level=logging.WARNING):
        super().__init__(reason)
        self.reason = reason
        self.log_level = log_level


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

def _env(name):
    value = os.environ.get(name, '').strip()
    return value or None


def _timeout_seconds():
    raw = _env('LLM_TIMEOUT_SECONDS')
    if raw is None:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        return float(raw)
    except ValueError:
        logger.warning("LLM_TIMEOUT_SECONDS が数値ではないため既定値 %s 秒を使用します", DEFAULT_TIMEOUT_SECONDS)
        return DEFAULT_TIMEOUT_SECONDS


def _cerebras_config():
    """Cerebras の設定。API キー未設定なら None（＝スキップ）"""
    api_key = _env('CEREBRAS_API_KEY')
    if not api_key:
        return None
    missing = [n for n in ('CEREBRAS_BASE_URL', 'CEREBRAS_MODEL') if not _env(n)]
    if missing:
        logger.error("Cerebras の設定が不足しています: %s", ', '.join(missing))
        return None
    return {
        'api_key': api_key,
        'base_url': _env('CEREBRAS_BASE_URL'),
        'model': _env('CEREBRAS_MODEL'),
    }


def _openrouter_config():
    """OpenRouter の設定。API キー未設定なら None（＝スキップ）"""
    api_key = _env('OPENROUTER_API_KEY')
    if not api_key:
        return None
    models = [m.strip() for m in (_env('OPENROUTER_MODELS') or '').split(',') if m.strip()]
    missing = [n for n in ('OPENROUTER_BASE_URL',) if not _env(n)]
    if not models:
        missing.append('OPENROUTER_MODELS')
    if missing:
        logger.error("OpenRouter の設定が不足しています: %s", ', '.join(missing))
        return None
    return {
        'api_key': api_key,
        'base_url': _env('OPENROUTER_BASE_URL'),
        'models': models,
        'site_url': _env('OPENROUTER_SITE_URL') or 'https://github.com/GenkiNakashima/Yahooquiz',
        'app_name': _env('OPENROUTER_APP_NAME') or 'Yahooquiz',
    }


def configured_providers():
    """利用可能なプロバイダ名の一覧（起動時チェック用）"""
    providers = []
    if _cerebras_config():
        providers.append('cerebras')
    if _openrouter_config():
        providers.append('openrouter')
    return providers


# ---------------------------------------------------------------------------
# 出力のパースとバリデーション
# ---------------------------------------------------------------------------

def _parse_json(content):
    if not content or not content.strip():
        raise ValueError("empty response")
    text = content.strip()
    # json_object 指定でもコードブロックで囲むモデルがあるため、外側のフェンスだけ取り除く
    if text.startswith('```'):
        text = text.split('\n', 1)[1] if '\n' in text else ''
        if text.rstrip().endswith('```'):
            text = text.rstrip()[:-3]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON ({e.msg})") from None
    if not isinstance(data, dict):
        raise ValueError("JSON is not an object")
    return data


def validate_quiz(data):
    """
    1問分のクイズを検証し、正規化した dict を返す。
    不正な場合は ValueError（理由のみを含み、内容は含めない）。
    """
    question = data.get('question')
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question is empty")

    choices = data.get('choices')
    if not isinstance(choices, list) or len(choices) != 4:
        raise ValueError("choices must be a list of exactly 4 items")
    if not all(isinstance(c, str) and c.strip() for c in choices):
        raise ValueError("choices must be non-empty strings")
    choices = [c.strip() for c in choices]
    if len(set(choices)) != 4:
        raise ValueError("choices must be distinct")

    answer_index = data.get('answer_index')
    # bool は int のサブクラスなので明示的に除外する
    if isinstance(answer_index, bool) or not isinstance(answer_index, int) or not 0 <= answer_index <= 3:
        raise ValueError("answer_index must be an integer in 0-3")

    explanation = data.get('explanation')
    if not isinstance(explanation, str) or not explanation.strip():
        raise ValueError("explanation is empty")

    return {
        'question': question.strip(),
        'choices': choices,
        'answer_index': answer_index,
        # 既存の画面・判定（自由記述の完全一致）はこの値を使う
        'answer': choices[answer_index],
        'explanation': explanation.strip(),
    }


def validate_quiz_set(data, expected_count):
    """
    {"quizzes": [...]} を検証する。問題数がちょうど expected_count で、すべての問題が有効な場合のみ通す
    （1問でも不正なら全体を不正とし、別プロバイダで作り直す）。
    """
    quizzes = data.get('quizzes')
    if not isinstance(quizzes, list):
        raise ValueError("quizzes must be a list")
    if len(quizzes) != expected_count:
        raise ValueError(f"expected {expected_count} quizzes, got {len(quizzes)}")
    validated = []
    for i, item in enumerate(quizzes, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"quiz {i}: not an object")
        try:
            validated.append(validate_quiz(item))
        except ValueError as e:
            raise ValueError(f"quiz {i}: {e}") from None
    return validated


# ---------------------------------------------------------------------------
# プロバイダ呼び出し
# ---------------------------------------------------------------------------

def _classify_api_error(e):
    """SDK の例外をフォールバック理由に変換する（API キーや本文はログに含めない）"""
    if isinstance(e, openai.APITimeoutError):
        return _ProviderFailure("timeout")
    if isinstance(e, openai.APIConnectionError):
        return _ProviderFailure("connection_error")
    if isinstance(e, openai.APIStatusError):
        status = e.status_code
        if status == 429:
            return _ProviderFailure("rate_limited (429)")
        if status >= 500:
            return _ProviderFailure(f"server_error ({status})")
        if status == 404:
            return _ProviderFailure("model_not_found (404)")
        # 400/401/403 などは設定ミスの可能性が高いので ERROR で残す
        return _ProviderFailure(
            f"client_error ({status}) - API キー・モデル名・リクエストパラメータを確認してください",
            log_level=logging.ERROR,
        )
    return _ProviderFailure(f"unexpected_error ({type(e).__name__})", log_level=logging.ERROR)


def _complete(client, messages, **kwargs):
    """chat.completions を呼び出し、(本文, 実際に応答したモデル名) を返す"""
    response = client.chat.completions.create(
        messages=messages,
        max_tokens=MAX_TOKENS,
        temperature=0.7,
        response_format={'type': 'json_object'},
        **kwargs,
    )
    if not getattr(response, 'choices', None):
        raise _ProviderFailure("invalid_output (no choices)")
    return response.choices[0].message.content, getattr(response, 'model', None)


def _call_cerebras(config, messages):
    client = OpenAI(
        api_key=config['api_key'],
        base_url=config['base_url'],
        timeout=_timeout_seconds(),
        # 429 でも待機せずに即フォールバックするため、SDK の自動リトライは無効にする
        max_retries=0,
    )
    content, _ = _complete(
        client,
        messages,
        model=config['model'],
        # このモデルは既定で推論が high になりトークンを浪費するため無効化する
        extra_body={'reasoning_effort': 'none'},
    )
    return content, config['model']


def _call_openrouter(config, messages):
    client = OpenAI(
        api_key=config['api_key'],
        base_url=config['base_url'],
        timeout=_timeout_seconds(),
        max_retries=0,
        default_headers={
            'HTTP-Referer': config['site_url'],
            'X-Title': config['app_name'],
        },
    )
    models = config['models']
    content, served_model = _complete(
        client,
        messages,
        model=models[0],
        # 複数モデルを渡し、OpenRouter 側の自動フォールバックを利用する
        extra_body={'models': models},
    )
    return content, served_model or models[0]


# ---------------------------------------------------------------------------
# 公開関数
# ---------------------------------------------------------------------------

def generate_quizzes(article_texts):
    """
    記事本文のリストから、記事ごとに1問ずつ4択クイズを1回の LLM 呼び出しで生成する。

    Returns:
        list[dict]: 記事と同じ順番のクイズ。各要素は
            question, choices(4), answer_index, answer(= choices[answer_index]), explanation
    Raises:
        QuizGenerationError: すべてのプロバイダで生成できなかった場合
    """
    if not article_texts:
        raise ValueError("article_texts must not be empty")
    expected = len(article_texts)
    messages = [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': build_user_prompt(article_texts)},
    ]

    providers = [
        ('cerebras', _cerebras_config(), _call_cerebras),
        ('openrouter', _openrouter_config(), _call_openrouter),
    ]

    failures = []
    for name, config, call in providers:
        if config is None:
            failures.append(f"{name}: not_configured")
            continue
        try:
            content, model = call(config, messages)
            try:
                quizzes = validate_quiz_set(_parse_json(content), expected)
            except ValueError as e:
                raise _ProviderFailure(f"invalid_output ({e})") from None
        except Exception as e:
            f = e if isinstance(e, _ProviderFailure) else _classify_api_error(e)
            logger.log(f.log_level, "LLM provider=%s failed: %s -> フォールバックします", name, f.reason)
            failures.append(f"{name}: {f.reason}")
            continue
        if failures:
            logger.info("クイズ生成成功 provider=%s model=%s count=%d (フォールバック理由: %s)",
                        name, model, expected, '; '.join(failures))
        else:
            logger.info("クイズ生成成功 provider=%s model=%s count=%d", name, model, expected)
        return quizzes

    logger.error("すべての LLM プロバイダでクイズ生成に失敗しました: %s", '; '.join(failures))
    raise QuizGenerationError('; '.join(failures))
