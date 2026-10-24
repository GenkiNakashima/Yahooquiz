# Yahooquiz

AIとクイズで対戦するWebアプリケーション（認証・ポイント・アイテム購入機能付き）

## 機能

### クイズ機能
- LLM（Cerebras、失敗時は OpenRouter の無料モデルにフォールバック）を使用したニュース記事からの自動クイズ生成
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
- **AI**: Cerebras（メイン）/ OpenRouter 無料モデル（フォールバック）— いずれも OpenAI 互換 API を `openai` SDK で呼び出し

## プロジェクト構造

```
yahooquiz/
├── app.py                 # メインアプリケーション
├── api_routes.py          # REST APIエンドポイント
├── db.py                  # データベース接続管理
├── honban.py              # クイズ生成ロジック（記事取得・AI対戦シミュレーション）
├── llm_client.py          # LLM 呼び出し（Cerebras → OpenRouter フォールバック、出力検証）
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
├── requirements.txt      # Python依存パッケージ
├── requirements-dev.txt  # 開発・テスト用依存パッケージ
└── tests/                # テスト（pytest）
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
- `DATABASE_URL`: PostgreSQL接続文字列
- `JWT_SECRET_KEY`: JWT署名用シークレットキー
- `SECRET_KEY`: Flask セッション用シークレットキー
- LLM 設定（下記）: Cerebras と OpenRouter の**少なくとも一方**（両方を推奨）

#### LLM（クイズ生成）の設定

クイズはまず Cerebras で生成し、失敗した場合は OpenRouter の無料モデルにフォールバックします。
プロバイダ・モデル名はすべて環境変数で指定します（コードには書かれていません）。

| 変数 | 説明 | 例 |
|------|------|----|
| `CEREBRAS_API_KEY` | Cerebras の API キー | |
| `CEREBRAS_BASE_URL` | Cerebras の API エンドポイント | `https://api.cerebras.ai/v1` |
| `CEREBRAS_MODEL` | 使用するモデル | `qwen-3.8-27b` |
| `OPENROUTER_API_KEY` | OpenRouter の API キー | |
| `OPENROUTER_BASE_URL` | OpenRouter の API エンドポイント | `https://openrouter.ai/api/v1` |
| `OPENROUTER_MODELS` | 無料モデル（`:free`）をカンマ区切りで優先順に指定 | |
| `OPENROUTER_SITE_URL` / `OPENROUTER_APP_NAME` | 任意。OpenRouter の推奨ヘッダー（`HTTP-Referer` / `X-Title`） | |
| `LLM_TIMEOUT_SECONDS` | 1リクエストのタイムアウト秒数（既定 30） | `30` |

- API キーが未設定のプロバイダはスキップされます。両方とも未設定の場合、アプリは起動時にエラーになります。
- OpenRouter の無料モデルは予告なく入れ替わるため、定期的に確認して `OPENROUTER_MODELS` を更新してください。
  ```bash
  curl -s https://openrouter.ai/api/v1/models | jq -r '.data[].id' | grep ':free$'
  ```
- フォールバックの条件: 429（レート制限・待機せず即切り替え）、5xx、タイムアウト、接続エラー、404（モデル提供終了など）、
  その他の 4xx（401/403/400 など。設定ミスの可能性があるため ERROR ログを出力）、出力 JSON のパース・検証失敗。
- 両方とも失敗した場合は「現在クイズを生成できません」と表示します（HTTP 503）。
- ログには、生成に成功したプロバイダ・モデルとフォールバック理由（ステータスコード等）を出力します。API キーとプロンプト本文は出力しません。

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

### テスト

LLM の API は実際には呼び出さず、モックでテストします。

```bash
pip install -r requirements-dev.txt
pytest
```

### Dockerでの実行

```bash
docker build -t yahooquiz .
docker run -p 8080:8080 \
  --env-file .env \
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
  --substitutions='^@^_DATABASE_URL=your-db-url@_JWT_SECRET_KEY=your-secret@_CEREBRAS_API_KEY=your-cerebras-key@_OPENROUTER_API_KEY=your-openrouter-key@_OPENROUTER_MODELS=YOUR_MODEL_1:free,YOUR_MODEL_2:free'
```
`OPENROUTER_MODELS` の値にカンマを含むため、`^@^` で区切り文字を `@` に変更しています。
Base URL・Cerebras のモデル名・タイムアウトは `cloudbuild.yaml` の `substitutions` に既定値があり、同じ方法で上書きできます。

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
