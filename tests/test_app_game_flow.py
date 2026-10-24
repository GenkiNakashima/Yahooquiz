"""
ゲームの流れ（/game → /quiz × 5 → /result）のテスト。
LLM・記事取得・Firestore はモックに差し替える。
"""
import importlib
import uuid

import pytest

from llm_client import QuizGenerationError

ARTICLE_BODY = '実際の記事を想定した長い本文。' * 140  # 約2000文字


def _quiz(i):
    return {
        'question': f'第{i}問の問題文',
        'choices': [f'正解{i}', f'誤答{i}a', f'誤答{i}b', f'誤答{i}c'],
        'answer_index': 0,
        'answer': f'正解{i}',
        'explanation': f'第{i}問の解説',
        'article_content': ARTICLE_BODY,
        'article_url': f'https://news.example/{i}',
        'article_title': f'記事タイトル{i}',
    }


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
def game(app_module, monkeypatch):
    """Firestore の代わりに辞書へ保存し、クイズ生成の呼び出し（問題数・ジャンル）を記録する"""
    state = {'store': {}, 'metadata': {}, 'results': {}, 'generate_calls': [], 'generate_error': None,
             'save_error': None, 'get_error': None, 'record_error': None}

    def create_quiz_set(count, genre='business'):
        state['generate_calls'].append((count, genre))
        if state['generate_error']:
            raise state['generate_error']
        return [_quiz(i) for i in range(1, count + 1)]

    def save_quiz_set(quizzes, metadata=None):
        if state['save_error']:
            raise state['save_error']
        quiz_set_id = str(uuid.uuid4())
        state['store'][quiz_set_id] = quizzes
        state['metadata'][quiz_set_id] = metadata
        state['results'][quiz_set_id] = {}
        return quiz_set_id

    def record_answer(quiz_set_id, index, result):
        # 本物と同じく、同じラウンドは最初の結果を採用する
        if state['record_error']:
            raise state['record_error']
        results = state['results'][quiz_set_id]
        results.setdefault(str(index), result)
        return results[str(index)], dict(results)

    def get_quiz(quiz_set_id, index):
        if state['get_error']:
            raise state['get_error']
        quizzes = state['store'].get(quiz_set_id)
        return quizzes[index] if quizzes and 0 <= index < len(quizzes) else None

    monkeypatch.setattr(app_module.quiz_generator, 'create_quiz_set', create_quiz_set)
    monkeypatch.setattr(app_module, 'save_quiz_set', save_quiz_set)
    monkeypatch.setattr(app_module, 'get_quiz', get_quiz)
    monkeypatch.setattr(app_module, 'record_answer', record_answer)
    state['client'] = app_module.app.test_client()
    return state


def test_full_game_generates_once_and_keeps_cookie_small(game):
    client = game['client']

    response = client.post('/game', data={'ai_level': 'normal'})
    assert response.status_code == 302 and response.headers['Location'].endswith('/quiz')
    assert game['generate_calls'] == [(5, 'business')]

    cookie = client.get_cookie('session')
    assert len(cookie.value) < 1000
    with client.session_transaction() as sess:
        assert sess['quiz_set_id'] in game['store']
        assert 'current_quiz' not in sess

    for i in range(1, 6):
        page = client.get('/quiz').get_data(as_text=True)
        assert f'第{i}問の問題文' in page
        assert f'記事タイトル{i}' in page
        # 回答はプレイヤーが先に押した扱いにする（AI は最短でも6秒）
        response = client.post('/quiz', data={'time': '1', 'answer': f'正解{i}'})
        if i < 5:
            assert response.status_code == 200
        else:
            assert response.status_code == 302 and response.headers['Location'].endswith('/result')

    with client.session_transaction() as sess:
        assert sess['score'] == {'player': 5, 'ai': 0}
    # 5ラウンド遊んでも LLM 呼び出しはゲーム開始時の1回だけ
    assert game['generate_calls'] == [(5, 'business')]
    # 終了後に /quiz を開いてもトップへ戻る
    assert client.get('/quiz').status_code == 302


def test_reloading_quiz_page_shows_same_question(game):
    client = game['client']
    client.post('/game', data={'ai_level': 'normal'})

    first = client.get('/quiz').get_data(as_text=True)
    second = client.get('/quiz').get_data(as_text=True)

    assert '第1問の問題文' in first and '第1問の問題文' in second
    assert game['generate_calls'] == [(5, 'business')]


def test_generation_failure_shows_message(game):
    game['generate_error'] = QuizGenerationError('all failed')

    response = game['client'].post('/game', data={'ai_level': 'normal'})

    assert response.status_code == 503
    assert '現在クイズを生成できません' in response.get_data(as_text=True)
    with game['client'].session_transaction() as sess:
        assert 'quiz_set_id' not in sess


def test_storage_failure_on_start_shows_message(game):
    game['save_error'] = RuntimeError('db down')

    response = game['client'].post('/game', data={'ai_level': 'normal'})

    assert response.status_code == 503
    assert 'データベースに接続できません' in response.get_data(as_text=True)


def test_storage_failure_during_game_shows_message(game):
    client = game['client']
    client.post('/game', data={'ai_level': 'normal'})
    game['get_error'] = RuntimeError('db down')

    response = client.get('/quiz')

    assert response.status_code == 503
    assert 'データベースに接続できません' in response.get_data(as_text=True)


