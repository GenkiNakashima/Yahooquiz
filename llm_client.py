# llm_client.py
"""
クイズ生成用の LLM 呼び出しを一か所にまとめたモジュール。

候補を次の優先順で試す（どれも OpenAI 互換 API を openai SDK で呼ぶ）:
  1. Groq の GROQ_MODELS（既定: openai/gpt-oss-120b → openai/gpt-oss-20b → qwen/qwen3.8-27b）。
     Groq のレート制限はモデルごとなので、1つ目が 429 でも次のモデルは使える
  2. OpenRouter の無料モデル（`models` 配列で OpenRouter 側の自動フォールバックを利用）

1問だけ検証に失敗した場合は、その記事だけを同じ候補に1回だけ再送する
（無料枠の消費を抑えるため）。それでも失敗した問題は次の候補に回す。

呼び出し側は generate_quizzes() だけを使う（1ゲーム分の問題を1回の呼び出しで生成する）。
"""
import json
import logging
import os

import openai
from openai import OpenAI

logger = logging.getLogger(__name__)

# Groq 無料枠の TPM（8,000）に収めるため、入力（記事5件で約2,500トークン）+ max_tokens を 7,000 以下にする
MAX_TOKENS = 2500
# プロンプトに入れる記事1件あたりの最大文字数（画面に表示する記事本文は切り詰めない）
ARTICLE_MAX_CHARS = 500
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_GROQ_BASE_URL = 'https://api.groq.com/openai/v1'
DEFAULT_GROQ_MODELS = 'openai/gpt-oss-120b,openai/gpt-oss-20b,qwen/qwen3.8-27b'
# 同じ候補で、検証に失敗した問題だけを作り直す回数
MAX_REGENERATIONS_PER_CANDIDATE = 1

# Groq のモデルごとの推論パラメータ（https://console.groq.com/docs/reasoning）。
# scripts/check_groq_models.py で実際に呼び出し、content に推論が混ざらないことを確認する
GROQ_REASONING_PARAMS = [
    # Qwen 3.8 27B: "none" で推論を無効化し、"hidden" で最終回答だけを返させる
    ('qwen/qwen3.8', {'reasoning_effort': 'none', 'reasoning_format': 'hidden'}),
    # gpt-oss（120b / 20b）は "none" に非対応のため最小の "low" にする
    ('openai/gpt-oss', {'reasoning_effort': 'low'}),
]

# モデルごとの max_tokens（未指定のモデルは MAX_TOKENS）。
# Qwen 3.8 27B は無料枠に OTPM（出力トークン/分）1,000 の上限があり、max_tokens がそれを超えると
# 毎回 429（Request too large）になるため、上限より小さくする
GROQ_MAX_TOKENS = [
    ('qwen/qwen3.8', 900),
]

SYSTEM_PROMPT = (
    "あなたは日本語の時事クイズ作成者です。"
    "必ず指定された JSON オブジェクトのみを出力し、前置き・説明文・コードブロックは出力しないでください。"
)

USER_PROMPT_TEMPLATE = """以下の {n} 件のニュース記事から、4択クイズを記事ごとに1問ずつ、合計 {n} 問作成してください。

# 条件
- 各記事から1問ずつ作り、各問題の article_index に、その問題を作った記事の番号（「## 記事1」なら 1）を入れる。
  すべての記事番号（1〜{n}）をちょうど1回ずつ使い、同じ記事番号を2つの問題に使わない
- 同じ内容の記事が複数ある場合も、問題同士は重複させない
- 問題文は記事を読めば答えられる内容にする
- 問題文・解説に記事番号や「記事3で」「この記事では」のような記事を指す表現を含めない（プレイヤーには1問につき1記事だけが表示され、記事番号は見えない）
- 選択肢はちょうど4つ。正解は1つだけで、選択肢同士は重複させない
- 選択肢はボタンに表示するため、それぞれ短い単語・語句にする（目安：15文字以内。記号や補足説明を含めない）
- 不正解の選択肢も正解と同じ種類の語句にする（人名なら人名、数値なら数値）
- 数値の選択肢は等間隔にせず、ばらつきを持たせる（例: 10・20・30・40 のような並びにしない）
- answer_index は正解の選択肢の位置（0〜3）
- 解説は記事の内容に基づいて1〜2文で書く

# 出力形式（この JSON オブジェクトのみを出力。quizzes はちょうど {n} 要素）
{{"quizzes": [{{"article_index": 1, "question": "問題文", "choices": ["選択肢1", "選択肢2", "選択肢3", "選択肢4"], "answer_index": 0, "explanation": "解説"}}]}}

# 記事
"""


