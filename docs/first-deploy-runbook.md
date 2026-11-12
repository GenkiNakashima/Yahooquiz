# 初回デプロイ手順書（PR #16 / #17）

本番: プロジェクト `aiquizbattle` / Cloud Run サービス `yahooquiz`（`asia-northeast1`）/ Firestore `(default)`
デプロイ前の本番リビジョン: `yahooquiz-00023-cn8`（2026-05-07、旧 groq SDK 版）

- コマンドはすべてリポジトリのルートで実行します。シークレットの値はコマンドラインに書きません。
- ステップ 3〜6 は本番の Firestore / Secret Manager を直接操作します。**ステップ 3 の削除は元に戻せません。**
- **問題が起きたときは、前のリビジョンに戻すのではなく「修正して再デプロイする」のが基本です**（ステップ 9 を参照）。

```bash
# 以降のステップで共通に使う変数
PROJECT_ID=aiquizbattle
REGION=asia-northeast1
SERVICE=yahooquiz
```

---

## 0. 事前準備

```bash
gcloud config set project "$PROJECT_ID"
gcloud auth login                         # gcloud コマンド用
gcloud auth application-default login     # スクリプト（ステップ 3・4・8）が本番の Firestore に接続するため

# 現在のリビジョンを控えておく（参考。ステップ 9 のとおり、戻しても動かない可能性が高い）
gcloud run services describe "$SERVICE" --project "$PROJECT_ID" --region "$REGION" \
  --format='value(status.latestReadyRevisionName)'
```

---

## 1. PR #17 をマージ

```bash
gh pr merge 17 --merge --delete-branch
```

## 2. PR #16 をマージ

```bash
gh pr merge 16 --merge --delete-branch
git checkout develop && git pull
.venv/bin/pip install -r requirements.txt  # groq SDK → openai SDK に変わっているため
```

デプロイするコミットは、この時点の `develop` です（`main` に反映してからデプロイする運用なら、`main` に反映して `main` をチェックアウトしてください）。

---

## 3. テストデータの削除（`icons` 以外）

`FIRESTORE_EMULATOR_HOST` が設定されているとエミュレーターに接続してしまうため、外して実行します。

```bash
# 確認のみ（件数を表示。削除しない）
env -u FIRESTORE_EMULATOR_HOST .venv/bin/python scripts/delete_test_data.py --project "$PROJECT_ID"
#   期待値: users 2 / matches 11 / match_players 11 / points_transactions 4 /
#           auth_refresh_tokens 21 / user_icons 1 / quiz_sets 0 / icons（削除しない）6

# 実際に削除（プロジェクト ID の入力を求められる）
env -u FIRESTORE_EMULATOR_HOST .venv/bin/python scripts/delete_test_data.py --project "$PROJECT_ID" --apply
#   「削除後:」がすべて 0、icons が 6 のままであることを確認
```

## 4. 移行スクリプト

削除後なので、限定アイコン3件の `purchased_count` が 0 に設定されるだけです。

```bash
env -u FIRESTORE_EMULATOR_HOST .venv/bin/python scripts/migrate_user_icons.py --project "$PROJECT_ID"
#   期待値: user_icons_total 0 / limited_icons 3 / purchased_count_updated 3

env -u FIRESTORE_EMULATOR_HOST .venv/bin/python scripts/migrate_user_icons.py --project "$PROJECT_ID" --apply

# もう一度確認のみで実行し、purchased_count_updated が 0 になっていれば完了（冪等）
env -u FIRESTORE_EMULATOR_HOST .venv/bin/python scripts/migrate_user_icons.py --project "$PROJECT_ID"
```

---

## 5. Secret Manager（シークレット4つ）と secretAccessor の付与

### 5-1. Groq の新しい API キーを発行する

旧リビジョン（`yahooquiz-00023-cn8` など）の設定には、今の Groq の API キーが平文で残っています（リビジョンの設定は変更できません）。
そのため、Secret Manager には**新しく発行したキー**を登録し、今のキーはデプロイ後に削除します（ステップ 10）。

1. Groq のコンソール（https://console.groq.com/keys）で、新しい API キーを発行する（名前の例: `yahooquiz-prod-2026-11`）
2. 発行したキーは、次の 5-2 で `groq-api-key` に登録する（ファイルやメモには保存しない）
3. 今のキー（旧リビジョンに残っているキー）は、まだ削除しない（ステップ 10 で削除する）

### 5-2. シークレットの作成

