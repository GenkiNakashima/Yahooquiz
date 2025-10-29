# Firebase 統合セットアップガイド

このアプリケーションを Firebase と統合してクイズ結果をデータベースに保存するためのセットアップガイドです。

## 必要な作業

### 1. Firebase プロジェクトの作成

1. [Firebase Console](https://console.firebase.google.com/) にアクセス
2. 「プロジェクトを作成」をクリック
3. プロジェクト名を入力（例: `yaooquiz-app`）
4. Google Analytics は任意で有効化
5. プロジェクトを作成

### 2. Firestore データベースの設定

1. Firebase Console でプロジェクトを開く
2. 左メニューから「Firestore Database」を選択
3. 「データベースを作成」をクリック
4. セキュリティルールを選択：
   - **テストモード**（開発用）：誰でも読み書き可能
   - **本番モード**（推奨）：認証が必要

### 3. サービスアカウントキーの取得

1. Firebase Console で「プロジェクトの設定」（歯車アイコン）をクリック
2. 「サービスアカウント」タブを選択
3. 「新しい秘密鍵の生成」をクリック
4. JSON ファイルがダウンロードされる

### 4. 環境変数の設定

#### 方法 1: 環境変数として設定

```bash
# Windows (PowerShell)
$env:FIREBASE_SERVICE_ACCOUNT='{"type":"service_account","project_id":"your-project-id",...}'

# Linux/Mac
export FIREBASE_SERVICE_ACCOUNT='{"type":"service_account","project_id":"your-project-id",...}'
```

#### 方法 2: JSON ファイルとして配置

1. ダウンロードした JSON ファイルをプロジェクトルートに配置
2. ファイル名を以下のいずれかに変更：
   - `firebase-service-account.json`
   - `service-account.json`
   - `firebase-adminsdk.json`

### 5. 依存関係のインストール

```bash
pip install -r requirements.txt
```

### 6. アプリケーションの起動

```bash
python app.py
```

## データ構造

### quiz_results コレクション

```json
{
  "player_score": 3,
  "ai_score": 2,
  "total_rounds": 5,
  "ai_level": "normal",
  "game_duration": 45.2,
  "timestamp": "2024-01-15T10:30:00Z",
  "winner": "player"
}
```

### question_results コレクション

```json
{
  "question": "問題文",
  "article_title": "記事タイトル",
  "article_url": "記事URL",
  "correct_answer": "正解",
  "player_answer": "プレイヤーの回答",
  "player_time": 8.5,
  "ai_time": 12.3,
  "result_type": "correct",
  "ai_level": "normal",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

## セキュリティルール（本番環境用）

Firestore セキュリティルール：

```javascript
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    // 読み取りは誰でも可能
    match /{document=**} {
      allow read: if true;
    }

    // 書き込みは認証されたユーザーのみ（将来の拡張用）
    match /{document=**} {
      allow write: if request.auth != null;
    }
  }
}
```

## 新しい機能

### 統計ページ (`/stats`)

- 全体統計の表示
- 最近の結果の表示
- AI レベル別の分布
- 勝率の計算

### API エンドポイント

- `GET /api/recent-results?limit=10`: 最近の結果を取得
- `GET /api/statistics`: 統計情報を取得

## トラブルシューティング

### よくある問題

1. **Firebase 初期化エラー**

   - サービスアカウントキーが正しく設定されているか確認
   - 環境変数または JSON ファイルのパスが正しいか確認

2. **権限エラー**

   - Firestore のセキュリティルールを確認
   - サービスアカウントに適切な権限があるか確認

3. **接続エラー**
   - インターネット接続を確認
   - Firebase プロジェクトの設定を確認

### ログの確認

アプリケーションのコンソール出力で以下のメッセージを確認：

```
Firebase: 初期化成功
Firebase: クイズ結果を保存しました (ID: xxxxx)
```

## 今後の拡張予定

- ユーザー認証の追加
- 個人の成績履歴
- ランキング機能
- リアルタイム統計更新

