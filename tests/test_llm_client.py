"""llm_client のテスト（実際の API は呼ばず、OpenAI クライアントをモックする）"""
import json
import logging
from types import SimpleNamespace

import httpx2
import openai
import pytest

import llm_client
from llm_client import QuizGenerationError

GROQ_URL = 'https://groq.test/openai/v1'
OPENROUTER_URL = 'https://openrouter.test/api/v1'
GROQ_KEY = 'gsk-groq-secret'
OPENROUTER_KEY = 'sk-openrouter-secret'
GPT_OSS = 'openai/gpt-oss-120b'
GPT_OSS_20B = 'openai/gpt-oss-20b'
QWEN = 'qwen/qwen3.8-27b'
ROUTER_A = 'vendor/model-a:free'
ROUTER_B = 'vendor/model-b:free'
ARTICLE = '日本銀行は本日、政策金利を0.1%引き上げることを発表しました。'

VALID_QUIZ = {
    'question': '日本銀行が引き上げを発表したのは何？',
    'choices': ['政策金利', '消費税', '最低賃金', '国債発行額'],
    'answer_index': 0,
    'explanation': '日本銀行は政策金利を0.1%引き上げると発表しました。',
}


def _quiz_set(*quizzes):
    return json.dumps({'quizzes': list(quizzes)}, ensure_ascii=False)


def _response(content, model='served-model', finish_reason='stop'):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)],
        model=model,
    )


def _status_error(cls, status):
    request = httpx2.Request('POST', 'https://example.test/v1/chat/completions')
    return cls(f'HTTP {status}', response=httpx2.Response(status, request=request), body=None)


class Providers:
    """
    (base_url, model) ごとに応答を登録すると、モックの OpenAI クライアントがその順に返す／投げる。
    呼び出し内容とクライアントの初期化引数を記録する。
    """

    def __init__(self):
        self.results = {}
        self.calls = []  # (base_url, init_kwargs, create_kwargs)

    def set(self, base_url, model, *results):
        self.results[(base_url, model)] = list(results)

    def openai(self, **init_kwargs):
        base_url = init_kwargs['base_url']

        def create(**kwargs):
            self.calls.append((base_url, init_kwargs, kwargs))
            queue = self.results.get((base_url, kwargs['model']))
            assert queue, f"想定外の呼び出し: {base_url} {kwargs['model']}"
            result = queue.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    def calls_to(self, base_url, model=None):
        return [c for c in self.calls if c[0] == base_url and (model is None or c[2]['model'] == model)]


@pytest.fixture
def env(monkeypatch):
    values = {
        'GROQ_API_KEY': GROQ_KEY,
        'GROQ_BASE_URL': GROQ_URL,
        'GROQ_MODELS': f'{GPT_OSS}, {GPT_OSS_20B}, {QWEN}',
        'OPENROUTER_API_KEY': OPENROUTER_KEY,
        'OPENROUTER_BASE_URL': OPENROUTER_URL,
        'OPENROUTER_MODELS': f'{ROUTER_A}, {ROUTER_B}',
        'LLM_TIMEOUT_SECONDS': '12',
    }
    for k, v in values.items():
        monkeypatch.setenv(k, v)
    return values


@pytest.fixture
def providers(monkeypatch):
    state = Providers()
    monkeypatch.setattr(llm_client, 'OpenAI', state.openai)
    return state


# ---------------------------------------------------------------------------
# Groq の2モデル
# ---------------------------------------------------------------------------

def test_first_model_gpt_oss_120b_success(env, providers):
    providers.set(GROQ_URL, GPT_OSS, _response(_quiz_set(VALID_QUIZ), model=GPT_OSS))

    (quiz,) = llm_client.generate_quizzes([ARTICLE])

    assert quiz == {**VALID_QUIZ, 'answer': '政策金利'}
    ((_, init, call),) = providers.calls
    assert init['api_key'] == GROQ_KEY
    assert init['max_retries'] == 0
    assert init['timeout'] == 12.0
    assert call['model'] == GPT_OSS
    assert call['extra_body'] == {'reasoning_effort': 'low'}
    assert call['max_tokens'] == 2500
    assert call['response_format'] == {'type': 'json_object'}
    assert 'temperature' not in call
    assert ARTICLE in call['messages'][-1]['content']


