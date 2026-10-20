"""llm_client のテスト（実際の API は呼ばず、OpenAI クライアントをモックする）"""
import json
import logging
from types import SimpleNamespace

import httpx2
import openai
import pytest

import llm_client
from llm_client import QuizGenerationError

CEREBRAS_URL = 'https://cerebras.test/v1'
OPENROUTER_URL = 'https://openrouter.test/api/v1'
CEREBRAS_KEY = 'sk-cerebras-secret'
OPENROUTER_KEY = 'sk-openrouter-secret'
ARTICLE = '日本銀行は本日、政策金利を0.1%引き上げることを発表しました。'

VALID_QUIZ = {
    'question': '日本銀行が引き上げを発表したのは何？',
    'choices': ['政策金利', '消費税', '最低賃金', '国債発行額'],
    'answer_index': 0,
    'explanation': '日本銀行は政策金利を0.1%引き上げると発表しました。',
}


def _response(content, model='served-model'):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        model=model,
    )


def _status_error(cls, status):
    request = httpx2.Request('POST', 'https://example.test/v1/chat/completions')
    return cls(f'HTTP {status}', response=httpx2.Response(status, request=request), body=None)


class FakeClient:
    """chat.completions.create の呼び出しを記録し、指定された結果を返す／例外を投げる"""

    def __init__(self, result, init_kwargs):
        self.result = result
        self.init_kwargs = init_kwargs
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def env(monkeypatch):
    values = {
        'CEREBRAS_API_KEY': CEREBRAS_KEY,
        'CEREBRAS_BASE_URL': CEREBRAS_URL,
        'CEREBRAS_MODEL': 'cerebras-test-model',
        'OPENROUTER_API_KEY': OPENROUTER_KEY,
        'OPENROUTER_BASE_URL': OPENROUTER_URL,
        'OPENROUTER_MODELS': 'vendor/model-a:free, vendor/model-b:free',
        'LLM_TIMEOUT_SECONDS': '12',
    }
    for k, v in values.items():
        monkeypatch.setenv(k, v)
    return values


@pytest.fixture
def providers(monkeypatch):
    """
    base_url ごとの応答を設定するとモックの OpenAI クライアントが返される。
    戻り値の dict に、生成された FakeClient が 'clients' として記録される。
    """
    state = {'results': {}, 'clients': {}}

    def fake_openai(**kwargs):
        base_url = kwargs['base_url']
        client = FakeClient(state['results'][base_url], kwargs)
        state['clients'][base_url] = client
        return client

    monkeypatch.setattr(llm_client, 'OpenAI', fake_openai)
    return state


def _calls(providers, base_url):
    client = providers['clients'].get(base_url)
    return client.calls if client else []


# ---------------------------------------------------------------------------
# 要求されたケース
# ---------------------------------------------------------------------------

def test_cerebras_success(env, providers):
    providers['results'][CEREBRAS_URL] = _response(json.dumps(VALID_QUIZ, ensure_ascii=False))

    quiz = llm_client.generate_quiz(ARTICLE)

    assert quiz == {**VALID_QUIZ, 'answer': '政策金利'}
    assert _calls(providers, OPENROUTER_URL) == []

    client = providers['clients'][CEREBRAS_URL]
    assert client.init_kwargs['api_key'] == CEREBRAS_KEY
    assert client.init_kwargs['max_retries'] == 0
    assert client.init_kwargs['timeout'] == 12.0
    (call,) = client.calls
    assert call['model'] == 'cerebras-test-model'
    assert call['extra_body'] == {'reasoning_effort': 'none'}
    assert call['max_tokens'] == 4000
    assert call['response_format'] == {'type': 'json_object'}
    assert ARTICLE in call['messages'][-1]['content']


def test_cerebras_429_falls_back_to_openrouter_without_retry(env, providers):
    providers['results'][CEREBRAS_URL] = _status_error(openai.RateLimitError, 429)
    providers['results'][OPENROUTER_URL] = _response(json.dumps(VALID_QUIZ), model='vendor/model-b:free')

    quiz = llm_client.generate_quiz(ARTICLE)

    assert quiz['answer'] == '政策金利'
    # 待機・リトライせずに1回だけ呼んでフォールバックする
    assert len(_calls(providers, CEREBRAS_URL)) == 1

    client = providers['clients'][OPENROUTER_URL]
    assert client.init_kwargs['api_key'] == OPENROUTER_KEY
    assert client.init_kwargs['max_retries'] == 0
    assert client.init_kwargs['default_headers']['HTTP-Referer']
    assert client.init_kwargs['default_headers']['X-Title'] == 'Yahooquiz'
    (call,) = client.calls
    assert call['model'] == 'vendor/model-a:free'
    assert call['extra_body'] == {'models': ['vendor/model-a:free', 'vendor/model-b:free']}
    assert call['response_format'] == {'type': 'json_object'}
    assert 'reasoning_effort' not in call['extra_body']


def test_cerebras_invalid_json_falls_back_to_openrouter(env, providers):
    providers['results'][CEREBRAS_URL] = _response('{"question": "途中で切れた')
    providers['results'][OPENROUTER_URL] = _response(json.dumps(VALID_QUIZ))

    quiz = llm_client.generate_quiz(ARTICLE)

    assert quiz['question'] == VALID_QUIZ['question']
    assert len(_calls(providers, OPENROUTER_URL)) == 1


