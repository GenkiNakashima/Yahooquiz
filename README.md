# Yahooquiz

AIとクイズで対戦するWebアプリケーション（認証・ポイント・アイテム購入機能付き）

## 機能

### クイズ機能
- LLM（Cerebras、失敗時は OpenRouter の無料モデルにフォールバック）を使用したニュース記事からの自動クイズ生成
- 1ゲーム分（5問）の問題をゲーム開始時に1回の LLM 呼び出しでまとめて生成し、Firestore に保存
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
- **Database**: Cloud Firestore（firebase-admin SDK）
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
│   ├── icon_service.py   # アイコン管理
│   └── quiz_set_service.py # 1ゲーム分のクイズの保存・取得（Firestore）
├── middleware/            # ミドルウェア
│   └── security.py       # セキュリティ対策
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
- Firestore（Native モード）が有効な Firebase / GCP プロジェクト

### 環境変数設定

`.env.example`をコピーして`.env`を作成し、必要な値を設定してください。

```bash
cp .env.example .env
```

必須の環境変数:
- `JWT_SECRET_KEY`: JWT署名用シークレットキー
- `SECRET_KEY`: Flask セッション用シークレットキー
- LLM 設定（下記）: Cerebras と OpenRouter の**少なくとも一方**（両方を推奨）

#### Firestore の認証

データベースは Cloud Firestore です。`firebase_admin.initialize_app()` を引数なしで呼んでいるため、
認証には Application Default Credentials（ADC）を使います。マイグレーションはありません
（コレクションは最初の書き込み時に作られ、`icons` は起動時に空ならデフォルトの6種を投入します）。

- **Cloud Run 上**: サービスアカウントで自動的に認証されるため、設定は不要です（Firestore の読み書き権限が必要）。
- **ローカル**: 次のどちらかを行います。認証できない場合もアプリは起動しますが、DB を使う機能は動きません（ログに警告が出ます）。
  ```bash
  gcloud auth application-default login
  # または
  export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
  ```
  プロジェクトを自動判定できない場合は `GOOGLE_CLOUD_PROJECT` も指定してください。

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

2. Firestore の認証情報を用意（上記「Firestore の認証」を参照）
```bash
gcloud auth application-default login
```

3. アプリケーション起動
```bash
python app.py
```

### クイズデータの保存（Firestore `quiz_sets` コレクション）

1ゲーム分の問題（記事本文を含む）は Firestore の `quiz_sets` コレクションに1ドキュメントとして保存し、
セッション Cookie にはドキュメント ID だけを入れます（記事本文を入れると Cookie の上限 約4KB を超えるため）。
そのため、**ゲームを遊ぶには Firestore への接続が必要**です。

各ドキュメントには `expires_at`（作成から1日後）が入っています。Firestore の TTL ポリシーを**最初に1回だけ**設定すると、
期限を過ぎたドキュメントが自動で削除されます（TTL による削除は遅れることがあるため、アプリ側でも読み込み時に期限を確認しています）。

```bash
gcloud firestore fields ttls update expires_at \
  --collection-group=quiz_sets --enable-ttl --project=YOUR_PROJECT_ID
```
Firebase コンソール（Firestore → TTL）からも設定できます。

### テスト

LLM の API・ニュース記事の取得・Firestore は実際には使わず、モックでテストします。

```bash
pip install -r requirements-dev.txt
pytest
```

Firestore エミュレータを使う結合テスト（`tests/test_quiz_set_service.py` の一部）は、
`FIRESTORE_EMULATOR_HOST` を設定したときだけ実行されます。
```bash
firebase emulators:start --only firestore   # 別のターミナルで起動
FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 pytest
```

### Dockerでの実行

Docker Compose での起動手順や、コンテナに認証情報を渡す方法は [DOCKER.md](DOCKER.md) を参照してください。

```bash
docker build -t yahooquiz .
docker run -p 8080:8080 \
  --env-file .env \
  -e GOOGLE_APPLICATION_CREDENTIALS=/secrets/adc.json \
  -v ~/.config/gcloud/application_default_credentials.json:/secrets/adc.json:ro \
  yahooquiz
```

## デプロイ

### Google Cloud Runへのデプロイ

1. Google Cloud Projectの設定
```bash
gcloud config set project YOUR_PROJECT_ID
```

2. Firestore の準備
   - プロジェクトで Firestore（Native モード）のデータベースを作成済みであること
   - Cloud Run のサービスアカウントに Firestore の読み書き権限（例: `roles/datastore.user`）があること
   - `quiz_sets` の TTL ポリシーを設定済みであること（「クイズデータの保存」を参照）

3. Cloud Buildでビルド・デプロイ
```bash
gcloud builds submit --config cloudbuild.yaml \
  --substitutions='^@^_JWT_SECRET_KEY=your-secret@_CEREBRAS_API_KEY=your-cerebras-key@_OPENROUTER_API_KEY=your-openrouter-key@_OPENROUTER_MODELS=YOUR_MODEL_1:free,YOUR_MODEL_2:free'
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
- クエリインジェクション対策（Firestore SDK によるクエリ組み立て。文字列連結でクエリを作らない）
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