```bash
# API キー（実行後に値を貼り付けて Enter。画面にも履歴にも残らない）
# groq-api-key には、5-1 で発行した「新しい」キーを貼り付ける
read -rs VALUE && printf '%s' "$VALUE" | gcloud secrets create groq-api-key --project "$PROJECT_ID" --replication-policy=automatic --data-file=- ; unset VALUE
read -rs VALUE && printf '%s' "$VALUE" | gcloud secrets create openrouter-api-key --project "$PROJECT_ID" --replication-policy=automatic --data-file=- ; unset VALUE

# SECRET_KEY / JWT_SECRET_KEY はランダムに生成して、表示せずにそのまま登録（2つは別の値になる）
python3 -c "import secrets; print(secrets.token_hex(32), end='')" | gcloud secrets create flask-secret-key --project "$PROJECT_ID" --replication-policy=automatic --data-file=-
python3 -c "import secrets; print(secrets.token_hex(32), end='')" | gcloud secrets create jwt-secret-key --project "$PROJECT_ID" --replication-policy=automatic --data-file=-
```

### 5-3. secretAccessor の付与と確認

```bash
# Cloud Run のサービスアカウント（本番は既定の Compute Engine サービスアカウント）に読み取り権限を付与
SA=$(gcloud run services describe "$SERVICE" --project "$PROJECT_ID" --region "$REGION" --format='value(spec.template.spec.serviceAccountName)')
SA=${SA:-$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')-compute@developer.gserviceaccount.com}
for secret in flask-secret-key jwt-secret-key groq-api-key openrouter-api-key; do
  gcloud secrets add-iam-policy-binding "$secret" --project "$PROJECT_ID" \
    --member="serviceAccount:$SA" --role=roles/secretmanager.secretAccessor
done

# 確認: 4つのシークレットがあり、それぞれに secretAccessor が付いている
gcloud secrets list --project "$PROJECT_ID" --format='value(name)'
for secret in flask-secret-key jwt-secret-key groq-api-key openrouter-api-key; do
  echo "$secret: $(gcloud secrets get-iam-policy "$secret" --project "$PROJECT_ID" --format='value(bindings.role)')"
done
```

OpenRouter を使わない場合は `openrouter-api-key` を作らず（5-2 の2行目と、5-3 の2つの `for` から外す）、ステップ 7 で `_SECRETS` を上書きします。

## 6. TTL の設定と削除保護の有効化

```bash
gcloud firestore fields ttls update expires_at --collection-group=quiz_sets --enable-ttl --project="$PROJECT_ID"
gcloud firestore databases update --database='(default)' --delete-protection --project="$PROJECT_ID"

# 確認
gcloud firestore fields ttls list --project="$PROJECT_ID"
gcloud firestore databases describe --database='(default)' --project="$PROJECT_ID" --format='value(deleteProtectionState)'
#   → DELETE_PROTECTION_ENABLED
```

---

## 7. デプロイ

> **初回デプロイで全ユーザーがログアウトされ、既存の JWT も無効になります**（ステップ 3 でユーザーを削除するため、今回は実質的な影響はありません）。

```bash
git status                     # 作業ツリーがきれいで、ステップ 2 のコミットにいることを確認
git log --oneline -1

# OpenRouter を使う場合（OPENROUTER_MODELS は事前に無料モデルを確認して指定）
#   curl -s https://openrouter.ai/api/v1/models | jq -r '.data[].id' | grep ':free$'
gcloud builds submit --config cloudbuild.yaml --project "$PROJECT_ID" \
  --substitutions="^@^COMMIT_SHA=$(git rev-parse HEAD)@_OPENROUTER_MODELS=YOUR_MODEL_1:free,YOUR_MODEL_2:free"

# OpenRouter を使わない場合
gcloud builds submit --config cloudbuild.yaml --project "$PROJECT_ID" \
  --substitutions="^@^COMMIT_SHA=$(git rev-parse HEAD)@_SECRETS=SECRET_KEY=flask-secret-key:latest,JWT_SECRET_KEY=jwt-secret-key:latest,GROQ_API_KEY=groq-api-key:latest"
```

- `COMMIT_SHA` は手動の `gcloud builds submit` では空になるため、必ず渡します（空だとイメージ名が不正になりビルドが失敗します）。
- アップロードされるのはソースのみです（`.env` と `.venv` は `.gitignore` により除外されることを確認済み）。
- ビルドやデプロイが失敗した場合、トラフィックは前のリビジョンのまま変わりません。

---

## 8. デプロイ後の動作確認

### 8-1. 設定の確認

```bash
# 新しいリビジョンが Ready で、トラフィックが 100% 向いている
gcloud run services describe "$SERVICE" --project "$PROJECT_ID" --region "$REGION" \
  --format='value(status.latestReadyRevisionName,status.traffic)'

# キー類が value: ではなく valueFrom.secretKeyRef になっている（平文の GROQ_API_KEY が消えている）
gcloud run services describe "$SERVICE" --project "$PROJECT_ID" --region "$REGION" \
  --format='yaml(spec.template.spec.containers[0].env)' | grep -B1 -A3 -E 'API_KEY|SECRET_KEY'

# 起動ログにエラーがない（「利用する LLM プロバイダ」の行が出ている）
gcloud run services logs read "$SERVICE" --project "$PROJECT_ID" --region "$REGION" --limit 50
```

