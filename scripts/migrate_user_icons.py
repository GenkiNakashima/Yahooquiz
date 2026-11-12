"""user_icons のドキュメント ID と、限定アイコンの purchased_count を新しい形式に移行する（何度実行しても同じ結果になる）。

アイコン購入のトランザクションからクエリをなくしたため、既存データを次の形式にそろえる。
  1. user_icons/{ランダムな UUID} → user_icons/{user_id}_{icon_id}（中身はそのまま）
  2. 限定アイコンの icons/{icon_id}.purchased_count を、実際の user_icons の件数に合わせる

使い方（リポジトリのルートで。既定は書き込みをしない確認のみ）:
    .venv/bin/python scripts/migrate_user_icons.py --project YOUR_PROJECT_ID           # 確認のみ
    .venv/bin/python scripts/migrate_user_icons.py --project YOUR_PROJECT_ID --apply   # 実際に書き込む

新しいコードをデプロイする前に実行すること（アプリが動いている間に実行しない）。
件数だけを表示し、ユーザー ID などの中身は表示しない。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google.cloud import firestore  # noqa: E402

from services.icon_service import user_icon_doc_id  # noqa: E402


def migrate(db, apply=False):
    """移行を行い（apply=False なら数えるだけ）、件数の dict を返す"""
    stats = {
        'user_icons_total': 0,
        'user_icons_already_migrated': 0,
        'user_icons_moved': 0,
        'user_icons_duplicates_removed': 0,
        'user_icons_skipped_invalid': 0,
        'limited_icons': 0,
        'purchased_count_updated': 0,
    }

    for doc in db.collection('user_icons').stream():
        stats['user_icons_total'] += 1
        data = doc.to_dict()
        user_id, icon_id = data.get('user_id'), data.get('icon_id')
        if not user_id or not icon_id:
            stats['user_icons_skipped_invalid'] += 1
            continue
        target_ref = db.collection('user_icons').document(user_icon_doc_id(user_id, icon_id))
        if doc.id == target_ref.id:
            stats['user_icons_already_migrated'] += 1
            continue

        if target_ref.get().exists:
            # 同じ組み合わせが既に新しい ID で存在する（重複購入のデータ、または途中まで移行済み）
            stats['user_icons_duplicates_removed'] += 1
            if apply:
                doc.reference.delete()
            continue

        stats['user_icons_moved'] += 1
        if apply:
            batch = db.batch()
            batch.create(target_ref, data)
            batch.delete(doc.reference)
            batch.commit()

    for icon_doc in db.collection('icons').stream():
        icon = icon_doc.to_dict()
        if not (icon.get('is_limited') and icon.get('total_supply')):
            continue
        stats['limited_icons'] += 1
        # 移行後の ID で数える（ユーザーとアイコンの組で1件）。確認のみの場合は移行前のデータを組で重複なく数える
        owners = {
            d.to_dict().get('user_id')
            for d in db.collection('user_icons').where('icon_id', '==', icon_doc.id).stream()
        }
        owners.discard(None)
        if icon.get('purchased_count') != len(owners):
            stats['purchased_count_updated'] += 1
            if apply:
                icon_doc.reference.update({'purchased_count': len(owners)})

    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--project', required=True, help='移行する GCP / Firebase のプロジェクト ID')
    parser.add_argument('--apply', action='store_true', help='実際に書き込む（指定しなければ件数の確認のみ）')
    args = parser.parse_args()

    db = firestore.Client(project=args.project)
    target = f"エミュレーター（{db._emulator_host}）" if db._emulator_host else '本番の Firestore'
    print(f"対象: {target} / プロジェクト: {args.project} / {'書き込みあり' if args.apply else '確認のみ（書き込みなし）'}")

    stats = migrate(db, apply=args.apply)
    for key, value in stats.items():
        print(f"  {key}: {value}")
    if not args.apply:
        print("書き込むには --apply を付けて再実行してください")


if __name__ == '__main__':
    main()
