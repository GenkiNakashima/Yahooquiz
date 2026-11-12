"""Groq の各モデルを、本番と同じバッチ生成（5問）のプロンプトとパラメータで1回ずつ呼び出して確認する。

使い方（リポジトリのルートで）:
    .venv/bin/python scripts/check_groq_models.py

確認内容:
- API エラーにならないか（reasoning_effort / reasoning_format が受け付けられるか）
- content に推論テキスト（<think> タグ）が混ざっていないか
- content が JSON としてパースでき、5問すべてがバリデーションを通るか
- トークン使用量（prompt_tokens / completion_tokens）が TPM の予算内か
- finish_reason が "length"（max_tokens で出力が途中で切れた）になっていないか

記事は本番と同じく Yahoo!ニュースから5件取得する（取得できない分はサンプル記事）。
OpenRouter は呼ばない。API キーとプロンプトは表示しない。合計で len(GROQ_MODELS) 回だけ呼び出す。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import openai  # noqa: E402

import llm_client  # noqa: E402
from honban import QuizGenerator  # noqa: E402

QUIZ_COUNT = 5
# Groq 無料枠の TPM は 8,000。入力 + MAX_TOKENS をこの値以下に収める
TOKEN_BUDGET = 7000


def main():
    groq = llm_client._groq_candidates()
    if not groq:
        print("GROQ_API_KEY が設定されていません（.env を確認してください）")
        return 1

    articles = QuizGenerator().get_news_articles(QUIZ_COUNT, 'business')
    texts = [a['content'] for a in articles]
    messages = llm_client.build_messages(texts)
    print(f"記事 {len(texts)} 件（プロンプトでの文字数: "
          f"{[len(llm_client.truncate_article(t)) for t in texts]}）\n")

    failed = False
    for cand in groq:
        print(f"=== {cand['label']}  max_tokens={cand['max_tokens']}  extra_body={cand['extra_body']}")
        try:
            resp = llm_client.request_completion(cand, messages)
        except openai.APIStatusError as e:
            print(f"  NG: status={e.status_code} {type(e).__name__}: {e.message}\n")
            failed = True
            continue
        except openai.APIError as e:
            print(f"  NG: {type(e).__name__}: {e}\n")
            failed = True
            continue

        choice = resp.choices[0]
        msg = choice.message
        content = msg.content or ''
        usage = resp.usage
        prompt_tokens = getattr(usage, 'prompt_tokens', None)
        completion_tokens = getattr(usage, 'completion_tokens', None)
        truncated = choice.finish_reason == 'length'
        print(f"  finish_reason={choice.finish_reason}{'  ← NG（出力が max_tokens で途中で切れた）' if truncated else ''}")
        failed |= truncated
        print(f"  usage: prompt_tokens={prompt_tokens} completion_tokens={completion_tokens} "
              f"total_tokens={getattr(usage, 'total_tokens', None)}")
        if prompt_tokens is not None:
            planned = prompt_tokens + cand['max_tokens']
            mark = 'OK' if planned <= TOKEN_BUDGET else 'NG'
            print(f"  予算: prompt_tokens + max_tokens = {planned} (上限 {TOKEN_BUDGET}) {mark}")
            failed |= planned > TOKEN_BUDGET
        reasoning = getattr(msg, 'reasoning', None)
        print(f"  message.reasoning: {'あり (' + str(len(reasoning)) + '文字)' if reasoning else 'なし'}")
        leaked = '<think>' in content or '</think>' in content
        print(f"  content に <think> タグ: {'あり ← NG' if leaked else 'なし'}")
        failed |= leaked

        try:
            results, errors = llm_client._validate_quiz_items(llm_client._parse_json(content), QUIZ_COUNT)
        except ValueError as e:
            print(f"  NG: パース/形式エラー: {e}")
            print(f"  content(先頭300文字): {content[:300]}\n")
            failed = True
            continue
        for i, quiz in enumerate(results, start=1):
            if quiz:
                print(f"  Q{i}: {quiz['question']}  正解={quiz['answer']!r}  選択肢={quiz['choices']}")
        if errors:
            print(f"  NG: 検証失敗 {errors}")
            failed = True
        elif truncated:
            print(f"  NG: 検証は通過したが finish_reason=length のため、本番では次の候補に切り替わる")
        else:
            print(f"  OK: {QUIZ_COUNT} 問すべて検証を通過")
        print()

    print("結果:", "NG あり" if failed else "すべて OK")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
