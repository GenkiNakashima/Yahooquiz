# Yahoo!ニュースクイズアプリ — ジャンル選択機能拡張 指示書

## 概要

Yahoo!ニュースをスクレイピングして記事を取得し、AIにクイズを生成させて対戦するアプリに「ジャンル選択」機能を追加する。

---

## 現状のフロー

```
ゲームスタート → モード選択 → クイズ開始
```

## 新フロー

```
ゲームスタート → ジャンル選択 → モード選択 → クイズ開始
```

---

## 追加するジャンル一覧

| ジャンルID | 表示名 | URL | アイコン（絵文字） |
|---|---|---|---|
| `top-picks` | ホットトピック | https://news.yahoo.co.jp/topics/top-picks | 🔥 |
| `domestic` | 国内 | https://news.yahoo.co.jp/topics/domestic | 🇯🇵 |
| `world` | グローバル | https://news.yahoo.co.jp/topics/world | 🌍 |
| `business` | 経済 | https://news.yahoo.co.jp/topics/business | 💹 |
| `entertainment` | エンタメ | https://news.yahoo.co.jp/topics/entertainment | 🎬 |
| `sports` | スポーツ | https://news.yahoo.co.jp/topics/sports | ⚽ |
| `it` | IT | https://news.yahoo.co.jp/topics/it | 💻 |
| `science` | 科学 | https://news.yahoo.co.jp/topics/science | 🔬 |
| `local` | ローカル | https://news.yahoo.co.jp/topics/local | 📍 |

---

## 変更方針

### 1. スクレイピング部分（バックエンド）

**変更内容:**
- 記事取得関数にジャンルパラメータを追加する
- ハードコードされた `https://news.yahoo.co.jp/topics/business` を、ジャンルに応じたURLに動的に切り替える
- ジャンルの定義は辞書またはマッピングで管理する

**実装イメージ:**
```python
GENRE_URLS = {
    'top-picks': 'https://news.yahoo.co.jp/topics/top-picks',
    'domestic': 'https://news.yahoo.co.jp/topics/domestic',
    'world': 'https://news.yahoo.co.jp/topics/world',
    'business': 'https://news.yahoo.co.jp/topics/business',
    'entertainment': 'https://news.yahoo.co.jp/topics/entertainment',
    'sports': 'https://news.yahoo.co.jp/topics/sports',
    'it': 'https://news.yahoo.co.jp/topics/it',
    'science': 'https://news.yahoo.co.jp/topics/science',
    'local': 'https://news.yahoo.co.jp/topics/local',
}
```

- 記事取得関数のシグネチャ: `def get_article(self, genre='business')` のように変更
- 呼び出し元からジャンルIDを受け取ってURLを切り替える
- スクレイピングロジック自体（newsFeed_list検出、data-ual-gotocontent、article>header>h1でのタイトル取得）は全ジャンル共通なので変更不要

### 2. APIルーティング（バックエンド）

**変更内容:**
- クイズ生成のAPIエンドポイントがジャンルパラメータを受け取れるようにする
- フロントエンドから送られたジャンルIDをスクレイピング関数に渡す

### 3. ジャンル選択画面（フロントエンド）

**変更内容:**
- モード選択画面と同一のUIレイアウトでジャンル選択画面を新規作成する
- 各ボタンにはジャンル名の右側にアイコン（絵文字）を表示する
- ジャンル選択後、選択されたジャンルIDを状態として保持し、モード選択画面に遷移する

**UI仕様:**
- ボタンのレイアウト・スタイル → モード選択画面と完全に同じ
- ボタンテキスト例: `「ホットトピック 🔥」` `「経済 💹」` `「スポーツ ⚽」`
- ジャンル選択 → モード選択 → クイズ開始 の順で画面遷移する

### 4. 画面遷移フロー（フロントエンド）

```
[スタート画面]
    ↓ スタートボタン
[ジャンル選択画面] ← 新規追加
    ↓ ジャンルボタン押下（選択ジャンルをstateに保持）
[モード選択画面] ← 既存（変更なし or 最小限の変更）
    ↓ モードボタン押下
[クイズ画面] ← ジャンル+モードを使って記事取得→クイズ生成
```

### 5. 対戦データ蓄積

- **ジャンル別にデータを分けない**
- 対戦結果の保存ロジック・DB/ストレージスキーマは変更しない
- 全ジャンルの対戦結果を同一のテーブル/配列に蓄積する（現状維持）

---

## 絶対に守るルール

1. **変更が必要ないファイルは絶対に触らない** — 影響範囲を最小限にする
2. スクレイピングロジック（HTML解析部分）は変更しない — URLの切り替えのみ
3. 対戦データの蓄積方式は変更しない — ジャンル別に分けない
4. モード選択画面の既存UIを壊さない
5. 既存の機能（経済ジャンルでのクイズ）が壊れないことを確認する

---

## 変更が必要なファイルの見極め方

以下の基準で判断する:

| 対象 | 変更する？ | 理由 |
|---|---|---|
| 記事取得（スクレイピング）関数 | ✅ する | ジャンルパラメータ追加、URL切り替え |
| APIエンドポイント | ✅ する | ジャンルパラメータの受け渡し |
| 画面遷移管理（state） | ✅ する | ジャンル選択ステップの追加 |
| ジャンル選択画面 | ✅ 新規作成 | モード選択UIを参考に作成 |
| モード選択画面 | ⚠️ 最小限 | 遷移元の変更があれば調整 |
| クイズ画面 | ❌ しない | ジャンルに依存しない |
| クイズ生成プロンプト | ❌ しない | 記事内容ベースなのでジャンル不要 |
| 対戦データ保存 | ❌ しない | ジャンル別にしない |
| スタイル/CSS | ⚠️ 最小限 | 既存モード選択のスタイルを流用 |
