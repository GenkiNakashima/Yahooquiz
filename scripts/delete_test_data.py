"""ローカル開発で作られたテストデータを削除する（icons は削除しない）。

削除するコレクション（サブコレクションも含めて削除する）:
    users, matches, match_players, points_transactions, auth_refresh_tokens, user_icons, quiz_sets
icons はこのスクリプトでは絶対に削除しない（対象に指定しても拒否する）。

使い方（リポジトリのルートで。既定は削除しない確認のみ）:
    .venv/bin/python scripts/delete_test_data.py --project YOUR_PROJECT_ID           # 件数の確認のみ
    .venv/bin/python scripts/delete_test_data.py --project YOUR_PROJECT_ID --apply   # 実際に削除

本番（エミュレーター以外）で --apply を付けた場合は、確認のためプロジェクト ID の入力を求める。
件数だけを表示し、中身は表示しない。削除は元に戻せない。
実行後に scripts/migrate_user_icons.py --apply を実行し、限定アイコンの purchased_count を 0 にそろえる。
"""
import argparse
import os
import sys

from google.cloud import firestore

TARGET_COLLECTIONS = (
    'users',
    'matches',
    'match_players',
    'points_transactions',
    'auth_refresh_tokens',
    'user_icons',
    'quiz_sets',
)
PROTECTED_COLLECTIONS = frozenset({'icons'})
assert not PROTECTED_COLLECTIONS & set(TARGET_COLLECTIONS)


def _count(ref):
    return int(ref.count().get()[0][0].value)


def delete_test_data(db, apply=False, collections=TARGET_COLLECTIONS):
    """
    collections のドキュメントを削除する（apply=False なら数えるだけ）。

    Returns:
        dict: コレクション名 → 削除した（確認のみなら削除される）ドキュメント数。サブコレクションの数は含まない
    """
    protected = PROTECTED_COLLECTIONS & set(collections)
    if protected:
        raise ValueError(f"削除できないコレクションが指定されています: {', '.join(sorted(protected))}")

    stats = {}
    for name in collections:
        ref = db.collection(name)
        stats[name] = _count(ref)
        if apply:
            # サブコレクションも含めて削除する（ドキュメントを消してもサブコレクションは残るため。
            # 親ドキュメントの無いサブコレクションは件数に数えられないので、0件でも実行する）
            db.recursive_delete(ref)
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--project', required=True, help='対象の GCP / Firebase のプロジェクト ID')
    parser.add_argument('--apply', action='store_true', help='実際に削除する（指定しなければ件数の確認のみ）')
    args = parser.parse_args()

    db = firestore.Client(project=args.project)
    on_emulator = bool(db._emulator_host)
    target = f"エミュレーター（{db._emulator_host}）" if on_emulator else '本番の Firestore'
    print(f"対象: {target} / プロジェクト: {args.project} / {'削除する' if args.apply else '確認のみ（削除しない）'}")

    before = delete_test_data(db, apply=False)
    for name, count in before.items():
        print(f"  {name}: {count}")
    print(f"  icons（削除しない）: {_count(db.collection('icons'))}")

    if not args.apply:
        print("削除するには --apply を付けて再実行してください")
        return 0

    if not on_emulator:
        answer = input(f"本番のデータを削除します。元に戻せません。続けるにはプロジェクト ID（{args.project}）を入力してください: ")
        if answer.strip() != args.project:
            print("プロジェクト ID が一致しないため中止しました")
            return 1

    delete_test_data(db, apply=True)
    after = delete_test_data(db, apply=False)
    print("削除後:")
    for name, count in after.items():
        print(f"  {name}: {count}")
    print(f"  icons（削除しない）: {_count(db.collection('icons'))}")
    return 0 if not any(after.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