def test_expired_quiz_set_redirects_to_top(game):
    client = game['client']
    client.post('/game', data={'ai_level': 'normal'})
    game['store'].clear()

    response = client.get('/quiz')

    assert response.status_code == 302 and response.headers['Location'].endswith('/')


def test_quiz_without_game_redirects_to_top(game):
    assert game['client'].get('/quiz').status_code == 302


def test_selected_genre_is_used_for_generation(game):
    client = game['client']
    client.post('/genre', data={'genre': 'sports'})

    client.post('/game', data={'ai_level': 'normal'})

    assert game['generate_calls'] == [(5, 'sports')]


def test_game_metadata_is_saved_for_match_recording(game):
    client = game['client']
    client.post('/genre', data={'genre': 'it'})

    client.post('/game', data={'ai_level': 'strong'})

    (metadata,) = game['metadata'].values()
    assert metadata == {'ai_level': 'strong', 'genre': 'it'}


def test_answers_are_recorded_on_server(game):
    client = game['client']
    client.post('/game', data={'ai_level': 'normal'})
    client.get('/quiz')
    client.post('/quiz', data={'time': '1', 'answer': '不正解の回答'})
    client.get('/quiz')
    client.post('/quiz', data={'time': '1', 'answer': '正解2'})

    (results,) = game['results'].values()
    assert results == {'0': 'wrong', '1': 'correct'}
    with client.session_transaction() as sess:
        assert sess['score'] == {'player': 1, 'ai': 0}


def test_replaying_an_old_cookie_cannot_change_an_answer(game):
    """正解を見た後に回答前の Cookie を送り直しても、最初の回答（不正解）が採用される"""
    client = game['client']
    client.post('/game', data={'ai_level': 'normal'})
    client.get('/quiz')
    cookie_before_answer = client.get_cookie('session').value

    client.post('/quiz', data={'time': '1', 'answer': '不正解の回答'})
    client.set_cookie('session', cookie_before_answer)
    page = client.post('/quiz', data={'time': '1', 'answer': '正解1'}).get_data(as_text=True)

    (results,) = game['results'].values()
    assert results == {'0': 'wrong'}
    with client.session_transaction() as sess:
        assert sess['score'] == {'player': 0, 'ai': 0}
    # 画面にも最初の結果（不正解）が表示される
    assert 'result-status wrong' in page and 'result-status correct' not in page


def test_storage_failure_while_answering_shows_message(game):
    client = game['client']
    client.post('/game', data={'ai_level': 'normal'})
    client.get('/quiz')
    game['record_error'] = RuntimeError('db down')

    response = client.post('/quiz', data={'time': '1', 'answer': '正解1'})

    assert response.status_code == 503
    with client.session_transaction() as sess:
        assert sess['round'] == 0


def _start(game, quizzes=None):
    client = game['client']
    client.post('/game', data={'ai_level': 'normal'})
    if quizzes is not None:
        (quiz_set_id,) = game['store']
        game['store'][quiz_set_id] = quizzes
    return client


def test_quiz_page_shows_four_choice_buttons_without_revealing_the_answer_index(game):
    quiz = {**_quiz(1), 'choices': ['誤答A', '誤答B', '正解1', '誤答C'], 'answer_index': 2}
    client = _start(game, [quiz] * 5)

    page = client.get('/quiz').get_data(as_text=True)

    assert page.count('class="choice-btn"') == 4
    for i, text in enumerate(quiz['choices']):
        assert f'name="choice" value="{i}"' in page and text in page
    assert 'answer_index' not in page
    assert 'name="answer"' not in page


@pytest.mark.parametrize('choice, expected', [
    ('2', 'correct'),
    ('0', 'wrong'),
    ('9', 'wrong'),
    ('abc', 'wrong'),
    ('', 'wrong'),
])
def test_choice_is_judged_by_index(game, choice, expected):
    quiz = {**_quiz(1), 'choices': ['誤答A', '誤答B', '正解1', '誤答C'], 'answer_index': 2}
    client = _start(game, [quiz] * 5)
    client.get('/quiz')

    client.post('/quiz', data={'time': '1', 'choice': choice})

    (results,) = game['results'].values()
    assert results == {'0': expected}


def test_quiz_without_choices_falls_back_to_text_answer(game):
    quiz = {k: v for k, v in _quiz(1).items() if k not in ('choices', 'answer_index')}
    client = _start(game, [quiz] * 5)

    page = client.get('/quiz').get_data(as_text=True)
    client.post('/quiz', data={'time': '1', 'answer': '正解1'})

    assert 'name="answer"' in page and 'name="choice"' not in page
    (results,) = game['results'].values()
    assert results == {'0': 'correct'}


def test_empty_text_answer_is_wrong(game):
    quiz = {k: v for k, v in _quiz(1).items() if k not in ('choices', 'answer_index')}
    client = _start(game, [{**quiz, 'answer': ''}] * 5)
    client.get('/quiz')

    client.post('/quiz', data={'time': '1', 'answer': ''})

    (results,) = game['results'].values()
    assert results == {'0': 'wrong'}
