# Docker実行ガイド

データベースは Cloud Firestore ですが、ローカルの Docker では **Firestore エミュレーター**を使います。
本番の Firestore には接続しません（認証情報もマウントしません）。`docker-compose.yml` の `firestore` サービスが
エミュレーターで、アプリはその起動を待ってから起動します。エミュレーターのデータはコンテナを止めると消えます。

## 方法1: Docker Compose（推奨）

### 手順

1. **環境変数の設定**
   ```bash
   cp .env.docker.example .env.docker
   # .env.docker を編集して SECRET_KEY / JWT_SECRET_KEY と
   # GROQ_API_KEY / OPENROUTER_API_KEY / OPENROUTER_MODELS などを設定
   ```

2. **起動**
   ```bash
   docker-compose --env-file .env.docker up --build
   ```

3. **アクセス**
   - アプリケーション: http://localhost:8080

4. **停止**
   ```bash
   docker-compose down
   ```

## 方法2: docker run

手元で起動したエミュレーター（README の「ローカル開発」を参照）に、コンテナから接続します。
コンテナから接続できるよう、エミュレーターは `--host-port=0.0.0.0:8085` で起動してください。

```bash
docker build -t yahooquiz-app .
docker run -p 8080:8080 \
  --env-file .env.docker \
  -e FIRESTORE_EMULATOR_HOST=host.docker.internal:8085 \
  -e GOOGLE_CLOUD_PROJECT=demo-yahooquiz \
  yahooquiz-app
```

## トラブルシューティング

### 起動時に `UnsafeFirestoreTargetError` で止まる

**ログ**: `FIRESTORE_EMULATOR_HOST が設定されていません` または `Firestore エミュレーター（...）に接続できません`

本番以外の環境では、本番の Firestore に接続しないよう、エミュレーターが必須です。

**解決方法**:
- Docker Compose: `firestore` サービスが起動しているか確認（`docker-compose ps`、`docker-compose logs firestore`）
- docker run: 手元のエミュレーターを `--host-port=0.0.0.0:8085` で起動しているか、`FIRESTORE_EMULATOR_HOST` を渡しているか確認
- `K_SERVICE` を設定していないか確認（本番の判別に使うため、ローカルでは設定しない）

### 環境変数が読み込まれない

**解決方法**:
```bash
# 環境変数を確認
docker-compose --env-file .env.docker config

# または個別に確認
docker run --env-file .env.docker yahooquiz-app env | grep -E '^(GROQ|OPENROUTER|LLM)_' | sed 's/_API_KEY=.*/_API_KEY=(設定済み)/'
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
     --set-env-vars '^@^GROQ_BASE_URL=https://api.groq.com/openai/v1@GROQ_MODELS=openai/gpt-oss-120b,openai/gpt-oss-20b,qwen/qwen3.8-27b@OPENROUTER_BASE_URL=https://openrouter.ai/api/v1@OPENROUTER_MODELS=YOUR_MODEL_1:free,YOUR_MODEL_2:free' \
     --set-secrets 'SECRET_KEY=flask-secret-key:latest,JWT_SECRET_KEY=jwt-secret-key:latest,GROQ_API_KEY=groq-api-key:latest,OPENROUTER_API_KEY=openrouter-api-key:latest'
   ```
   ※ `GROQ_MODELS` / `OPENROUTER_MODELS` の値にカンマを含むため、`^@^` で区切り文字を `@` に変更しています。
   ※ API キー・`SECRET_KEY`・`JWT_SECRET_KEY` は Secret Manager から読み込みます。シークレットの作成と権限の付与は README の「デプロイ」を参照してください。