@pytest.mark.parametrize('error', [
    _status_error(openai.RateLimitError, 429),
    _status_error(openai.NotFoundError, 404),
], ids=['429', '404'])
def test_first_model_error_falls_back_to_gpt_oss_20b_without_retry(env, providers, error):
    providers.set(GROQ_URL, GPT_OSS, error)
    providers.set(GROQ_URL, GPT_OSS_20B, _response(_quiz_set(VALID_QUIZ), model=GPT_OSS_20B))

    (quiz,) = llm_client.generate_quizzes([ARTICLE])

    assert quiz['answer'] == '政策金利'
    # 待機・リトライせずに1回だけ呼んで次のモデルへ
    assert len(providers.calls_to(GROQ_URL, GPT_OSS)) == 1
    ((_, init, call),) = providers.calls_to(GROQ_URL, GPT_OSS_20B)
    assert init['max_retries'] == 0
    assert call['extra_body'] == {'reasoning_effort': 'low'}
    assert call['max_tokens'] == 2500
    assert providers.calls_to(OPENROUTER_URL) == []


def test_gpt_oss_both_fail_then_qwen_with_its_own_params(env, providers):
    providers.set(GROQ_URL, GPT_OSS, _status_error(openai.RateLimitError, 429))
    providers.set(GROQ_URL, GPT_OSS_20B, _status_error(openai.RateLimitError, 429))
    providers.set(GROQ_URL, QWEN, _response(_quiz_set(VALID_QUIZ), model=QWEN))

    (quiz,) = llm_client.generate_quizzes([ARTICLE])

    assert quiz['answer'] == '政策金利'
    ((_, _, call),) = providers.calls_to(GROQ_URL, QWEN)
    assert call['extra_body'] == {'reasoning_effort': 'none', 'reasoning_format': 'hidden'}
    # 無料枠の OTPM（1,000）を超えないよう、Qwen だけ max_tokens を小さくする
    assert call['max_tokens'] == 900
    assert providers.calls_to(OPENROUTER_URL) == []


def test_all_groq_models_fail_then_openrouter(env, providers):
    providers.set(GROQ_URL, GPT_OSS, _status_error(openai.RateLimitError, 429))
    providers.set(GROQ_URL, GPT_OSS_20B, _status_error(openai.RateLimitError, 429))
    providers.set(GROQ_URL, QWEN, _status_error(openai.RateLimitError, 429))
    providers.set(OPENROUTER_URL, ROUTER_A, _response(_quiz_set(VALID_QUIZ), model=ROUTER_B))

    (quiz,) = llm_client.generate_quizzes([ARTICLE])

    assert quiz['answer'] == '政策金利'
    ((_, init, call),) = providers.calls_to(OPENROUTER_URL)
    assert init['api_key'] == OPENROUTER_KEY
    assert init['max_retries'] == 0
    assert init['default_headers']['HTTP-Referer']
    assert init['default_headers']['X-Title'] == 'Yahooquiz'
    assert call['model'] == ROUTER_A
    assert call['extra_body'] == {'models': [ROUTER_A, ROUTER_B]}
    assert call['max_tokens'] == 2500
    assert call['response_format'] == {'type': 'json_object'}
    assert call['temperature'] == 0.7


def test_all_candidates_fail_raises(env, providers):
    providers.set(GROQ_URL, GPT_OSS, _status_error(openai.RateLimitError, 429))
    providers.set(GROQ_URL, GPT_OSS_20B, _status_error(openai.NotFoundError, 404))
    providers.set(GROQ_URL, QWEN, _status_error(openai.RateLimitError, 429))
    providers.set(OPENROUTER_URL, ROUTER_A, _status_error(openai.InternalServerError, 503))

    with pytest.raises(QuizGenerationError) as excinfo:
        llm_client.generate_quizzes([ARTICLE])

    message = str(excinfo.value)
    assert f'groq:{GPT_OSS}: rate_limited (429)' in message
    assert f'groq:{GPT_OSS_20B}: model_not_found (404)' in message
    assert f'groq:{QWEN}: rate_limited (429)' in message
    assert 'openrouter: server_error (503)' in message


# ---------------------------------------------------------------------------
# フォールバック条件
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('error, reason', [
    (_status_error(openai.InternalServerError, 500), 'server_error (500)'),
    (openai.APITimeoutError(request=httpx2.Request('POST', GROQ_URL)), 'timeout'),
    (openai.APIConnectionError(request=httpx2.Request('POST', GROQ_URL)), 'connection_error'),
])
def test_transient_errors_fall_back(env, providers, caplog, error, reason):
    providers.set(GROQ_URL, GPT_OSS, error)
    providers.set(GROQ_URL, GPT_OSS_20B, _response(_quiz_set(VALID_QUIZ)))

    with caplog.at_level(logging.INFO, logger='llm_client'):
        llm_client.generate_quizzes([ARTICLE])

    assert any(reason in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)


