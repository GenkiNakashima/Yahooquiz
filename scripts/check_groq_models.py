"""Groq の各モデルを、本番と同じバッチ生成（5問）のプロンプトとパラメータで1回ずつ呼び出して確認する。

使い方（リポジトリのルートで）:
    .venv/bin/python scripts/check_groq_models.py             # 各モデル1回（合計 len(GROQ_MODELS) 回）
    .venv/bin/python scripts/check_groq_models.py --compare   # 変更前のプロンプトとの出力トークンの比較（合計 2 × len(GROQ_MODELS) 回）

確認内容:
- API エラーにならないか（reasoning_effort / reasoning_format が受け付けられるか）
- content に推論テキスト（<think> タグ）が混ざっていないか
- content が JSON としてパースでき、5問すべてがバリデーションを通るか
- トークン使用量（prompt_tokens / completion_tokens）が TPM の予算内か
- finish_reason が "length"（max_tokens で出力が途中で切れた）になっていないか
- 各問題の answer_type・aliases と、本番と同じ選び方で記述式に選ばれた問題（選び方はランダムなので1例）

--compare を付けると、同じ記事で「answer_type・aliases を追加する前のプロンプト」も呼び出し、
出力トークン（completion_tokens）の増加量を表示する。Qwen の無料枠は出力 1,000 トークン/分のため、
同じモデルの2回目の呼び出しは1回目から60秒以上あけてから行う（合計で数分かかる）。

記事は本番と同じく Yahoo!ニュースから5件取得する（取得できない分はサンプル記事）。
OpenRouter は呼ばない。API キーとプロンプトは表示しない。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import openai  # noqa: E402

import answer_checker  # noqa: E402
import llm_client  # noqa: E402
from honban import ANSWER_FORMAT_TEXT, QuizGenerator, assign_answer_formats  # noqa: E402

QUIZ_COUNT = 5
# Groq 無料枠の TPM は 8,000。入力 + MAX_TOKENS をこの値以下に収める
TOKEN_BUDGET = 7000
# 同じモデルを続けて呼ぶときの間隔（Qwen の出力トークン/分の上限に当たらないように）
SAME_MODEL_INTERVAL_SECONDS = 61

# answer_type・aliases を追加する前（develop の 2fd8d03 時点）のプロンプト。--compare の比較にだけ使う
BASELINE_USER_PROMPT_TEMPLATE = """以下の {n} 件のニュース記事から、4択クイズを記事ごとに1問ずつ、合計 {n} 問作成してください。

# 条件
- 各記事から1問ずつ作り、各問題の article_index に、その問題を作った記事の番号（「## 記事1」なら 1）を入れる。
  すべての記事番号（1〜{n}）をちょうど1回ずつ使い、同じ記事番号を2つの問題に使わない
- 同じ内容の記事が複数ある場合も、問題同士は重複させない
- 問題文は記事を読めば答えられる内容にする
- 問題文・解説に記事番号や「記事3で」「この記事では」のような記事を指す表現を含めない（プレイヤーには1問につき1記事だけが表示され、記事番号は見えない）
- 選択肢はちょうど4つ。正解は1つだけで、選択肢同士は重複させない
- 選択肢はボタンに表示するため、それぞれ短い単語・語句にする（目安：15文字以内。記号や補足説明を含めない）
- 不正解の選択肢も正解と同じ種類の語句にする（人名なら人名、数値なら数値）
- 数値の選択肢は等間隔にせず、ばらつきを持たせる（例: 10・20・30・40 のような並びにしない）
- answer_index は正解の選択肢の位置（0〜3）
- 解説は記事の内容に基づいて1〜2文で書く

# 出力形式（この JSON オブジェクトのみを出力。quizzes はちょうど {n} 要素）
{{"quizzes": [{{"article_index": 1, "question": "問題文", "choices": ["選択肢1", "選択肢2", "選択肢3", "選択肢4"], "answer_index": 0, "explanation": "解説"}}]}}

