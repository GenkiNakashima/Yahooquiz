# quiz_generator.py
import random
import requests
from bs4 import BeautifulSoup

import llm_client

GENRE_URLS = {
    'top-picks': 'https://news.yahoo.co.jp/topics/top-picks',
    'domestic': 'https://news.yahoo.co.jp/topics/domestic',
    'world': 'https://news.yahoo.co.jp/topics/world',
    'business': 'https://news.yahoo.co.jp/topics/business',
    'entertainment': 'https://news.yahoo.co.jp/topics/entertainment',
    'sports': 'https://news.yahoo.co.jp/topics/sports',
    'it': 'https://news.yahoo.co.jp/topics/it',
    'science': 'https://news.yahoo.co.jp/topics/science',
    'local': 'https://news.yahoo.co.jp/topics/local',
}

class QuizGenerator:
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        self.ai_levels = {
            'strong': {
                'correct_rate': 0.95,  # 95%の正解率
                'reaction_time': {
                    'min': 6.0,        # 最速6秒
                    'max': 8.0         # 最長8秒
                }
            },
            'normal': {
                'correct_rate': 0.80,  # 80%の正解率
                'reaction_time': {
                    'min': 8.0,
                    'max': 11.0
                }
            },
            'weak': {
                'correct_rate': 0.60,  # 60%の正解率
                'reaction_time': {
                    'min': 10.0,
                    'max': 15.0
                }
            }
        }

    def simulate_ai_buzzer(self, level='normal'):
        """AIの早押し判定をレベルに応じてシミュレート"""
        level_config = self.ai_levels[level]
        return random.uniform(
            level_config['reaction_time']['min'],
            level_config['reaction_time']['max']
        )

    def simulate_ai_answer(self, level='normal'):
        """AIの回答をレベルに応じてシミュレート"""
        return random.random() < self.ai_levels[level]['correct_rate']

    def get_ai_thinking_message(self, level='normal'):
        """AIの思考メッセージをレベルに応じて変更"""
        messages = {
            'strong': [
                "高精度で解析中...",
                "過去の記事と詳細な照合を実施中...",
                "複数のデータベースを並列検索中..."
            ],
            'normal': [
                "データを分析中...",
                "関連記事を確認中...",
                "情報を検索中..."
            ],
            'weak': [
                "なんとか思い出そうとしています...",
                "記事を読み返しています...",
                "ゆっくり考え中..."
            ]
        }
        return random.choice(messages[level])

    def _fetch_with_retry(self, url, max_retries=3, timeout=10):
        """リトライ付きHTTPリクエスト（指数バックオフ）"""
        import time
        for attempt in range(max_retries):
            try:
                response = requests.get(url, headers=self.headers, timeout=timeout)
                response.raise_for_status()
                return response
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                wait = 2 ** attempt + random.uniform(0.5, 1.5)
                print(f"リクエスト失敗 (試行{attempt + 1}/{max_retries}): {e} - {wait:.1f}秒後にリトライ")
                time.sleep(wait)

    def get_news_article(self, genre='business'):
        """Yahoo!ニュースから記事を取得する（失敗時はサンプル記事を使用）"""
        import time

        sample_articles = [
            {
                'content': '日本銀行は本日、政策金利を0.1%引き上げることを発表しました。これは2年ぶりの利上げとなり、インフレ対策の一環として実施されます。市場関係者からは慎重な反応が見られており、今後の経済動向に注目が集まっています。',
                'url': 'https://example.com/news1',
                'title': '日銀が2年ぶりの利上げを決定'
            },
            {
                'content': '東京都は2025年度から新しい環境税を導入することを発表しました。この税制は企業の二酸化炭素排出量に応じて課税され、環境保護の推進を図ります。対象となる企業は約1000社で、年間約500億円の税収を見込んでいます。',
                'url': 'https://example.com/news2',
                'title': '東京都が新環境税を導入へ'
            },
            {
                'content': '大手自動車メーカーのトヨタ自動車は、2030年までに電気自動車の生産台数を年間350万台に増加させる計画を発表しました。これは現在の約10倍の規模となり、カーボンニュートラル実現に向けた取り組みの一環です。',
                'url': 'https://example.com/news3',
                'title': 'トヨタ、EV生産を大幅拡大へ'
            }
        ]

        try:
            # Step1: ニュース一覧ページを取得
            url = GENRE_URLS.get(genre, GENRE_URLS['business'])
            response = self._fetch_with_retry(url)
            soup = BeautifulSoup(response.text, 'html.parser')

            news_feed = soup.find('ul', class_='newsFeed_list')
            article_links = news_feed.find_all('a') if news_feed else []

            if not article_links:
                print("記事リンクが見つかりません、サンプル記事を使用します")
                return random.choice(sample_articles)

            # 複数の記事候補をシャッフルして順番に試す（最大3件）
            candidates = random.sample(article_links, min(3, len(article_links)))

            for candidate in candidates:
                try:
                    article_url = candidate.get('href')
                    if not article_url:
                        continue

                    time.sleep(random.uniform(0.5, 1.5))

                    # Step2: 記事ページを取得
                    article_response = self._fetch_with_retry(article_url)
                    article_soup = BeautifulSoup(article_response.text, 'html.parser')

                    full_article_link = article_soup.find('a', {'data-ual-gotocontent': 'true'})
                    if not full_article_link:
                        continue

                    full_url = full_article_link.get('href')
                    if not full_url:
                        continue

                    time.sleep(random.uniform(0.5, 1.5))

                    # Step3: 全文ページを取得
                    full_response = self._fetch_with_retry(full_url)
                    full_soup = BeautifulSoup(full_response.text, 'html.parser')

                    article_content = full_soup.find('div', class_='article_body')
                    if not article_content:
                        continue

                    content_text = ' '.join([p.text.strip() for p in article_content.find_all(['p', 'h2'])])
                    if not content_text.strip():
                        continue

                    # タイトル取得: <article> > <header> > <h1>
                    title = None
                    article_tag = full_soup.find('article')
                    if article_tag:
                        header_tag = article_tag.find('header')
                        if header_tag:
                            h1_tag = header_tag.find('h1')
                            if h1_tag:
                                title = h1_tag.get_text(strip=True)

                    if not title:
                        og_title = full_soup.find('meta', property='og:title')
                        if og_title:
                            title = og_title.get('content', '').strip()

                    if not title:
                        title = 'タイトルなし'

                    print(f"記事取得成功: {title}")
                    return {
                        'content': content_text[:2000],
                        'url': full_url,
                        'title': title
                    }

                except Exception as e:
                    print(f"記事候補の取得失敗、次の候補へ: {e}")
                    continue

            print("Yahoo!ニュースからの記事取得に失敗、サンプル記事を使用します")
            return random.choice(sample_articles)

        except Exception as e:
            print(f"記事取得エラー: {e} - サンプル記事を使用します")
            return random.choice(sample_articles)

    def generate_quiz(self, text):
        """
        AIによるクイズ生成（LLM 呼び出しは llm_client に委譲）。
        生成できない場合は llm_client.QuizGenerationError をそのまま送出する。
        """
        return llm_client.generate_quiz(text)

    def create_quiz(self, genre='business'):
        """
        記事取得からクイズ生成までの一連の処理。
        記事取得は内部でサンプル記事にフォールバックするため、失敗時に送出されるのは
        llm_client.QuizGenerationError のみ。
        """
        article_data = self.get_news_article(genre)
        quiz_data = self.generate_quiz(article_data['content'])
        # クイズデータに記事情報を追加
        quiz_data['article_content'] = article_data['content']
        quiz_data['article_url'] = article_data['url']
        quiz_data['article_title'] = article_data['title']
        return quiz_data