@pytest.mark.parametrize('cls, status', [
    (openai.AuthenticationError, 401),
    (openai.PermissionDeniedError, 403),
    (openai.BadRequestError, 400),
])
def test_client_errors_fall_back_with_error_log(env, providers, caplog, cls, status):
    providers.set(GROQ_URL, GPT_OSS, _status_error(cls, status))
    providers.set(GROQ_URL, GPT_OSS_20B, _response(_quiz_set(VALID_QUIZ)))

    with caplog.at_level(logging.INFO, logger='llm_client'):
        llm_client.generate_quizzes([ARTICLE])

    assert any(f'client_error ({status})' in r.getMessage() and r.levelno == logging.ERROR
               for r in caplog.records)


def test_invalid_json_falls_back(env, providers):
    providers.set(GROQ_URL, GPT_OSS, _response('{"question": "途中で切れた'))
    providers.set(GROQ_URL, GPT_OSS_20B, _response(_quiz_set(VALID_QUIZ)))

    (quiz,) = llm_client.generate_quizzes([ARTICLE])

    assert quiz['question'] == VALID_QUIZ['question']
    # JSON が壊れている場合は同じ候補で再生成せず、次の候補へ
    assert len(providers.calls_to(GROQ_URL, GPT_OSS)) == 1


def test_truncated_output_falls_back(env, providers, caplog):
    # finish_reason=length（max_tokens で途中で切れた）は、JSON として読めても不正扱いで次の候補へ
    providers.set(GROQ_URL, GPT_OSS, _response(_quiz_set(VALID_QUIZ), finish_reason='length'))
    providers.set(GROQ_URL, GPT_OSS_20B, _response(_quiz_set(VALID_QUIZ)))

    with caplog.at_level(logging.INFO, logger='llm_client'):
        (quiz,) = llm_client.generate_quizzes([ARTICLE])

    assert quiz['answer'] == '政策金利'
    assert len(providers.calls_to(GROQ_URL, GPT_OSS)) == 1
    assert len(providers.calls_to(GROQ_URL, GPT_OSS_20B)) == 1
    assert any('finish_reason=length' in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize('broken', [
    {**VALID_QUIZ, 'question': '  '},
    {**VALID_QUIZ, 'choices': VALID_QUIZ['choices'][:3]},
    {**VALID_QUIZ, 'choices': VALID_QUIZ['choices'] + ['5つ目']},
    {**VALID_QUIZ, 'choices': ['政策金利', '政策金利', '消費税', '最低賃金']},
    {**VALID_QUIZ, 'answer_index': 4},
    {**VALID_QUIZ, 'answer_index': -1},
    {**VALID_QUIZ, 'answer_index': True},
    {**VALID_QUIZ, 'answer_index': '0'},
    {k: v for k, v in VALID_QUIZ.items() if k != 'explanation'},
    ['not', 'an', 'object'],
])
def test_single_quiz_validation_failure_falls_back(env, providers, broken):
    # 1問しかなく、それが不正 = 1問もできていないので、再生成せず次の候補へ
    providers.set(GROQ_URL, GPT_OSS, _response(_quiz_set(broken)))
    providers.set(GROQ_URL, GPT_OSS_20B, _response(_quiz_set(VALID_QUIZ)))

    (quiz,) = llm_client.generate_quizzes([ARTICLE])

    assert quiz['answer'] == '政策金利'
    assert len(providers.calls_to(GROQ_URL, GPT_OSS)) == 1
    assert len(providers.calls_to(GROQ_URL, GPT_OSS_20B)) == 1


def test_code_fenced_json_is_accepted(env, providers):
    fenced = '```json\n' + _quiz_set(VALID_QUIZ) + '\n```'
    providers.set(GROQ_URL, GPT_OSS, _response(fenced))

    assert llm_client.generate_quizzes([ARTICLE])[0]['answer'] == '政策金利'
    assert len(providers.calls) == 1


def test_answer_follows_answer_index(env, providers):
    providers.set(GROQ_URL, GPT_OSS, _response(_quiz_set({**VALID_QUIZ, 'answer_index': 2})))

    (quiz,) = llm_client.generate_quizzes([ARTICLE])

    assert quiz['answer_index'] == 2
    assert quiz['answer'] == '最低賃金'


# ---------------------------------------------------------------------------
# 設定とログ
# ---------------------------------------------------------------------------

def test_groq_defaults(monkeypatch, providers):
    monkeypatch.setenv('GROQ_API_KEY', GROQ_KEY)
    for name in ('GROQ_BASE_URL', 'GROQ_MODELS', 'OPENROUTER_API_KEY'):
        monkeypatch.delenv(name, raising=False)

    assert llm_client.configured_providers() == [f'groq:{GPT_OSS}', f'groq:{GPT_OSS_20B}', f'groq:{QWEN}']
    assert {c['base_url'] for c in llm_client.candidates()} == {'https://api.groq.com/openai/v1'}


def test_unknown_groq_model_gets_no_reasoning_params(env, providers, monkeypatch):
    monkeypatch.setenv('GROQ_MODELS', 'some/other-model')
    providers.set(GROQ_URL, 'some/other-model', _response(_quiz_set(VALID_QUIZ)))

    llm_client.generate_quizzes([ARTICLE])

    ((_, _, call),) = providers.calls
    assert 'extra_body' not in call
    assert call['max_tokens'] == 2500


def test_missing_groq_key_goes_straight_to_openrouter(env, providers, monkeypatch):
    monkeypatch.delenv('GROQ_API_KEY')
    providers.set(OPENROUTER_URL, ROUTER_A, _response(_quiz_set(VALID_QUIZ)))

    assert llm_client.generate_quizzes([ARTICLE])[0]['answer'] == '政策金利'
    assert providers.calls_to(GROQ_URL) == []
    assert llm_client.configured_providers() == ['openrouter']


@pytest.mark.parametrize('name, value', [
    ('OPENROUTER_MODELS', ' , '),
    ('OPENROUTER_API_KEY', ''),
])
def test_openrouter_skipped_when_not_configured(env, providers, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    providers.set(GROQ_URL, GPT_OSS, _status_error(openai.RateLimitError, 429))
    providers.set(GROQ_URL, GPT_OSS_20B, _status_error(openai.RateLimitError, 429))
    providers.set(GROQ_URL, QWEN, _status_error(openai.RateLimitError, 429))

    with pytest.raises(QuizGenerationError):
        llm_client.generate_quizzes([ARTICLE])
    assert providers.calls_to(OPENROUTER_URL) == []
    assert llm_client.configured_providers() == [f'groq:{GPT_OSS}', f'groq:{GPT_OSS_20B}', f'groq:{QWEN}']


def test_no_providers_configured(monkeypatch, providers):
    for name in ('GROQ_API_KEY', 'OPENROUTER_API_KEY'):
        monkeypatch.delenv(name, raising=False)

    assert llm_client.configured_providers() == []
    with pytest.raises(QuizGenerationError):
        llm_client.generate_quizzes([ARTICLE])
    assert providers.calls == []


def test_logs_provider_model_and_reason_without_secrets(env, providers, caplog):
    providers.set(GROQ_URL, GPT_OSS, _status_error(openai.RateLimitError, 429))
    providers.set(GROQ_URL, GPT_OSS_20B, _status_error(openai.RateLimitError, 429))
    providers.set(GROQ_URL, QWEN, _status_error(openai.RateLimitError, 429))
    providers.set(OPENROUTER_URL, ROUTER_A, _response(_quiz_set(VALID_QUIZ), model=ROUTER_B))

    with caplog.at_level(logging.DEBUG, logger='llm_client'):
        llm_client.generate_quizzes([ARTICLE])

    text = '\n'.join(r.getMessage() for r in caplog.records)
    assert f'provider=openrouter model={ROUTER_B} count=1' in text
    assert 'rate_limited (429)' in text
    assert GROQ_KEY not in text
    assert OPENROUTER_KEY not in text
    assert ARTICLE not in text


# ---------------------------------------------------------------------------
# 1回の呼び出しで複数問を生成する
# ---------------------------------------------------------------------------

ARTICLES = [f'記事本文その{i}。内容{i}について報じた。' for i in range(1, 6)]


def _quiz_no(i):
    return {
        'question': f'問題{i}',
        'choices': [f'正解{i}', f'誤答{i}a', f'誤答{i}b', f'誤答{i}c'],
        'answer_index': 0,
        'explanation': f'解説{i}',
    }


BROKEN = {'question': '不正', 'choices': ['a', 'b', 'c'], 'answer_index': 0, 'explanation': 'x'}


def _prompt_articles(call):
    """プロンプトに含まれる記事本文（記事番号は振り直される）"""
    prompt = call[2]['messages'][-1]['content']
    return [a for a in ARTICLES if a in prompt]


def test_generates_all_quizzes_in_one_call_in_article_order(env, providers):
    providers.set(GROQ_URL, GPT_OSS, _response(_quiz_set(*[_quiz_no(i) for i in range(1, 6)])))

    quizzes = llm_client.generate_quizzes(ARTICLES)

    assert [q['question'] for q in quizzes] == [f'問題{i}' for i in range(1, 6)]
    assert [q['answer'] for q in quizzes] == [f'正解{i}' for i in range(1, 6)]
    (call,) = providers.calls
    prompt = call[2]['messages'][-1]['content']
    for i, article in enumerate(ARTICLES, start=1):
        assert f'## 記事{i}\n{article}' in prompt
    assert '合計 5 問' in prompt


@pytest.mark.parametrize('content', [
    _quiz_set(*[_quiz_no(i) for i in range(1, 5)]),            # 4問しかない
    _quiz_set(*[_quiz_no(i) for i in range(1, 7)]),            # 6問ある
    json.dumps({'quizzes': 'not a list'}),
    json.dumps(_quiz_no(1)),                                     # 1問だけの旧形式
])
def test_wrong_shape_falls_back_to_next_candidate(env, providers, caplog, content):
    # 問題数が合わないと記事との対応が取れないので、再生成せず次の候補で全問作り直す
    providers.set(GROQ_URL, GPT_OSS, _response(content))
    providers.set(GROQ_URL, GPT_OSS_20B, _response(_quiz_set(*[_quiz_no(i) for i in range(1, 6)])))

    with caplog.at_level(logging.INFO, logger='llm_client'):
        quizzes = llm_client.generate_quizzes(ARTICLES)

    assert len(quizzes) == 5
    assert len(providers.calls_to(GROQ_URL, GPT_OSS)) == 1
    (call,) = providers.calls_to(GROQ_URL, GPT_OSS_20B)
    assert _prompt_articles(call) == ARTICLES
    assert any('invalid_output' in r.getMessage() for r in caplog.records)


def test_one_invalid_quiz_is_regenerated_with_same_candidate(env, providers):
    providers.set(
        GROQ_URL, GPT_OSS,
        _response(_quiz_set(_quiz_no(1), _quiz_no(2), BROKEN, _quiz_no(4), _quiz_no(5))),
        _response(_quiz_set(_quiz_no(3))),
    )

    quizzes = llm_client.generate_quizzes(ARTICLES)

    assert [q['question'] for q in quizzes] == [f'問題{i}' for i in range(1, 6)]
    first, retry = providers.calls_to(GROQ_URL, GPT_OSS)
    assert _prompt_articles(first) == ARTICLES
    # 再送するのは失敗した3問目の記事だけ
    assert _prompt_articles(retry) == [ARTICLES[2]]
    assert '合計 1 問' in retry[2]['messages'][-1]['content']
    assert providers.calls_to(GROQ_URL, GPT_OSS_20B) == []


def test_regeneration_failure_goes_to_next_candidate_with_remaining_articles(env, providers):
    providers.set(
        GROQ_URL, GPT_OSS,
        _response(_quiz_set(_quiz_no(1), BROKEN, _quiz_no(3), BROKEN, _quiz_no(5))),
        _response(_quiz_set(_quiz_no(2), BROKEN)),   # 再生成で2問目だけ直った
    )
    providers.set(GROQ_URL, GPT_OSS_20B, _response(_quiz_set(_quiz_no(4))))

    quizzes = llm_client.generate_quizzes(ARTICLES)

    assert [q['question'] for q in quizzes] == [f'問題{i}' for i in range(1, 6)]
    first, retry = providers.calls_to(GROQ_URL, GPT_OSS)   # 同じ候補での再生成は1回まで
    assert _prompt_articles(retry) == [ARTICLES[1], ARTICLES[3]]
    (fallback,) = providers.calls_to(GROQ_URL, GPT_OSS_20B)
    assert _prompt_articles(fallback) == [ARTICLES[3]]


def test_regeneration_api_error_goes_to_next_candidate(env, providers):
    providers.set(
        GROQ_URL, GPT_OSS,
        _response(_quiz_set(_quiz_no(1), _quiz_no(2), _quiz_no(3), _quiz_no(4), BROKEN)),
        _status_error(openai.RateLimitError, 429),
    )
    providers.set(GROQ_URL, GPT_OSS_20B, _response(_quiz_set(_quiz_no(5))))

    quizzes = llm_client.generate_quizzes(ARTICLES)

    assert [q['question'] for q in quizzes] == [f'問題{i}' for i in range(1, 6)]
    (fallback,) = providers.calls_to(GROQ_URL, GPT_OSS_20B)
    assert _prompt_articles(fallback) == [ARTICLES[4]]


def test_partial_failures_everywhere_raise(env, providers):
    partial = _response(_quiz_set(_quiz_no(1), BROKEN, _quiz_no(3), _quiz_no(4), _quiz_no(5)))
    providers.set(GROQ_URL, GPT_OSS, partial, _response(_quiz_set(BROKEN)))
    providers.set(GROQ_URL, GPT_OSS_20B, _response(_quiz_set(BROKEN)))
    providers.set(GROQ_URL, QWEN, _response(_quiz_set(BROKEN)))
    providers.set(OPENROUTER_URL, ROUTER_A, _response(_quiz_set(BROKEN)))

    with pytest.raises(QuizGenerationError) as excinfo:
        llm_client.generate_quizzes(ARTICLES)

    assert 'invalid_output' in str(excinfo.value)
    # 1問もできなかった候補では再生成しない
    assert len(providers.calls_to(GROQ_URL, GPT_OSS_20B)) == 1
    assert len(providers.calls_to(GROQ_URL, QWEN)) == 1
    assert len(providers.calls_to(OPENROUTER_URL)) == 1


def test_empty_article_list_is_rejected(env, providers):
    with pytest.raises(ValueError):
        llm_client.generate_quizzes([])
    assert providers.calls == []


def test_article_text_with_braces_is_kept_verbatim(env, providers):
    article = '記事中の {波括弧} と {n} はそのまま渡す'
    providers.set(GROQ_URL, GPT_OSS, _response(_quiz_set(VALID_QUIZ)))

    llm_client.generate_quizzes([article])

    (call,) = providers.calls
    assert article in call[2]['messages'][-1]['content']


# ---------------------------------------------------------------------------
# 記事の切り詰め（プロンプトだけ）
# ---------------------------------------------------------------------------

def test_truncate_keeps_short_text():
    assert llm_client.truncate_article('短い記事') == '短い記事'
    exact = 'あ' * 500
    assert llm_client.truncate_article(exact) == exact


def test_truncate_cuts_at_last_period_within_limit():
    text = 'あ' * 300 + '。' + 'い' * 150 + '。' + 'う' * 100 + '。'
    assert llm_client.truncate_article(text) == 'あ' * 300 + '。' + 'い' * 150 + '。'


def test_truncate_without_period_cuts_at_limit():
    assert llm_client.truncate_article('あ' * 800) == 'あ' * 500


def test_prompt_uses_truncated_article(env, providers):
    long_article = '前半の文。' * 150   # 750文字
    providers.set(GROQ_URL, GPT_OSS, _response(_quiz_set(VALID_QUIZ)))

    llm_client.generate_quizzes([long_article])

    (call,) = providers.calls
    prompt = call[2]['messages'][-1]['content']
    assert '前半の文。' * 100 in prompt
    assert '前半の文。' * 101 not in prompt


def test_prompt_asks_for_uneven_numeric_choices(env, providers):
    providers.set(GROQ_URL, GPT_OSS, _response(_quiz_set(VALID_QUIZ)))

    llm_client.generate_quizzes([ARTICLE])

    (call,) = providers.calls
    assert '数値の選択肢は等間隔にせず' in call[2]['messages'][-1]['content']


def test_prompt_forbids_article_numbers_in_questions(env, providers):
    # プレイヤーには1ラウンドに1記事しか表示されないため、「記事3で」のような表現は意味をなさない
    providers.set(GROQ_URL, GPT_OSS, _response(_quiz_set(VALID_QUIZ)))

    llm_client.generate_quizzes([ARTICLE])

    (call,) = providers.calls
    prompt = call[2]['messages'][-1]['content']
    assert '問題文・解説に記事番号や「記事3で」' in prompt
    assert '記事を指す表現を含めない' in prompt
