# quiz_generator.py
from groq import Groq
import random
import requests
from bs4 import BeautifulSoup
import os

class QuizGenerator:
    def __init__(self, api_key):
        self.api_key = api_key
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
        # 利用可能なGroqモデル（無料枠で使用可能）
        # llama-3.3-70b-versatile: 高性能で汎用的なモデル
        # mixtral-8x7b-32768: 大きなコンテキストウィンドウ
        # llama-3.1-8b-instant: 高速で軽量
        self.model_name = "llama-3.3-70b-versatile"

        # Groqクライアントの初期化
        try:
            self.client = Groq(api_key=self.api_key)
            # モデルの動作確認
            test_response = self.client.chat.completions.create(
                messages=[{"role": "user", "content": "test"}],
                model=self.model_name,
                max_tokens=10
            )
            print(f"成功: Groqモデル '{self.model_name}' を使用します")
        except Exception as e:
            print(f"Groqモデルの初期化エラー: {e}")
            raise Exception(f"利用可能なGroqモデルが見つかりませんでした: {e}")

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

    def get_news_article(self):
        """サンプル記事を返す（Yahoo!ニュースが利用できない場合のフォールバック）"""
        # Yahoo!ニュースのスクレイピングが難しい場合のサンプルデータ
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
            # 実際のYahoo!ニュースからの取得を試行
            url = "https://news.yahoo.co.jp/topics/business"
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            news_feed = soup.find('ul', class_='newsFeed_list')
            article_links = news_feed.find_all('a') if news_feed else []
            
            if article_links:
                random_article = random.choice(article_links)
                article_url = random_article.get('href')
                
                article_response = requests.get(article_url, headers=self.headers, timeout=10)
                article_soup = BeautifulSoup(article_response.text, 'html.parser')
                
                full_article_link = article_soup.find('a', {
                    'class': 'sc-gdv5m1-9 bxbqJP',
                    'data-ual-gotocontent': 'true'
                })
                
                if full_article_link:
                    full_url = full_article_link.get('href')
                    full_response = requests.get(full_url, headers=self.headers, timeout=10)
                    full_soup = BeautifulSoup(full_response.text, 'html.parser')
                    
                    article_content = full_soup.find('div', class_='article_body')
                    if article_content:
                        content_text = ' '.join([p.text.strip() for p in article_content.find_all(['p', 'h2'])])
                        return {
                            'content': content_text[:2000],
                            'url': full_url,
                            'title': full_soup.find('h1').text.strip() if full_soup.find('h1') else 'タイトルなし'
                        }
            
            # Yahoo!ニュースからの取得に失敗した場合、サンプル記事を使用
            print("Yahoo!ニュースからの記事取得に失敗、サンプル記事を使用します")
            return random.choice(sample_articles)
            
        except Exception as e:
            print(f"記事取得エラー: {e} - サンプル記事を使用します")
            return random.choice(sample_articles)

    def generate_quiz(self, text):
        """AIによるクイズ生成"""
        try:
            prompt = f"""
以下の文章から時事ネタのクイズを作成してください。
1単語または短い語句で答えられる問題にしてください。
以下のフォーマットで出力してください：

Question: （ここに問題文）
Answer: （ここに1単語または短い語句で答え）
Explanation: （ここに解説）

文章:
{text}
"""
            # GroqのChat Completions APIを使用
            response = self.client.chat.completions.create(
                messages=[
                    {
                        "role": "system",
                        "content": "あなたは日本語でクイズを作成する専門家です。指定されたフォーマットに厳密に従ってクイズを生成してください。"
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                model=self.model_name,
                max_tokens=500,
                temperature=0.7
            )

            if response.choices and len(response.choices) > 0:
                response_text = response.choices[0].message.content
                lines = response_text.split('\n')
                quiz_data = {}

                for line in lines:
                    line = line.strip()
                    if line.startswith('Question:') or line.startswith('問題:'):
                        quiz_data['question'] = line.replace('Question:', '').replace('問題:', '').strip()
                    elif line.startswith('Answer:') or line.startswith('答え:') or line.startswith('回答:'):
                        quiz_data['answer'] = line.replace('Answer:', '').replace('答え:', '').replace('回答:', '').strip()
                    elif line.startswith('Explanation:') or line.startswith('解説:'):
                        quiz_data['explanation'] = line.replace('Explanation:', '').replace('解説:', '').strip()

                # 必要なフィールドが全て揃っているかチェック
                if all(key in quiz_data for key in ['question', 'answer', 'explanation']):
                    return quiz_data
                else:
                    print(f"クイズデータ不完全: {quiz_data}")
                    return None

            return None
        except Exception as e:
            # レート制限時のフォールバック（簡易問題を返す）
            err_text = str(e)
            print(f"クイズ生成エラー: {err_text}")
            if '429' in err_text or 'RATE_LIMIT' in err_text or 'rate_limit' in err_text.lower():
                # テキストから簡易的に固有名詞らしき語を抽出（非常に単純なフォールバック）
                keywords = [w for w in text.split() if len(w) >= 2]
                fallback_answer = (keywords[0][:12] if keywords else 'ニュース')
                fallback_question = '本文のキーワードは何？'
                fallback_explanation = 'レート制限のため簡易問題を表示しています。'
                return {
                    'question': fallback_question,
                    'answer': fallback_answer,
                    'explanation': fallback_explanation
                }
            return None

    def create_quiz(self):
        """記事取得からクイズ生成までの一連の処理"""
        try:
            article_data = self.get_news_article()
            if article_data:
                quiz_data = self.generate_quiz(article_data['content'])
                if quiz_data:
                    # クイズデータに記事情報を追加
                    quiz_data['article_content'] = article_data['content']
                    quiz_data['article_url'] = article_data['url']
                    quiz_data['article_title'] = article_data['title']
                    return quiz_data
            return None
        except Exception as e:
            print(f"クイズ作成エラー: {e}")
            return None