def truncate_article(text, max_chars=ARTICLE_MAX_CHARS):
    """max_chars 以内で最後の「。」まで残す（文の途中で切らない）。「。」がなければ max_chars で切る"""
    if len(text) <= max_chars:
        return text
    head = text[:max_chars]
    end = head.rfind('。')
    return head[:end + 1] if end >= 0 else head


def build_user_prompt(article_texts):
    articles = '\n\n'.join(
        f"## 記事{i}\n{truncate_article(text)}" for i, text in enumerate(article_texts, start=1)
    )
    return USER_PROMPT_TEMPLATE.format(n=len(article_texts)) + articles


def build_messages(article_texts):
    return [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': build_user_prompt(article_texts)},
    ]


class QuizGenerationError(Exception):
    """すべてのプロバイダでクイズ生成に失敗した（呼び出し側でユーザー向けエラーに変換する）"""


class _ProviderFailure(Exception):
    """1候補の失敗。フォールバックの判断とログ用に理由を持つ"""

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


def _split_models(value):
    return [m.strip() for m in (value or '').split(',') if m.strip()]


def _timeout_seconds():
    raw = _env('LLM_TIMEOUT_SECONDS')
    if raw is None:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        return float(raw)
    except ValueError:
        logger.warning("LLM_TIMEOUT_SECONDS が数値ではないため既定値 %s 秒を使用します", DEFAULT_TIMEOUT_SECONDS)
        return DEFAULT_TIMEOUT_SECONDS


def groq_reasoning_params(model):
    for prefix, params in GROQ_REASONING_PARAMS:
        if model.startswith(prefix):
            return dict(params)
    return {}


def groq_max_tokens(model):
    for prefix, max_tokens in GROQ_MAX_TOKENS:
        if model.startswith(prefix):
            return max_tokens
    return MAX_TOKENS


def _groq_candidates():
    """Groq の候補（モデルごとに1つ）。API キー未設定なら空（＝スキップ）"""
    api_key = _env('GROQ_API_KEY')
    if not api_key:
        return []
    models = _split_models(_env('GROQ_MODELS') or DEFAULT_GROQ_MODELS)
    base_url = _env('GROQ_BASE_URL') or DEFAULT_GROQ_BASE_URL
    return [
        {
            'provider': 'groq',
            'label': f'groq:{model}',
            'api_key': api_key,
            'base_url': base_url,
            'model': model,
            'extra_body': groq_reasoning_params(model),
            'max_tokens': groq_max_tokens(model),
            'temperature': None,
            'default_headers': None,
        }
        for model in models
    ]


def _openrouter_candidates():
    """OpenRouter の候補（models 配列ごと1つ）。API キー未設定なら空（＝スキップ）"""
    api_key = _env('OPENROUTER_API_KEY')
    if not api_key:
        return []
    models = _split_models(_env('OPENROUTER_MODELS'))
    missing = [n for n in ('OPENROUTER_BASE_URL',) if not _env(n)]
    if not models:
        missing.append('OPENROUTER_MODELS')
    if missing:
        logger.error("OpenRouter の設定が不足しています: %s", ', '.join(missing))
        return []
    return [{
        'provider': 'openrouter',
        'label': 'openrouter',
        'api_key': api_key,
        'base_url': _env('OPENROUTER_BASE_URL'),
        'model': models[0],
        # 複数モデルを渡し、OpenRouter 側の自動フォールバックを利用する
        'extra_body': {'models': models},
        'max_tokens': MAX_TOKENS,
        'temperature': 0.7,
        'default_headers': {
            'HTTP-Referer': _env('OPENROUTER_SITE_URL') or 'https://github.com/GenkiNakashima/Yahooquiz',
            'X-Title': _env('OPENROUTER_APP_NAME') or 'Yahooquiz',
        },
    }]


