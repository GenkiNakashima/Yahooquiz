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
    mp.setenv('SECRET_KEY', 'test-secret-key')
    mp.setenv('GROQ_API_KEY', 'test-key')
    mp.setenv('GROQ_BASE_URL', 'https://groq.test/openai/v1')
    mp.setenv('GROQ_MODELS', 'test-model')
    module = importlib.import_module('app')
    module.app.config['TESTING'] = True
    yield module
    mp.undo()


@pytest.fixture
def game(app_module, monkeypatch):
    """Firestore の代わりに辞書へ保存し、クイズ生成の呼び出し（問題数・ジャンル）を記録する"""
    state = {'store': {}, 'metadata': {}, 'results': {}, 'answers': {}, 'generate_calls': [], 'generate_error': None,
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

    def record_answer(quiz_set_id, index, result, answer_text=None):
        # 本物と同じく、同じラウンドは最初の結果を採用する
        if state['record_error']:
            raise state['record_error']
        results = state['results'][quiz_set_id]
        if str(index) not in results and answer_text is not None:
            state['answers'].setdefault(quiz_set_id, {})[str(index)] = answer_text
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

    def get_quiz_set(quiz_set_id):
        if state['get_error']:
            raise state['get_error']
        if quiz_set_id not in state['store']:
            return None
        return {'quizzes': state['store'][quiz_set_id], 'results': dict(state['results'][quiz_set_id])}

    monkeypatch.setattr(app_module, 'get_quiz_set', get_quiz_set)
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


# ---------------------------------------------------------------------------
# 記述式の問題
# ---------------------------------------------------------------------------

def _text_quiz(i=1, answer='7680億円', answer_type='number', aliases=None):
    return {**_quiz(i), 'choices': ['7680億円', '5000億円', '1兆円', '3200億円'], 'answer_index': 0,
            'answer': answer, 'answer_type': answer_type, 'aliases': aliases or [], 'answer_format': 'text'}


def test_text_question_shows_input_and_hint_without_choices(game):
    client = _start(game, [_text_quiz()] * 5)

    page = client.get('/quiz').get_data(as_text=True)

    assert 'name="answer"' in page and 'name="choice"' not in page
    assert '数字のみでOK・単位は不要です（例: 3万、30000 どちらも可）' in page
    # 選択肢（正解を含む）は HTML に出さない
    for choice in _text_quiz()['choices']:
        assert choice not in page


def test_name_question_shows_name_hint(game):
    client = _start(game, [_text_quiz(answer='公正取引委員会', answer_type='name')] * 5)

    page = client.get('/quiz').get_data(as_text=True)

    assert '「株式会社」や、人名の敬称・肩書' in page


@pytest.mark.parametrize('answer, expected', [
    ('7,680億', 'correct'),
    ('768000000000円', 'correct'),
    ('約７６８０億', 'correct'),
    ('7000億', 'wrong'),
    ('', 'wrong'),
])
def test_text_answer_is_judged_on_the_server_with_normalization(game, answer, expected):
    client = _start(game, [_text_quiz()] * 5)
    client.get('/quiz')

    client.post('/quiz', data={'time': '1', 'answer': answer})

    (results,) = game['results'].values()
    assert results == {'0': expected}
    # 入力した回答は判定の調整用に記録する
    (answers,) = game['answers'].values()
    assert answers == {'0': answer}


def test_choice_index_is_not_accepted_for_a_text_question(game):
    """記述式の問題に、正解の選択肢の番号を送っても正解にならない"""
    client = _start(game, [_text_quiz()] * 5)
    client.get('/quiz')

    client.post('/quiz', data={'time': '1', 'choice': '0'})

    (results,) = game['results'].values()
    assert results == {'0': 'wrong'}


def test_aliases_are_accepted_and_shown_after_answering(game):
    quiz = _text_quiz(answer='公正取引委員会', answer_type='name', aliases=['公取委', 'JFTC'])
    client = _start(game, [quiz] * 5)
    client.get('/quiz')

    page = client.post('/quiz', data={'time': '1', 'answer': 'jftc'}).get_data(as_text=True)

    (results,) = game['results'].values()
    assert results == {'0': 'correct'}
    assert '公正取引委員会' in page
    assert 'この表記でも正解: 公取委、JFTC' in page


def test_correct_answer_is_shown_when_ai_answers_a_text_question(game, app_module, monkeypatch):
    calls = []

    def simulate_ai_answer(level='normal', answer_format='choice'):
        calls.append((level, answer_format))
        return True

    monkeypatch.setattr(app_module.quiz_generator, 'simulate_ai_answer', simulate_ai_answer)
    quiz = _text_quiz(answer='公正取引委員会', answer_type='name', aliases=['公取委'])
    client = _start(game, [quiz] * 5)
    client.get('/quiz')

    # AI が先に押した扱いにする（プレイヤーの時間を AI の最長より遅くする）
    page = client.post('/quiz', data={'time': '999'}).get_data(as_text=True)

    assert calls == [('normal', 'text')]
    assert game['answers'] == {}
    assert '公正取引委員会' in page and 'この表記でも正解: 公取委' in page


def test_ai_uses_four_choice_rate_for_choice_questions(game, app_module, monkeypatch):
    calls = []
    monkeypatch.setattr(app_module.quiz_generator, 'simulate_ai_answer',
                        lambda level='normal', answer_format='choice': calls.append(answer_format) or False)
    client = _start(game, [{**_quiz(1), 'answer_format': 'choice'}] * 5)
    client.get('/quiz')

    client.post('/quiz', data={'time': '999'})

    assert calls == ['choice']


def test_choice_question_requires_a_choice_index(game):
    """answer_format が choice の問題は選択肢の番号だけで判定する（正解の文字列を送っても正解にしない）"""
    client = _start(game, [{**_quiz(1), 'answer_format': 'choice'}] * 5)
    client.get('/quiz')

    page = client.post('/quiz', data={'time': '1', 'answer': '正解1'}).get_data(as_text=True)

    (results,) = game['results'].values()
    assert results == {'0': 'wrong'}
    # 4択の問題では別表記は表示しない
    assert 'この表記でも正解' not in page


def test_text_input_has_maxlength(game):
    client = _start(game, [_text_quiz()] * 5)

    page = client.get('/quiz').get_data(as_text=True)

    assert 'maxlength="50"' in page


@pytest.mark.parametrize('length, expected, stored', [
    (50, 'correct', True),
    (51, 'wrong', False),
    (500, 'wrong', False),
])
def test_text_answer_length_limit(game, length, expected, stored):
    """50文字を超える回答は、正規化・保存の前に不正解にする（ゲームは止めない）"""
    answer = 'あ' * length
    client = _start(game, [_text_quiz(answer='あ' * 50, answer_type='name')] * 5)
    client.get('/quiz')

    response = client.post('/quiz', data={'time': '1', 'answer': answer})

    assert response.status_code == 200
    (results,) = game['results'].values()
    assert results == {'0': expected}
    assert game['answers'] == ({next(iter(game['results'])): {'0': answer}} if stored else {})


def test_overlong_answer_is_rejected_before_normalization(game, app_module, monkeypatch):
    calls = []
    monkeypatch.setattr(app_module.answer_checker, 'is_correct', lambda *a, **k: calls.append(a) or True)
    client = _start(game, [_text_quiz()] * 5)
    client.get('/quiz')

    client.post('/quiz', data={'time': '1', 'answer': '7680億円' + ' ' * 50})

    assert calls == []
    (results,) = game['results'].values()
    assert results == {'0': 'wrong'}



# ---------------------------------------------------------------------------
# 結果画面の正解一覧
# ---------------------------------------------------------------------------

def _play_all(client, rounds=5):
    for _ in range(rounds):
        client.get('/quiz')
        client.post('/quiz', data={'time': '1', 'answer': 'x', 'choice': '1'})


def test_result_lists_every_answer_including_the_last_round(game):
    quizzes = [{**_quiz(1), 'answer_format': 'choice'},
               _text_quiz(2, answer='公正取引委員会', answer_type='name', aliases=['公取委', 'JFTC']),
               {**_quiz(3), 'answer_format': 'choice'},
               _text_quiz(4, answer='7680億円'),
               _text_quiz(5, answer='ソフトバンクグループ', answer_type='name', aliases=['SoftBank Group'])]
    client = _start(game, quizzes)
    _play_all(client)

    page = client.get('/result').get_data(as_text=True)

    assert 'class="review-card"' in page
    for i, answer in enumerate(['正解1', '公正取引委員会', '正解3', '7680億円', 'ソフトバンクグループ'], start=1):
        assert f'第{i}問の問題文' in page and answer in page
    assert 'この表記でも正解: 公取委、JFTC' in page
    assert 'この表記でも正解: SoftBank Group' in page
    assert page.count('この表記でも正解') == 2


def test_result_does_not_show_aliases_of_four_choice_questions(game):
    client = _start(game, [{**_quiz(1), 'answer_format': 'choice', 'aliases': ['別表記']}] * 5)
    _play_all(client)

    page = client.get('/result').get_data(as_text=True)

    assert 'class="review-card"' in page and 'この表記でも正解' not in page


def test_result_page_opened_mid_game_does_not_reveal_answers(game):
    client = _start(game, [_text_quiz(i, answer=f'答え{i}', answer_type='name') for i in range(1, 6)])
    _play_all(client, rounds=2)

    page = client.get('/result').get_data(as_text=True)

    assert 'class="review-card"' not in page
    assert '答え3' not in page and '答え5' not in page


def test_result_page_still_renders_when_answers_cannot_be_loaded(game):
    client = _start(game)
    _play_all(client)
    game['get_error'] = RuntimeError('firestore down')

    response = client.get('/result')

    assert response.status_code == 200
    assert 'class="review-card"' not in response.get_data(as_text=True)