### 8-2. 画面での確認（https://yahooquiz-pexvbq6aaq-an.a.run.app）

ステップ 3 でユーザーをすべて削除しているため、新しく登録します。

1. **ユーザー登録・ログイン**: 新しいユーザーを登録し、そのメールアドレスとパスワードでログインできる
2. **1ゲームのプレイ**: ジャンルを選んで5問を最後まで遊ぶ（問題が4択で表示され、記事番号が問題文に入っていない）
3. **ポイント付与**: AI に勝つと 100 ポイント付与される（ダッシュボードで確認。負けた場合は付与されないので、勝つまでもう1ゲーム）
4. **アイコン購入**: ショップでブロンズバッジ（100 ポイント）を購入 → 残高が 0 になり、所持アイコンに表示される。もう一度購入しようとすると「所持済み」で断られる

### 8-3. データとログの確認

```bash
# 件数だけを確認（確認のみ。削除しない）
env -u FIRESTORE_EMULATOR_HOST .venv/bin/python scripts/delete_test_data.py --project "$PROJECT_ID"
#   1ゲーム勝って1回購入した場合の目安: users 1 / matches 1 / match_players 1 / points_transactions 2（+100, -100）/
#   user_icons 1 / quiz_sets 1 / auth_refresh_tokens 1以上   ※ --apply は付けないこと

# クイズ生成の成功ログ（どのモデルで生成されたか・フォールバックの理由）
gcloud run services logs read "$SERVICE" --project "$PROJECT_ID" --region "$REGION" --limit 200 | grep -E 'クイズ生成成功|LLM provider'
```

---

## 9. 問題が起きたとき

### 基本: 修正して再デプロイする

**前のリビジョン（`yahooquiz-00023-cn8`）に戻しても、動かない可能性が高いです。**

- 2026年5月のコードで、提供終了した Llama のモデルを使っています。
- 起動時に LLM のテスト呼び出しを行い、失敗すると起動しません。そのため、戻してもコンテナが起動しない（＝サービスが止まる）可能性が高いです。
- データも戻りません。ステップ 3 の削除と 4 の移行は、リビジョンを戻しても元に戻りません。

そのため、問題が起きたときは次の順で対応します。

1. ログで原因を確認する
   ```bash
   gcloud run services logs read "$SERVICE" --project "$PROJECT_ID" --region "$REGION" --limit 200
   ```
2. 設定の問題（シークレットの不足・権限・環境変数）なら、設定を直してステップ 7 をやり直す
   - デプロイ自体が失敗した場合は、トラフィックは前のリビジョンのまま変わっていません
3. コードの問題なら、修正の PR を作ってマージし、ステップ 7 をやり直す

### 参考: リビジョンを切り替えるコマンド

2回目以降のデプロイで、直前の**正常に動いていたリビジョン**に戻す場合に使います（初回デプロイの直後に `yahooquiz-00023-cn8` へ戻すことは推奨しません）。

```bash
# リビジョンの一覧（新しい順）
gcloud run revisions list --service "$SERVICE" --project "$PROJECT_ID" --region "$REGION" --limit 5

# 指定したリビジョンにトラフィックを 100% 切り替える（新しいリビジョンは削除されずに残る）
gcloud run services update-traffic "$SERVICE" --project "$PROJECT_ID" --region "$REGION" \
  --to-revisions=REVISION_NAME=100

# 修正して再デプロイしたあと、最新のリビジョンに戻す
gcloud run services update-traffic "$SERVICE" --project "$PROJECT_ID" --region "$REGION" --to-latest
```

---

## 10. Groq の古い API キーを削除する

ステップ 8 の動作確認がすべて済んでから行います（新しいリビジョンが Secret Manager の新しいキーで動いていることを確認したあと）。

1. Groq のコンソール（https://console.groq.com/keys）で、**古いキー**（5-1 より前から使っていたキー）を削除する
   - 新しいキー（5-1 で発行したキー）を間違えて削除しないよう、名前と作成日で確認する
2. 削除後、もう一度 1ゲームを遊び、クイズが生成されることを確認する（新しいキーで動いている）
   ```bash
   gcloud run services logs read "$SERVICE" --project "$PROJECT_ID" --region "$REGION" --limit 100 | grep -E 'クイズ生成成功|LLM provider'
   ```

これで、旧リビジョンの設定に平文で残っているキーも使えなくなります。

---

## デプロイが安定したあとに

- サービスアカウントの Editor ロールの見直し（予定どおり別途）。
