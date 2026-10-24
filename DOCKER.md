# Docker実行ガイド

データベースは Cloud Firestore を使います（コンテナ内に DB は立てません）。
コンテナからは、手元の Application Default Credentials（ADC）を使って Firestore に接続します。

## 方法1: Docker Compose（推奨）

### 手順

1. **Firestore の認証情報を用意（最初に1回だけ）**
   ```bash
   gcloud auth application-default login
   ```
   `~/.config/gcloud/application_default_credentials.json` が作られ、コンテナに読み取り専用でマウントされます。
   サービスアカウントキーを使う場合は、`.env.docker` の `GOOGLE_ADC_FILE` にそのファイルのパスを指定してください。

2. **環境変数の設定**
   ```bash
   cp .env.docker.example .env.docker
   # .env.docker を編集して GOOGLE_CLOUD_PROJECT と
   # CEREBRAS_API_KEY / OPENROUTER_API_KEY / OPENROUTER_MODELS などを設定
   ```

3. **起動**
   ```bash
   docker-compose --env-file .env.docker up --build
   ```

4. **アクセス**
   - アプリケーション: http://localhost:8080

5. **停止**
   ```bash
   docker-compose down
   ```

## 方法2: docker run

```bash
docker build -t yahooquiz-app .
docker run -p 8080:8080 \
  --env-file .env.docker \
  -e GOOGLE_APPLICATION_CREDENTIALS=/secrets/adc.json \
  -v ~/.config/gcloud/application_default_credentials.json:/secrets/adc.json:ro \
  yahooquiz-app
```

## トラブルシューティング

### Firestore に接続できない

**ログ**: `Failed to initialize Firestore: Your default credentials were not found.`
または `Firestore initialization failed ... Some features may be unavailable.`

アプリは起動しますが、ログイン・ランキング・ゲームなど DB を使う機能が動きません。

**解決方法**:
- `gcloud auth application-default login` を実行したか確認
- `.env.docker` の `GOOGLE_CLOUD_PROJECT` が正しいプロジェクト ID か確認
- `GOOGLE_ADC_FILE` を指定している場合、そのファイルが存在するか確認
- そのアカウント（またはサービスアカウント）に Firestore への読み書き権限（例: `roles/datastore.user`）があるか確認

### 環境変数が読み込まれない

**解決方法**:
```bash
# 環境変数を確認
docker-compose --env-file .env.docker config

# または個別に確認
docker run --env-file .env.docker yahooquiz-app env | grep -E '^(CEREBRAS|OPENROUTER|LLM)_' | sed 's/_API_KEY=.*/_API_KEY=(設定済み)/'
```

## 本番環境デプロイ

### Google Cloud Run

Cloud Run 上では、サービスに割り当てたサービスアカウントで Firestore に自動的に認証されます
（認証情報ファイルの設定は不要です。サービスアカウントに Firestore の読み書き権限が必要です）。

1. **Container Registryにプッシュ**
   ```bash
   gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/yahooquiz
   ```

2. **Cloud Runにデプロイ**
   ```bash
   gcloud run deploy yahooquiz \
     --image gcr.io/YOUR_PROJECT_ID/yahooquiz \
     --platform managed \
     --region asia-northeast1 \
     --allow-unauthenticated \
     --set-env-vars '^@^JWT_SECRET_KEY=YOUR_SECRET@CEREBRAS_API_KEY=YOUR_CEREBRAS_KEY@CEREBRAS_BASE_URL=https://api.cerebras.ai/v1@CEREBRAS_MODEL=qwen-3.8-27b@OPENROUTER_API_KEY=YOUR_OPENROUTER_KEY@OPENROUTER_BASE_URL=https://openrouter.ai/api/v1@OPENROUTER_MODELS=YOUR_MODEL_1:free,YOUR_MODEL_2:free'
   ```
   ※ `OPENROUTER_MODELS` の値にカンマを含むため、`^@^` で区切り文字を `@` に変更しています。
