"""OpenRouter の各モデル（OPENROUTER_MODELS）を、本番と同じバッチ生成（5問）のプロンプトとパラメータで1回ずつ呼び出して確認する。

使い方（リポジトリのルートで）:
    .venv/bin/python scripts/check_openrouter_models.py
    .venv/bin/python scripts/check_openrouter_models.py vendor/model-a:free vendor/model-b:free   # モデルを指定する場合

本番では OPENROUTER_MODELS を models 配列で渡して OpenRouter 側で自動フォールバックさせるが、このスクリプトは
どのモデルが使えるかを確かめるため、models 配列を使わずに各モデルを個別に呼び出す。
それ以外のパラメータ（response_format=json_object・max_tokens・temperature・ヘッダー）は本番と同じ。

確認内容:
- API エラーの有無。429 の場合は、upstream（モデルを提供しているプロバイダ）の共有枠によるものか、
  OpenRouter 自身の無料モデルの上限（1分あたり・1日あたり）によるものか
- HTTP 200 でも本文に error が入っていないか（OpenRouter は生成中のエラーを 200 で返すことがある）
- 実際に応答したモデル、finish_reason、トークン使用量
- 本文に推論の過程（<think> タグや JSON の前後の文章）が混ざっていないか、message.reasoning の有無
- 生成された5問と、本番と同じ検証の結果

記事は本番と同じく Yahoo!ニュースから5件取得する（取得できない分はサンプル記事）。
Groq は呼ばない。API キーとプロンプト本文は表示しない。合計でモデルの数だけ呼び出す（無料モデルの1日の上限を消費する）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import openai  # noqa: E402
import requests  # noqa: E402

import llm_client  # noqa: E402
from honban import QuizGenerator  # noqa: E402

QUIZ_COUNT = 5
DEFAULT_BASE_URL = 'https://openrouter.ai/api/v1'
RATE_LIMIT_HEADERS = ('x-ratelimit-limit', 'x-ratelimit-remaining', 'x-ratelimit-reset', 'retry-after')


def _extra(obj, name):
    """SDK の型に無いフィールド（OpenRouter 独自のもの）を取り出す"""
    value = getattr(obj, name, None)
    if value is None:
        value = (getattr(obj, 'model_extra', None) or {}).get(name)
    return value


def _error_dict(body):
    if isinstance(body, dict):
        inner = body.get('error')
        return inner if isinstance(inner, dict) else body
    return {}


def _describe_429(e):
    """429 が upstream の共有枠によるものか、OpenRouter 自身の上限によるものかを判定して表示する"""
    err = _error_dict(e.body)
    metadata = err.get('metadata') if isinstance(err.get('metadata'), dict) else {}
    headers = {k: v for k, v in e.response.headers.items() if k.lower() in RATE_LIMIT_HEADERS}
    provider = metadata.get('provider_name')
    provider_code = metadata.get('provider_code')
    message = str(err.get('message') or '')

    if provider or provider_code or 'upstream' in message.lower():
        origin = (f"upstream（プロバイダ {provider or '不明'} の共有枠。"
                  "時間をおけば回復することが多い）")
    elif any(k.lower().startswith('x-ratelimit') for k in headers):
        origin = "OpenRouter 自身の無料モデルの上限（1分あたり、または1日あたり）"
    else:
        origin = "判別できない（provider_code / X-RateLimit-* ヘッダーのどちらも無い）"
    print(f"  429 の原因: {origin}")
    if provider_code:
        print(f"  provider_code={provider_code}")
    if metadata.get('error_type'):
        print(f"  error_type={metadata['error_type']}")
    if headers:
        print(f"  ヘッダー: {headers}")
    if message:
        print(f"  message: {message[:200]}")


def _print_free_quota(base_url, api_key):
    """無料モデルの1日の残り回数（GET /key）。キーの中身は表示しない"""
    try:
        r = requests.get(f"{base_url.rstrip('/')}/key", headers={'Authorization': f'Bearer {api_key}'}, timeout=10)
        r.raise_for_status()
        data = r.json().get('data', {})
    except Exception as e:
        print(f"無料モデルの1日の残り回数: 取得できませんでした（{type(e).__name__}）")
        return
    quota = data.get('free_model_daily_requests')
    if quota:
        print(f"無料モデルの1日の利用状況（UTC）: used={quota.get('used')} limit={quota.get('limit')} "
              f"remaining={quota.get('remaining')}")
    else:
        print("無料モデルの1日の利用状況: レスポンスに free_model_daily_requests がありません")


def _check_content(content):
    """
    推論の過程が混ざっていないか（<think> タグ、JSON の前後の文章）を確認する。
    Returns: [(理由, 本番で使えないか)] のリスト
    """
    problems = []
    if '<think>' in content or '</think>' in content:
        problems.append(('<think> タグがある', True))
    text = content.strip()
    if text.startswith('```'):
        problems.append(('コードブロックで囲まれている（本番では取り除いて読むので問題なし）', False))
        text = text.split('\n', 1)[1] if '\n' in text else ''
        text = text.rstrip().removesuffix('```').rstrip()
    if text and not text.startswith('{'):
        problems.append((f'JSON の前に文章がある（{len(text.split("{", 1)[0])} 文字）', True))
    if text and not text.endswith('}'):
        problems.append(('JSON の後ろに文章がある、または途中で切れている', True))
    return problems


def main(argv):
    api_key = os.environ.get('OPENROUTER_API_KEY', '').strip()
    base_url = os.environ.get('OPENROUTER_BASE_URL', '').strip() or DEFAULT_BASE_URL
    models = argv or [m.strip() for m in os.environ.get('OPENROUTER_MODELS', '').split(',') if m.strip()]
    if not api_key:
        print("OPENROUTER_API_KEY が設定されていません（.env を確認してください）")
        return 1
    if not models:
        print("OPENROUTER_MODELS が設定されていません（.env に書くか、引数でモデルを指定してください）")
        return 1

    # 本番の OpenRouter 候補と同じパラメータで、models 配列だけを外して1モデルずつ呼ぶ
    base = {
        'provider': 'openrouter',
        'api_key': api_key,
        'base_url': base_url,
        'extra_body': {},
        'max_tokens': llm_client.MAX_TOKENS,
        'temperature': 0.7,
        'default_headers': {
            'HTTP-Referer': os.environ.get('OPENROUTER_SITE_URL') or 'https://github.com/GenkiNakashima/Yahooquiz',
            'X-Title': os.environ.get('OPENROUTER_APP_NAME') or 'Yahooquiz',
        },
    }

    _print_free_quota(base_url, api_key)
    articles = QuizGenerator().get_news_articles(QUIZ_COUNT, 'business')
    messages = llm_client.build_messages([a['content'] for a in articles])
    print(f"記事 {len(articles)} 件（プロンプトでの文字数: "
          f"{[len(llm_client.truncate_article(a['content'])) for a in articles]}）")
    for i, a in enumerate(articles, start=1):
        print(f"  記事{i}: {a['title']}")
    print()

    usable = []
    for model in models:
        cand = {**base, 'label': f'openrouter:{model}', 'model': model}
        print(f"=== {model}  max_tokens={cand['max_tokens']}  temperature={cand['temperature']}")
        try:
            resp = llm_client.request_completion(cand, messages)
        except openai.APIStatusError as e:
            print(f"  NG: status={e.status_code} {type(e).__name__}")
            if e.status_code == 429:
                _describe_429(e)
            else:
                err = _error_dict(e.body)
                print(f"  message: {str(err.get('message') or e.message)[:200]}")
            print()
            continue
        except openai.APIError as e:
            print(f"  NG: {type(e).__name__}: {str(e)[:200]}\n")
            continue

        body_error = _extra(resp, 'error')
        if body_error or not resp.choices:
            err = _error_dict({'error': body_error}) if body_error else {}
            print(f"  NG: HTTP 200 だが本文にエラー: code={err.get('code')} message={str(err.get('message'))[:200]}\n")
            continue

        choice = resp.choices[0]
        msg = choice.message
        content = msg.content or ''
        usage = resp.usage
        print(f"  応答したモデル: {resp.model}  provider: {_extra(resp, 'provider') or '（レスポンスに無し）'}")
        truncated = choice.finish_reason == 'length'
        print(f"  finish_reason={choice.finish_reason}"
              f"{'  ← NG（出力が max_tokens で途中で切れた）' if truncated else ''}"
              f"  native_finish_reason={_extra(choice, 'native_finish_reason')}")
        print(f"  usage: prompt_tokens={getattr(usage, 'prompt_tokens', None)} "
              f"completion_tokens={getattr(usage, 'completion_tokens', None)} "
              f"total_tokens={getattr(usage, 'total_tokens', None)}")
        reasoning = _extra(msg, 'reasoning')
        print(f"  message.reasoning: {'あり (' + str(len(reasoning)) + '文字。本文とは別のフィールドなので本番には影響しない)' if reasoning else 'なし'}")
        problems = _check_content(content)
        print(f"  本文の推論の混入: {'; '.join(p for p, _ in problems) if problems else 'なし'}")

        failed = truncated or any(fatal for _, fatal in problems)
        try:
            results, errors = llm_client._validate_quiz_items(llm_client._parse_json(content), QUIZ_COUNT)
        except ValueError as e:
            print(f"  NG: パース/形式エラー: {e}\n")
            continue
        raw_order = [q.get('article_index') if isinstance(q, dict) else None
                     for q in llm_client._parse_json(content).get('quizzes', [])]
        print(f"  出力の並び（article_index）: {raw_order}"
              f"{'' if raw_order == list(range(1, QUIZ_COUNT + 1)) else '  ← 記事の順番と違う'}")
        # article_index に対応したコード（PR #20）では、results は記事の順番に並べ替え済み（Q{i} は記事{i} の問題）
        for i, (quiz, article) in enumerate(zip(results, articles), start=1):
            print(f"  Q{i} [記事{i}: {article['title']}]")
            if quiz:
                print(f"      {quiz['question']}  正解={quiz['answer']!r}  選択肢={quiz['choices']}")
            else:
                print("      （問題なし: 検証失敗など。本番ではこの記事の問題だけ再生成する）")
        if errors:
            print(f"  検証失敗: {errors}（本番では失敗した問題だけ同じモデルで再生成する）")
        if failed:
            print("  NG: 本番では使えない（上記の理由）")
        elif errors:
            print(f"  △: {QUIZ_COUNT - len(errors)}/{QUIZ_COUNT} 問が検証を通過")
            usable.append(model)
        else:
            print(f"  OK: {QUIZ_COUNT} 問すべて検証を通過")
            usable.append(model)
        print()

    print(f"使えたモデル（{len(usable)}/{len(models)}）: {', '.join(usable) or 'なし'}")
    return 0 if usable else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
