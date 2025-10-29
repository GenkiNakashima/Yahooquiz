# firebase_service.py
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
import os
import json


class FirebaseService:
    def __init__(self):
        """Firebase初期化"""
        self.db = None
        self._initialize_firebase()

    def _initialize_firebase(self):
        """Firebase初期化処理"""
        try:
            # 既に初期化されている場合はスキップ
            if firebase_admin._apps:
                self.db = firestore.client()
                print("Firebase: 既存の接続を使用")
                return

            # 環境変数からサービスアカウントキーを取得
            service_account_info = os.environ.get("FIREBASE_SERVICE_ACCOUNT")

            if service_account_info:
                # JSON文字列として提供された場合
                try:
                    service_account_dict = json.loads(service_account_info)
                    cred = credentials.Certificate(service_account_dict)
                except json.JSONDecodeError:
                    # ファイルパスとして提供された場合
                    cred = credentials.Certificate(service_account_info)
            else:
                # デフォルトのサービスアカウントファイルを探す
                possible_paths = [
                    "firebase-service-account.json",
                    "service-account.json",
                    "firebase-adminsdk.json",
                ]

                cred = None
                for path in possible_paths:
                    if os.path.exists(path):
                        cred = credentials.Certificate(path)
                        break

                if not cred:
                    print(
                        "警告: Firebaseサービスアカウントが見つかりません。環境変数FIREBASE_SERVICE_ACCOUNTを設定するか、適切なJSONファイルを配置してください。"
                    )
                    return

            # Firebase初期化
            firebase_admin.initialize_app(cred)
            self.db = firestore.client()
            print("Firebase: 初期化成功")

        except Exception as e:
            print(f"Firebase初期化エラー: {e}")
            self.db = None

    def save_quiz_result(
        self, player_score, ai_score, total_rounds, ai_level, game_duration=None
    ):
        """クイズ結果をFirestoreに保存"""
        if not self.db:
            print("Firebase: データベース接続が利用できません")
            return None

        try:
            # 結果データの準備
            result_data = {
                "player_score": player_score,
                "ai_score": ai_score,
                "total_rounds": total_rounds,
                "ai_level": ai_level,
                "timestamp": datetime.now(),
                "game_duration": game_duration,
                "winner": (
                    "player"
                    if player_score > ai_score
                    else "ai" if ai_score > player_score else "draw"
                ),
            }

            # Firestoreに保存
            doc_ref = self.db.collection("quiz_results").add(result_data)
            doc_id = doc_ref[1].id

            print(f"Firebase: クイズ結果を保存しました (ID: {doc_id})")
            return doc_id

        except Exception as e:
            print(f"Firebase: 結果保存エラー - {e}")
            return None

    def get_recent_results(self, limit=10):
        """最近の結果を取得"""
        if not self.db:
            print("Firebase: データベース接続が利用できません")
            return []

        try:
            results = []
            docs = (
                self.db.collection("quiz_results")
                .order_by("timestamp", direction=firestore.Query.DESCENDING)
                .limit(limit)
                .stream()
            )

            for doc in docs:
                result = doc.to_dict()
                result["id"] = doc.id
                # timestampを文字列に変換（JSONシリアライズ用）
                if "timestamp" in result:
                    result["timestamp"] = result["timestamp"].isoformat()
                results.append(result)

            return results

        except Exception as e:
            print(f"Firebase: 結果取得エラー - {e}")
            return []

    def get_statistics(self):
        """統計情報を取得"""
        if not self.db:
            print("Firebase: データベース接続が利用できません")
            return {
                "total_games": 0,
                "total_questions": 0,
                "player_wins": 0,
                "ai_wins": 0,
                "draws": 0,
                "average_player_score": 0,
                "average_ai_score": 0,
                "ai_level_distribution": {"strong": 0, "normal": 0, "weak": 0},
                "win_rate": 0,
            }

        try:
            docs = self.db.collection("quiz_results").stream()

            total_games = 0
            player_wins = 0
            ai_wins = 0
            draws = 0
            total_player_score = 0
            total_ai_score = 0
            ai_level_stats = {"strong": 0, "normal": 0, "weak": 0}

            for doc in docs:
                result = doc.to_dict()
                total_games += 1

                if result.get("winner") == "player":
                    player_wins += 1
                elif result.get("winner") == "ai":
                    ai_wins += 1
                else:
                    draws += 1

                total_player_score += result.get("player_score", 0)
                total_ai_score += result.get("ai_score", 0)

                ai_level = result.get("ai_level", "normal")
                if ai_level in ai_level_stats:
                    ai_level_stats[ai_level] += 1

            stats = {
                "total_games": total_games,
                "player_wins": player_wins,
                "ai_wins": ai_wins,
                "draws": draws,
                "average_player_score": (
                    round(total_player_score / total_games, 2) if total_games > 0 else 0
                ),
                "average_ai_score": (
                    round(total_ai_score / total_games, 2) if total_games > 0 else 0
                ),
                "ai_level_distribution": ai_level_stats,
                "win_rate": (
                    round(player_wins / total_games * 100, 1) if total_games > 0 else 0
                ),
            }

            return stats

        except Exception as e:
            print(f"Firebase: 統計取得エラー - {e}")
            return {
                "total_games": 0,
                "total_questions": 0,
                "player_wins": 0,
                "ai_wins": 0,
                "draws": 0,
                "average_player_score": 0,
                "average_ai_score": 0,
                "ai_level_distribution": {"strong": 0, "normal": 0, "weak": 0},
                "win_rate": 0,
            }

    def save_individual_question_result(
        self,
        question_data,
        player_answer,
        correct_answer,
        player_time,
        ai_time,
        result_type,
        ai_level,
    ):
        """個別の問題結果を保存"""
        if not self.db:
            print("Firebase: データベース接続が利用できません")
            return None

        try:
            question_result = {
                "question": question_data.get("question", ""),
                "article_title": question_data.get("article_title", ""),
                "article_url": question_data.get("article_url", ""),
                "correct_answer": correct_answer,
                "player_answer": player_answer,
                "player_time": player_time,
                "ai_time": ai_time,
                "result_type": result_type,  # 'correct', 'wrong', 'ai_correct', 'ai_wrong'
                "ai_level": ai_level,
                "timestamp": datetime.now(),
            }

            doc_ref = self.db.collection("question_results").add(question_result)
            doc_id = doc_ref[1].id

            print(f"Firebase: 問題結果を保存しました (ID: {doc_id})")
            return doc_id

        except Exception as e:
            print(f"Firebase: 問題結果保存エラー - {e}")
            return None
