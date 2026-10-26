"""honban（記事取得とクイズセット生成）のテスト。ネットワークには接続しない"""
import sys
from types import SimpleNamespace

import pytest

import honban
from honban import GENRE_URLS, QuizGenerator, SAMPLE_ARTICLES


def _page(html):
    return SimpleNamespace(text=html, raise_for_status=lambda: None)


def _list_page(n):
    links = ''.join(f'<li><a href="https://pickup.test/{i}">見出し{i}</a></li>' for i in range(1, n + 1))
    # 同じリンクが重複していても1件として扱われることを確認するため、1件目を重複させる
    links += '<li><a href="https://pickup.test/1">見出し1（重複）</a></li>'
    return _page(f'<ul class="newsFeed_list">{links}</ul>')


def _pickup_page(i):
    return _page(f'<a data-ual-gotocontent="true" href="https://full.test/{i}">続きを読む</a>')


def _full_page(i):
    return _page(f'<article><header><h1>タイトル{i}</h1></header></article>'
                 f'<div class="article_body"><p>本文{i}</p></div>')


@pytest.fixture
def fake_web(monkeypatch):
    """requests.get を差し替える。failing に入れた番号の記事は取得に失敗する"""
    state = {'calls': [], 'failing': set(), 'list_links': 10, 'list_error': None}

    def fake_get(url, headers=None, timeout=None):
        state['calls'].append(url)
        if url in GENRE_URLS.values():
            if state['list_error']:
                raise state['list_error']
            return _list_page(state['list_links'])
        kind, i = url.split('/')[2], int(url.rsplit('/', 1)[1])
        if i in state['failing']:
            raise ConnectionError('boom')
        return _pickup_page(i) if kind == 'pickup.test' else _full_page(i)

    monkeypatch.setattr(honban.requests, 'get', fake_get)
    # リトライ・アクセス間隔の待機は行わない
    monkeypatch.setattr(honban.time, 'sleep', lambda seconds: None)
    return state


def _list_calls(state):
    return [u for u in state['calls'] if u in GENRE_URLS.values()]


def test_fetches_requested_number_of_articles_with_single_list_request(fake_web):
    articles = QuizGenerator().get_news_articles(5)

    assert len(articles) == 5
    assert all(a['content'].startswith('本文') for a in articles)
    assert all(a['title'].startswith('タイトル') for a in articles)
    assert len({a['url'] for a in articles}) == 5
    assert _list_calls(fake_web) == [GENRE_URLS['business']]


def test_uses_genre_list_page(fake_web):
    QuizGenerator().get_news_articles(5, genre='sports')

    assert _list_calls(fake_web) == [GENRE_URLS['sports']]


def test_unknown_genre_falls_back_to_business(fake_web):
    QuizGenerator().get_news_articles(5, genre='no-such-genre')

    assert _list_calls(fake_web) == [GENRE_URLS['business']]


def test_failed_articles_are_replaced_with_other_links(fake_web):
    fake_web['failing'] = {1, 2, 3}

    articles = QuizGenerator().get_news_articles(5)

    assert len(articles) == 5
    assert all(a['url'].startswith('https://full.test/') for a in articles)
    assert not {a['url'] for a in articles} & {'https://full.test/1', 'https://full.test/2', 'https://full.test/3'}


def test_fills_with_sample_articles_when_not_enough(fake_web):
    fake_web['list_links'] = 2

    articles = QuizGenerator().get_news_articles(5)

    assert len(articles) == 5
    assert sum(a['url'].startswith('https://full.test/') for a in articles) == 2
    assert sum(a in SAMPLE_ARTICLES for a in articles) == 3


def test_number_of_attempts_is_bounded(fake_web):
    fake_web['list_links'] = 30
    fake_web['failing'] = set(range(1, 31))

    articles = QuizGenerator().get_news_articles(5)

    assert len(articles) == 5
    # 試行する記事は count * 2 = 10 件まで（各記事は _fetch_with_retry で最大3回）
    tried = {u for u in fake_web['calls'] if u.startswith('https://pickup.test/')}
    assert len(tried) == 10
    assert len(fake_web['calls']) == 1 + 10 * 3


def test_list_page_failure_falls_back_to_samples(fake_web):
    fake_web['list_error'] = TimeoutError('timeout')

    articles = QuizGenerator().get_news_articles(5)

    assert len(articles) == 5
    assert all(a in SAMPLE_ARTICLES for a in articles)
    # サンプル記事の辞書そのものではなくコピーを返す
    articles[0]['content'] = '変更'
    assert all(s['content'] != '変更' for s in SAMPLE_ARTICLES)


def test_title_falls_back_to_og_title(fake_web, monkeypatch):
    def full_page_without_h1(i):
        return _page(f'<meta property="og:title" content="OGタイトル{i}">'
                     f'<div class="article_body"><p>本文{i}</p></div>')

    monkeypatch.setattr(sys.modules[__name__], '_full_page', full_page_without_h1)

    articles = QuizGenerator().get_news_articles(1)

    assert articles[0]['title'].startswith('OGタイトル')


def test_create_quiz_set_calls_llm_once_and_attaches_articles(fake_web, monkeypatch):
    received = []

    def fake_generate(texts):
        received.append(list(texts))
        return [{'question': f'Q{i}', 'answer': f'A{i}'} for i in range(len(texts))]

    monkeypatch.setattr(honban.llm_client, 'generate_quizzes', fake_generate)

    quizzes = QuizGenerator().create_quiz_set(5, genre='it')

    assert len(received) == 1 and len(received[0]) == 5
    assert _list_calls(fake_web) == [GENRE_URLS['it']]
    assert len(quizzes) == 5
    for quiz, text in zip(quizzes, received[0]):
        assert quiz['article_content'] == text
        assert quiz['article_url'].startswith('https://full.test/')
        assert quiz['article_title'].startswith('タイトル')


def test_choices_are_shuffled_and_answer_index_follows(fake_web, monkeypatch):
    """LLM が正解を先頭に置いても、正解の位置が偏らず、answer / answer_index が正しく対応する"""
    def fake_generate(texts):
        return [{'question': f'Q{i}', 'choices': [f'正解{i}', 'b', 'c', 'd'], 'answer_index': 0,
                 'answer': f'正解{i}', 'explanation': 'e'} for i in range(len(texts))]

    monkeypatch.setattr(honban.llm_client, 'generate_quizzes', fake_generate)
    generator = QuizGenerator()

    positions = []
    for _ in range(8):
        for i, quiz in enumerate(generator.create_quiz_set(5)):
            assert sorted(quiz['choices']) == sorted([f'正解{i}', 'b', 'c', 'd'])
            assert quiz['choices'][quiz['answer_index']] == quiz['answer'] == f'正解{i}'
            positions.append(quiz['answer_index'])

    # 40問で正解が毎回同じ位置になる確率は無視できるほど小さい
    assert len(set(positions)) > 1


def test_quiz_without_choices_is_left_as_is(fake_web, monkeypatch):
    monkeypatch.setattr(honban.llm_client, 'generate_quizzes',
                        lambda texts: [{'question': 'Q', 'answer': 'A'} for _ in texts])

    quizzes = QuizGenerator().create_quiz_set(2)

    assert all('choices' not in q and q['answer'] == 'A' for q in quizzes)