def test_both_providers_fail_raises(env, providers):
    providers['results'][CEREBRAS_URL] = _status_error(openai.RateLimitError, 429)
    providers['results'][OPENROUTER_URL] = _status_error(openai.InternalServerError, 503)

    with pytest.raises(QuizGenerationError) as excinfo:
        llm_client.generate_quiz(ARTICLE)

    assert 'rate_limited (429)' in str(excinfo.value)
    assert 'server_error (503)' in str(excinfo.value)


# ---------------------------------------------------------------------------
# フォールバック条件
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('error, reason', [
    (_status_error(openai.InternalServerError, 500), 'server_error (500)'),
    (_status_error(openai.NotFoundError, 404), 'model_not_found (404)'),
    (openai.APITimeoutError(request=httpx2.Request('POST', CEREBRAS_URL)), 'timeout'),
    (openai.APIConnectionError(request=httpx2.Request('POST', CEREBRAS_URL)), 'connection_error'),
])
def test_cerebras_transient_errors_fall_back(env, providers, caplog, error, reason):
    providers['results'][CEREBRAS_URL] = error
    providers['results'][OPENROUTER_URL] = _response(json.dumps(VALID_QUIZ))

    with caplog.at_level(logging.INFO, logger='llm_client'):
        llm_client.generate_quiz(ARTICLE)

    assert any(reason in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)


@pytest.mark.parametrize('cls, status', [
    (openai.AuthenticationError, 401),
    (openai.PermissionDeniedError, 403),
    (openai.BadRequestError, 400),
])
def test_cerebras_client_errors_fall_back_with_error_log(env, providers, caplog, cls, status):
    providers['results'][CEREBRAS_URL] = _status_error(cls, status)
    providers['results'][OPENROUTER_URL] = _response(json.dumps(VALID_QUIZ))

    with caplog.at_level(logging.INFO, logger='llm_client'):
        llm_client.generate_quiz(ARTICLE)

    assert any(f'client_error ({status})' in r.getMessage() and r.levelno == logging.ERROR
               for r in caplog.records)


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
def test_validation_failure_falls_back(env, providers, broken):
    providers['results'][CEREBRAS_URL] = _response(json.dumps(broken, ensure_ascii=False))
    providers['results'][OPENROUTER_URL] = _response(json.dumps(VALID_QUIZ))

    quiz = llm_client.generate_quiz(ARTICLE)

    assert quiz['answer'] == '政策金利'
    assert len(_calls(providers, OPENROUTER_URL)) == 1


def test_code_fenced_json_is_accepted(env, providers):
    fenced = '```json\n' + json.dumps(VALID_QUIZ) + '\n```'
    providers['results'][CEREBRAS_URL] = _response(fenced)

    assert llm_client.generate_quiz(ARTICLE)['answer'] == '政策金利'
    assert _calls(providers, OPENROUTER_URL) == []


def test_answer_follows_answer_index(env, providers):
    providers['results'][CEREBRAS_URL] = _response(json.dumps({**VALID_QUIZ, 'answer_index': 2}))

    quiz = llm_client.generate_quiz(ARTICLE)

    assert quiz['answer_index'] == 2
    assert quiz['answer'] == '最低賃金'


# ---------------------------------------------------------------------------
# 設定とログ
# ---------------------------------------------------------------------------

def test_missing_cerebras_key_goes_straight_to_openrouter(env, providers, monkeypatch):
    monkeypatch.delenv('CEREBRAS_API_KEY')
    providers['results'][OPENROUTER_URL] = _response(json.dumps(VALID_QUIZ))

    assert llm_client.generate_quiz(ARTICLE)['answer'] == '政策金利'
    assert CEREBRAS_URL not in providers['clients']
    assert llm_client.configured_providers() == ['openrouter']


def test_empty_openrouter_models_disables_fallback(env, providers, monkeypatch):
    monkeypatch.setenv('OPENROUTER_MODELS', ' , ')
    providers['results'][CEREBRAS_URL] = _status_error(openai.RateLimitError, 429)

    with pytest.raises(QuizGenerationError):
        llm_client.generate_quiz(ARTICLE)
    assert OPENROUTER_URL not in providers['clients']
    assert llm_client.configured_providers() == ['cerebras']


def test_no_providers_configured(monkeypatch, providers):
    for name in ('CEREBRAS_API_KEY', 'OPENROUTER_API_KEY'):
        monkeypatch.delenv(name, raising=False)

    assert llm_client.configured_providers() == []
    with pytest.raises(QuizGenerationError):
        llm_client.generate_quiz(ARTICLE)


def test_logs_provider_model_and_reason_without_secrets(env, providers, caplog):
    providers['results'][CEREBRAS_URL] = _status_error(openai.RateLimitError, 429)
    providers['results'][OPENROUTER_URL] = _response(json.dumps(VALID_QUIZ), model='vendor/model-b:free')

    with caplog.at_level(logging.DEBUG, logger='llm_client'):
        llm_client.generate_quiz(ARTICLE)

    text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'provider=openrouter model=vendor/model-b:free' in text
    assert 'rate_limited (429)' in text
    assert CEREBRAS_KEY not in text
    assert OPENROUTER_KEY not in text
    assert ARTICLE not in text
