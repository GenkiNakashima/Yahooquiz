# Yahooquiz

AIとクイズで対戦するWebアプリケーション（認証・ポイント・アイテム購入機能付き）

## 機能

### クイズ機能
- Gemini AIを使用したニュース記事からの自動クイズ生成
- AI対戦相手との早押しクイズバトル
- 3段階の難易度（強い・普通・弱い）

### 認証機能
- メールアドレス + パスワードでのユーザー登録・ログイン
- JWT（アクセストークン + リフレッシュトークン）による認証
- セキュアなパスワードハッシュ化（bcrypt）

### 対戦・ポイント機能
- クイズ対戦結果の保存
- 勝利時に自動でポイント付与（100ポイント/勝利）
- ポイント履歴の管理
- リーダーボード（ランキング）

### アイコン購入機能
- ポイントでデジタルアイテム（アイコン）を購入
- 限定アイコンの在庫管理
- トランザクション処理による整合性保証

## 技術スタック

- **Backend**: Python 3.11, Flask
- **Database**: PostgreSQL
- **Authentication**: JWT, bcrypt
- **Container**: Docker
- **Deployment**: Google Cloud Run
- **Hosting**: Firebase Hosting (静的ファイル)
- **AI**: Google Gemini API

## プロジェクト構造

```
yahooquiz/
├── app.py                 # メインアプリケーション
├── api_routes.py          # REST APIエンドポイント
├── db.py                  # データベース接続管理
├── honban.py              # クイズ生成ロジック
├── services/              # ビジネスロジック
│   ├── auth.py           # 認証サービス
│   ├── user_service.py   # ユーザー管理
│   ├── match_service.py  # 対戦管理
│   └── icon_service.py   # アイコン管理
├── middleware/            # ミドルウェア
│   └── security.py       # セキュリティ対策
├── migrations/            # DBマイグレーション
│   └── 001_initial_schema.sql
├── templates/             # HTMLテンプレート
├── static/               # 静的ファイル
├── Dockerfile            # Dockerイメージ定義
├── cloudbuild.yaml       # Cloud Build設定
├── firebase.json         # Firebase設定
└── requirements.txt      # Python依存パッケージ
```

## API仕様

### 認証

#### POST /api/auth/register
ユーザー登録
```json
{
  "email": "user@example.com",
  "password": "Password123",
  "display_name": "username"
}
```

#### POST /api/auth/login
ログイン
```json
{
  "email": "user@example.com",
  "password": "Password123"
}
```

#### POST /api/auth/refresh
アクセストークン更新

### ユーザー

#### GET /api/users/me
現在のユーザー情報取得（認証必須）

#### GET /api/users/me/icons
所有アイコン一覧（認証必須）

#### GET /api/users/me/transactions
ポイント履歴（認証必須）

#### GET /api/users/me/matches
対戦履歴（認証必須）

### 対戦

#### POST /api/matches
対戦結果登録（認証必須）
```json
{
  "match_type": "1v1",
  "players": [
    {"user_id": "uuid", "score": 5, "is_winner": true},
    {"user_id": "uuid", "score": 3, "is_winner": false}
  ],
  "metadata": {"quiz_set": "news_20250118"}
}
```

### アイコン

#### GET /api/icons
利用可能なアイコン一覧

#### POST /api/icons/{icon_id}/purchase
アイコン購入（認証必須）

### リーダーボード

#### GET /api/leaderboard
ランキング取得

## セットアップ

### 前提条件
- Docker
- Google Cloud SDK (gcloud)
- Firebase CLI
- PostgreSQL（ローカル開発用）

### 環境変数設定

`.env.example`をコピーして`.env`を作成し、必要な値を設定してください。

```bash
cp .env.example .env
```

必須の環境変数:
- `GEMINI_API_KEY`: Google Gemini APIキー
- `DATABASE_URL`: PostgreSQL接続文字列
- `JWT_SECRET_KEY`: JWT署名用シークレットキー
- `SECRET_KEY`: Flask セッション用シークレットキー

### ローカル開発

1. 依存パッケージをインストール
```bash
pip install -r requirements.txt
```

2. PostgreSQLデータベースを作成
```bash
createdb yahooquiz
```

3. マイグレーション実行
```bash
psql yahooquiz < migrations/001_initial_schema.sql
```

4. アプリケーション起動
```bash
python app.py
```

### Dockerでの実行

```bash
docker build -t yahooquiz .
docker run -p 8080:8080 \
  -e GEMINI_API_KEY=your-key \
  -e DATABASE_URL=your-db-url \
  -e JWT_SECRET_KEY=your-secret \
  yahooquiz
```

## デプロイ

### Google Cloud Runへのデプロイ

1. Google Cloud Projectの設定
```bash
gcloud config set project YOUR_PROJECT_ID
```

2. Cloud SQL（PostgreSQL）インスタンス作成
```bash
gcloud sql instances create yahooquiz-db \
  --database-version=POSTGRES_15 \
  --tier=db-f1-micro \
  --region=asia-northeast1
```

3. データベース作成とマイグレーション実行
```bash
gcloud sql databases create yahooquiz --instance=yahooquiz-db
# Cloud SQL Proxyを使用してマイグレーションを実行
```

4. Cloud Buildでビルド・デプロイ
```bash
gcloud builds submit --config cloudbuild.yaml \
  --substitutions=_GEMINI_API_KEY="your-key",_DATABASE_URL="your-db-url",_JWT_SECRET_KEY="your-secret"
```

### Firebaseへのデプロイ

1. Firebaseプロジェクトの設定
```bash
firebase login
firebase init
```

2. `.firebaserc`の`default`プロジェクトIDを更新

3. デプロイ
```bash
firebase deploy
```

## セキュリティ

実装済みのセキュリティ対策:
- SQLインジェクション対策（Prepared Statements）
- XSS対策（入力サニタイズ、CSP）
- パスワードハッシュ化（bcrypt）
- JWT認証（アクセストークン + リフレッシュトークン）
- HTTPS強制（本番環境）
- セキュリティヘッダー（X-Frame-Options, X-Content-Type-Options等）
- トランザクション処理によるデータ整合性保証

## ライセンス

MIT License

## 貢献

Pull Requestsを歓迎します。
