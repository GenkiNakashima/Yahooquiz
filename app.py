# app.py
from flask import Flask, render_template, request, session, redirect, url_for
from flask_cors import CORS
from datetime import datetime
from honban import QuizGenerator
import os
import logging

# Import database and API routes
from db import init_db_pool, close_db_pool
from api_routes import api
from middleware.security import setup_security_headers

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your_secret_key')

# Setup CORS
CORS(app, resources={r"/api/*": {"origins": "*"}})

# Setup security headers
setup_security_headers(app)

# Register API blueprint
app.register_blueprint(api)

# Initialize database connection pool
try:
    init_db_pool()
    logger.info("Database connection pool initialized")
except Exception as e:
    logger.warning(f"Database initialization failed: {e}. Some features may be unavailable.")

@app.teardown_appcontext
def shutdown_db_pool(exception=None):
    """Close database pool on app shutdown"""
    if exception:
        logger.error(f"App context teardown with exception: {exception}")
    close_db_pool()

# QuizGeneratorのインスタンスを作成（環境変数からAPIキーを取得）
api_key = os.environ.get('GEMINI_API_KEY')
if not api_key:
    raise ValueError("GEMINI_API_KEY environment variable is required")

quiz_generator = QuizGenerator(api_key=api_key)

@app.route('/favicon.ico')
def favicon():
   """ブラウザのfavicon要求に200で応答（404ノイズ抑制）。"""
   # staticにfaviconが無い場合でも空レスポンスでOK
   return ('', 200, {'Content-Type': 'image/x-icon'})

@app.route('/')
def index():
   """トップページ"""
   return render_template('index.html')

@app.route('/login')
def login():
   """ログインページ"""
   return render_template('login.html')

@app.route('/register')
def register():
   """ユーザー登録ページ"""
   return render_template('register.html')

@app.route('/dashboard')
def dashboard():
   """ユーザーダッシュボード"""
   return render_template('dashboard.html')

@app.route('/shop')
def shop():
   """アイコンショップ"""
   return render_template('shop.html')

@app.route('/leaderboard')
def leaderboard():
   """ランキング"""
   return render_template('leaderboard.html')

@app.route('/game', methods=['GET', 'POST'])
def game():
   """ゲーム開始ページ"""
   if request.method == 'POST':
       # AIレベルの選択を受け取る
       ai_level = request.form.get('ai_level', 'normal')
       session['ai_level'] = ai_level
       session['score'] = {'player': 0, 'ai': 0}
       session['round'] = 0
       session['total_rounds'] = 5
       return redirect(url_for('quiz'))
   return render_template('game.html')

@app.route('/quiz', methods=['GET', 'POST'])
def quiz():
    """クイズページ"""
    if 'round' not in session:
        return redirect(url_for('index'))
    
    ai_level = session.get('ai_level', 'normal')
    
    if request.method == 'GET':
    # 新しい問題の用意
        quiz_data = quiz_generator.create_quiz()
        if quiz_data:
            session['current_quiz'] = quiz_data
            session['ai_buzzer_time'] = quiz_generator.simulate_ai_buzzer(ai_level)
            ai_thinking = quiz_generator.get_ai_thinking_message(ai_level)
            return render_template('quiz.html',
                                question=quiz_data['question'],
                                article_content=quiz_data['article_content'],
                                article_url=quiz_data['article_url'],
                                article_title=quiz_data['article_title'],
                                round=session['round'] + 1,
                                total=session['total_rounds'],
                                ai_level=ai_level,
                                ai_thinking=ai_thinking)
        else:
            # quiz_dataが取得できなかった場合のエラーハンドリング
            return render_template('error.html', 
                                error_message="クイズの生成に失敗しました。"), 500
    
    elif request.method == 'POST':
        quiz_data = session.get('current_quiz', {})
        if not quiz_data:
            return redirect(url_for('index'))

        player_time = float(request.form.get('time', 999))
        ai_time = session.get('ai_buzzer_time', 999)
        
        # 変数を初期化
        result = None
        ai_thinking = None  # ここで初期化
        
        print("\n==== 回答判定開始 ====")
        print(f"プレイヤーの回答時間: {player_time:.2f}秒")
        print(f"AIの回答時間: {ai_time:.2f}秒")
        
        if player_time < ai_time:
            print("\n✨ プレイヤーが早押し成功！")
            user_answer = request.form.get('answer', '').strip().lower()
            correct_answer = quiz_data.get('answer', '').lower()
            
            print(f"問題: {quiz_data.get('question')}")
            print(f"プレイヤーの回答: {user_answer}")
            print(f"正解: {correct_answer}")
            
            if user_answer == correct_answer:
                session['score']['player'] += 1
                result = 'correct'
                print("\n🎉 正解！")
            else:
                result = 'wrong'
                print("\n❌ 不正解...")
            
            # プレイヤーが回答した場合のAIの思考メッセージ
            ai_thinking = "まだ考えていたのに..."
            
        else:
            print("\n🤖 AIが早押し成功！")
            ai_level = session.get('ai_level', 'normal')
            ai_correct = quiz_generator.simulate_ai_answer(ai_level)
            ai_thinking = quiz_generator.get_ai_thinking_message(ai_level)
            
            print(f"問題: {quiz_data.get('question')}")
            print(f"正解: {quiz_data.get('answer')}")
            
            if ai_correct:
                result = 'ai_correct'
                session['score']['ai'] += 1
                print("\n🎯 AIが正解！")
            else:
                result = 'ai_wrong'
                print("\n😅 AIが不正解！")
        
        print(f"プレイヤーの得点: {session['score']['player']}点")
        print(f"AIの得点: {session['score']['ai']}点")
        
        print("\n==== ラウンド情報 ====")
        session['round'] += 1
        print(f"現在のラウンド: {session['round']}/{session['total_rounds']}")
        
        if session['round'] >= session['total_rounds']:
            print("\n🏁 ゲーム終了！")
            return redirect(url_for('result'))
        
        # 必ず返り値を返す
        return render_template('quiz.html',
                            question=quiz_data['question'],
                            answer=quiz_data['answer'],
                            explanation=quiz_data.get('explanation', ''),
                            result=result,
                            ai_thinking=ai_thinking,  # 常に定義された状態で渡される
                            round=session['round'],
                            total=session['total_rounds'],
                            ai_level=session.get('ai_level', 'normal'))

@app.route('/result')
def result():
   """結果表示ページ"""
   if 'score' not in session:
       return redirect(url_for('index'))
   
   ai_level = session.get('ai_level', 'normal')
   return render_template('result.html',
                        player_score=session['score']['player'],
                        ai_score=session['score']['ai'],
                        total=session['total_rounds'],
                        ai_level=ai_level)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)