def candidates():
    """優先順の候補一覧"""
    return _groq_candidates() + _openrouter_candidates()


def configured_providers():
    """利用可能な候補名の一覧（起動時チェック用）。例: ['groq:qwen/qwen3.8-27b', 'openrouter']"""
    return [c['label'] for c in candidates()]


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


def _article_index(item, expected_count):
    """問題の article_index（1 始まり）を検証して返す。不正なら ValueError"""
    index = item.get('article_index')
    # bool は int のサブクラスなので明示的に除外する
    if isinstance(index, bool) or not isinstance(index, int) or not 1 <= index <= expected_count:
        raise ValueError(f"article_index must be an integer in 1-{expected_count}")
    return index


def _validate_quiz_items(data, expected_count):
    """
    {"quizzes": [...]} を1問ずつ検証し、article_index（元の記事の番号）の順に並べ替える（一部だけ作り直すため）。
    LLM は記事の順番どおりに並べないことがあるため、出力の並び順ではなく article_index で記事と対応付ける。

    - 問題数が合わない・配列でない場合は、全体を ValueError にする
    - article_index が範囲外・整数でない問題、同じ article_index を持つ問題（どれが正しいか分からないので全部）は
      検証失敗とし、その記事の問題は作り直す

    Returns:
        (results, errors): results は記事と同じ順番で、有効なら dict、問題が無い・不正なら None。
                           errors は不正だった問題の理由のリスト
    """
    quizzes = data.get('quizzes')
    if not isinstance(quizzes, list):
        raise ValueError("quizzes must be a list")
    if len(quizzes) != expected_count:
        raise ValueError(f"expected {expected_count} quizzes, got {len(quizzes)}")

    errors = []
    by_index = {}  # article_index → [(出力の位置, 検証済みの問題)]
    for i, item in enumerate(quizzes, start=1):
        try:
            if not isinstance(item, dict):
                raise ValueError("not an object")
            index = _article_index(item, expected_count)
            quiz = validate_quiz(item)
        except ValueError as e:
            errors.append(f"quiz {i}: {e}")
            continue
        by_index.setdefault(index, []).append((i, quiz))

    results = [None] * expected_count
    for index, entries in sorted(by_index.items()):
        if len(entries) > 1:
            positions = ', '.join(str(i) for i, _ in entries)
            errors.append(f"quiz {positions}: duplicate article_index {index}")
            continue
        results[index - 1] = entries[0][1]
    # 有効な問題が無い記事（不正・重複・どの問題にも使われなかった記事）は、呼び出し側で作り直す
    return results, errors


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


def request_completion(candidate, messages):
    """1候補に1回だけリクエストし、SDK のレスポンスをそのまま返す（確認スクリプトからも使う）"""
    client = OpenAI(
        api_key=candidate['api_key'],
        base_url=candidate['base_url'],
        timeout=_timeout_seconds(),
        # 429 でも待機せずに即フォールバックするため、SDK の自動リトライは無効にする
        max_retries=0,
        default_headers=candidate['default_headers'],
    )
    kwargs = {}
    if candidate['temperature'] is not None:
        kwargs['temperature'] = candidate['temperature']
    if candidate['extra_body']:
        kwargs['extra_body'] = candidate['extra_body']
    return client.chat.completions.create(
        model=candidate['model'],
        messages=messages,
        max_tokens=candidate['max_tokens'],
        response_format={'type': 'json_object'},
        **kwargs,
    )


