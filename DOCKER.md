# Docker実行ガイド

## 方法1: Docker Compose（推奨）

PostgreSQLとアプリケーションを両方Dockerで起動します。

### 手順

1. **環境変数の設定**
   ```bash
   # .env.dockerファイルを編集して、実際のAPIキーを設定
   ```

2. **起動**
   ```bash
   docker-compose --env-file .env.docker up --build
   ```

3. **アクセス**
   - アプリケーション: http://localhost:8080
   - PostgreSQL: localhost:5432

4. **停止**
   ```bash
   docker-compose down
   ```

5. **データベースも削除して完全にクリーンアップ**
   ```bash
   docker-compose down -v
   ```

## 方法2: ホストのPostgreSQLに接続

既存のローカルPostgreSQLを使用する場合。

### 手順

1. **PostgreSQLがホストで起動していることを確認**
   ```bash
   psql -U nakashimagenki -d yahooquiz -c "SELECT 1"
   ```

2. **PostgreSQLがTCP/IP接続を許可していることを確認**

   `postgresql.conf`で以下を確認:
   ```
   listen_addresses = '*'  # または 'localhost'
   ```

   `pg_hba.conf`で以下を確認:
   ```
   host    all             all             0.0.0.0/0            trust
   # または
   host    all             all             127.0.0.1/32         trust
   ```

3. **Dockerコンテナを起動**
   ```bash
   docker run -p 8080:8080 --env-file .env.docker-host yahooquiz-app
   ```

   または、docker buildしてから:
   ```bash
   docker build -t yahooquiz-app .
   docker run -p 8080:8080 --env-file .env.docker-host yahooquiz-app
   ```

## トラブルシューティング

### データベース接続エラー

**エラー**: `connection to server at "localhost" ... Connection refused`

**解決方法**:
- Docker Composeを使用している場合: `DATABASE_URL`が`db`ホストを指しているか確認
- ホストのPostgreSQLを使用している場合: `host.docker.internal`を使用しているか確認

### 環境変数が読み込まれない

**解決方法**:
```bash
# 環境変数を確認
docker-compose config

# または個別に確認
docker run --env-file .env.docker yahooquiz-app env | grep GROQ_API_KEY
```

### マイグレーションエラー

**解決方法**:
```bash
# データベースを初期化
docker-compose exec app python migrations/init_db.py
```

## 本番環境デプロイ

### Google Cloud Run

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
     --set-env-vars DATABASE_URL=YOUR_DB_URL,GROQ_API_KEY=YOUR_KEY,JWT_SECRET_KEY=YOUR_SECRET
   ```