# 記事
"""


def _baseline_messages(texts):
    current = llm_client.build_user_prompt(texts)
    articles_part = current[len(llm_client.USER_PROMPT_TEMPLATE.format(n=len(texts))):]
    return [
        {'role': 'system', 'content': llm_client.SYSTEM_PROMPT},
        {'role': 'user', 'content': BASELINE_USER_PROMPT_TEMPLATE.format(n=len(texts)) + articles_part},
    ]


def _reasoning_tokens(usage):
    details = getattr(usage, 'completion_tokens_details', None)
    if details is None:
        return None
    if isinstance(details, dict):
        return details.get('reasoning_tokens')
    return getattr(details, 'reasoning_tokens', None)


def _call(cand, messages):
    """Returns: (response, エラーの説明)"""
    try:
        return llm_client.request_completion(cand, messages), None
    except openai.APIStatusError as e:
        return None, f"status={e.status_code} {type(e).__name__}: {e.message}"
    except openai.APIError as e:
        return None, f"{type(e).__name__}: {e}"


def _print_usage(resp):
    usage = resp.usage
    reasoning = _reasoning_tokens(usage)
    print(f"  usage: prompt_tokens={getattr(usage, 'prompt_tokens', None)} "
          f"completion_tokens={getattr(usage, 'completion_tokens', None)}"
          f"{'' if reasoning is None else f' (うち推論 {reasoning})'} "
          f"total_tokens={getattr(usage, 'total_tokens', None)}")


def _check(cand, resp, articles):
    """1回分の結果を表示する。Returns: NG があれば True"""
    failed = False
    choice = resp.choices[0]
    msg = choice.message
    content = msg.content or ''
    usage = resp.usage
    prompt_tokens = getattr(usage, 'prompt_tokens', None)
    truncated = choice.finish_reason == 'length'
    print(f"  finish_reason={choice.finish_reason}{'  ← NG（出力が max_tokens で途中で切れた）' if truncated else ''}")
    failed |= truncated
    _print_usage(resp)
    completion_tokens = getattr(usage, 'completion_tokens', None)
    if completion_tokens is not None:
        print(f"  max_tokens に対する出力: {completion_tokens}/{cand['max_tokens']} "
              f"（残り {cand['max_tokens'] - completion_tokens}）")
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
        data = llm_client._parse_json(content)
        results, errors = llm_client._validate_quiz_items(data, QUIZ_COUNT)
    except ValueError as e:
        print(f"  NG: パース/形式エラー: {e}")
        print(f"  content(先頭300文字): {content[:300]}")
        return True
    raw_order = [q.get('article_index') if isinstance(q, dict) else None for q in data.get('quizzes', [])]
    print(f"  出力の並び（article_index）: {raw_order}"
          f"{'' if raw_order == list(range(1, QUIZ_COUNT + 1)) else '  ← 記事の順番と違う（article_index で並べ替える）'}")

    # 本番と同じ選び方で記述式の問題を選ぶ（ランダムなので1例。検証に失敗した問題は除く）
    valid = [q for q in results if q]
    assign_answer_formats(valid)
    text_count = sum(1 for q in valid if q.get('answer_format') == ANSWER_FORMAT_TEXT)

    # results は article_index で記事の順番に並べ替え済み（Q{i} は記事{i} から作られた問題）
    for i, (quiz, article) in enumerate(zip(results, articles), start=1):
        print(f"  Q{i} [記事{i}: {article['title']}]")
        if quiz:
            form = '記述式' if quiz.get('answer_format') == ANSWER_FORMAT_TEXT else '4択'
            print(f"      [{form}] {quiz['question']}")
            print(f"      正解={quiz['answer']!r}  answer_type={quiz['answer_type']}  aliases={quiz['aliases']}")
            print(f"      選択肢={quiz['choices']}")
        else:
            print("      （問題なし: 検証失敗・article_index の重複など。本番ではこの記事の問題だけ再生成する）")
    eligible = sum(1 for q in valid if q['answer_type'] in answer_checker.TEXT_ANSWER_TYPES)
    print(f"  記述式にできる問題（answer_type が number / name）: {eligible} 問"
          f"{'' if eligible >= 3 else '  ← プロンプトの指示（3問以上）を満たしていない'}")
    print(f"  記述式に選ばれた問題: {text_count} 問（2〜3問。対象が2問未満ならその数）")
    if errors:
        print(f"  NG: 検証失敗 {errors}")
        failed = True
    elif truncated:
        print("  NG: 検証は通過したが finish_reason=length のため、本番では次の候補に切り替わる")
    else:
        print(f"  OK: {QUIZ_COUNT} 問すべて検証を通過")
    return failed


def main(argv):
    compare = '--compare' in argv
    groq = llm_client._groq_candidates()
    if not groq:
        print("GROQ_API_KEY が設定されていません（.env を確認してください）")
        return 1

    articles = QuizGenerator().get_news_articles(QUIZ_COUNT, 'business')
    texts = [a['content'] for a in articles]
    messages = llm_client.build_messages(texts)
    print(f"記事 {len(texts)} 件（プロンプトでの文字数: "
          f"{[len(llm_client.truncate_article(t)) for t in texts]}）")
    for i, a in enumerate(articles, start=1):
        print(f"  記事{i}: {a['title']}")
    print()

    failed = False
    baseline_tokens = {}
    called_at = {}
    if compare:
        print("##### 変更前のプロンプト（answer_type・aliases なし）")
        baseline = _baseline_messages(texts)
        for cand in groq:
            print(f"=== {cand['label']}  max_tokens={cand['max_tokens']}  extra_body={cand['extra_body']}")
            called_at[cand['label']] = time.monotonic()
            resp, error = _call(cand, baseline)
            if error:
                print(f"  NG: {error}\n")
                continue
            print(f"  finish_reason={resp.choices[0].finish_reason}")
            _print_usage(resp)
            baseline_tokens[cand['label']] = getattr(resp.usage, 'completion_tokens', None)
            print()
        print("##### 変更後のプロンプト（本番と同じ）")

    current_tokens = {}
    for cand in groq:
        if compare and cand['label'] in called_at:
            wait = SAME_MODEL_INTERVAL_SECONDS - (time.monotonic() - called_at[cand['label']])
            if wait > 0:
                print(f"（{cand['label']} の出力トークン/分の上限に当たらないよう {wait:.0f} 秒待ちます）")
                time.sleep(wait)
        print(f"=== {cand['label']}  max_tokens={cand['max_tokens']}  extra_body={cand['extra_body']}")
        resp, error = _call(cand, messages)
        if error:
            print(f"  NG: {error}\n")
            failed = True
            continue
        current_tokens[cand['label']] = getattr(resp.usage, 'completion_tokens', None)
        failed |= _check(cand, resp, articles)
        print()

    if compare:
        print("##### 出力トークン（completion_tokens）の増加量（同じ記事での比較。推論トークンを含む）")
        for cand in groq:
            before, after = baseline_tokens.get(cand['label']), current_tokens.get(cand['label'])
            if before is None or after is None:
                print(f"  {cand['label']}: 比較できません（どちらかの呼び出しが失敗）")
                continue
            print(f"  {cand['label']}: 変更前 {before} → 変更後 {after}（{after - before:+d}）"
                  f"  max_tokens={cand['max_tokens']} に対して残り {cand['max_tokens'] - after}")

    print("結果:", "NG あり" if failed else "すべて OK")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