def _generate_with(candidate, article_texts):
    """
    1候補で article_texts の問題を生成する。

    Returns:
        (results, errors, served_model): results は記事と同じ順番で、不正な問題は None
    Raises:
        _ProviderFailure: API エラー・JSON 不正・問題数の不一致など、候補ごと諦める場合
    """
    try:
        response = request_completion(candidate, build_messages(article_texts))
    except Exception as e:
        raise _classify_api_error(e) from None
    if not getattr(response, 'choices', None):
        raise _ProviderFailure("invalid_output (no choices)")
    choice = response.choices[0]
    # max_tokens に達して出力が途中で切れた場合は、JSON 不正と同じ扱いで次の候補へ
    if getattr(choice, 'finish_reason', None) == 'length':
        raise _ProviderFailure(f"invalid_output (truncated: finish_reason=length, max_tokens={candidate['max_tokens']})")
    content = choice.message.content
    try:
        results, errors = _validate_quiz_items(_parse_json(content), len(article_texts))
    except ValueError as e:
        raise _ProviderFailure(f"invalid_output ({e})") from None
    return results, errors, getattr(response, 'model', None) or candidate['model']


# ---------------------------------------------------------------------------
# 公開関数
# ---------------------------------------------------------------------------

def generate_quizzes(article_texts):
    """
    記事本文のリストから、記事ごとに1問ずつ4択クイズを生成する（通常は1回の LLM 呼び出し）。

    - 一部の問題だけ検証に失敗した場合は、その記事だけを同じ候補に1回だけ再送する
    - それでも残った問題（または API エラー等で候補ごと失敗した問題）は次の候補で生成する
    - 成功した問題は記事の順番どおりにマージする

    Returns:
        list[dict]: 記事と同じ順番のクイズ。各要素は
            question, choices(4), answer_index, answer(= choices[answer_index]), explanation
    Raises:
        QuizGenerationError: すべての候補で生成できなかった場合
    """
    if not article_texts:
        raise ValueError("article_texts must not be empty")
    total = len(article_texts)
    results = [None] * total
    pending = list(range(total))  # まだ問題ができていない記事の位置
    failures = []
    succeeded_with = []

    available = candidates()
    if not available:
        failures.append("not_configured")

    for candidate in available:
        label = candidate['label']
        regenerations = 0
        while pending:
            try:
                batch, errors, model = _generate_with(candidate, [article_texts[i] for i in pending])
            except _ProviderFailure as f:
                logger.log(f.log_level, "LLM provider=%s failed: %s -> 次の候補へフォールバックします", label, f.reason)
                failures.append(f"{label}: {f.reason}")
                break

            got = [i for i, quiz in zip(pending, batch) if quiz is not None]
            for i, quiz in zip(pending, batch):
                if quiz is not None:
                    results[i] = quiz
            if got:
                succeeded_with.append(f"provider={candidate['provider']} model={model} count={len(got)}")
            pending = [i for i, quiz in zip(pending, batch) if quiz is None]
            if not pending:
                break

            reason = f"invalid_output ({'; '.join(errors)})"
            # 1問もできなかった場合は、この候補では作れないとみなして次の候補へ
            if not got or regenerations >= MAX_REGENERATIONS_PER_CANDIDATE:
                logger.warning("LLM provider=%s: %d 問が不正 %s -> 次の候補へフォールバックします",
                               label, len(pending), reason)
                failures.append(f"{label}: {reason}")
                break
            regenerations += 1
            logger.warning("LLM provider=%s: %d 問が不正 %s -> 同じ候補でその問題だけ再生成します",
                           label, len(pending), reason)
        if not pending:
            break

    if pending:
        logger.error("すべての LLM 候補でクイズ生成に失敗しました: %s", '; '.join(failures))
        raise QuizGenerationError('; '.join(failures))

    if failures:
        logger.info("クイズ生成成功 %s (全 %d 問, フォールバックの理由: %s)",
                    ', '.join(succeeded_with), total, '; '.join(failures))
    else:
        logger.info("クイズ生成成功 %s (全 %d 問)", ', '.join(succeeded_with), total)
    return results
