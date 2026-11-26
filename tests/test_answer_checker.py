"""answer_checker（記述式の正誤判定）の表形式テスト: (ユーザーの入力, 正解, 期待結果)"""
import pytest

from answer_checker import MAX_INPUT_CHARS, is_correct, parse_number

NUMBER_CASES = [
    # 桁区切り・全角・単位「億」
    ('7680億', '7680億円', True),
    ('768000000000', '7680億円', True),
    ('7,680億円', '7680億円', True),
    ('７６８０億', '7680億円', True),
    ('７，６８０億円', '7680億円', True),
    ('7000億', '7680億円', False),
    # 小数と「万」
    ('12000', '1.2万', True),
    ('1.2万', '12000人', True),
    ('1万2000', '1.2万', True),
    ('1.3万', '1.2万', False),
    ('1兆2000億', '1.2兆円', True),
    # 漢数字
    ('三', '3', True),
    ('十二', '12', True),
    ('千', '1000', True),
    ('万', '10000', True),
    ('三千五百万', '3500万', True),
    ('百二十', '120', True),
    ('二〇二五', '2025年', True),
    ('3千', '3000', True),
    # 単位はあってもなくても正解
    ('5', '5トン', True),
    ('5t', '5トン', True),
    ('5トン', '5', True),
    ('100人', '100', True),
    ('3件', '3', True),
    ('20歳', '20', True),
    ('2030年', '2030', True),
    ('2025', '2025年度', True),
    ('1,000円', '1000円', True),
    # % の問題で数値だけ答えても正解
    ('3.5', '3.5%', True),
    ('3.5％', '3.5%', True),
    ('3.5パーセント', '3.5%', True),
    ('3.5', '3.5パーセント', True),
    ('0.1', '0.1%', True),
    ('1', '0.1%', False),
    # 「約」「およそ」「程度」は無視
    ('約7680億円', '7680億円', True),
    ('およそ500', '500', True),
    ('500程度', '500', True),
    ('500〜程度', '500', True),
    ('約500人程度', '500', True),
    ('500', '約500人', True),
    # 位（万・億）は省略できない
    ('3', '3万人', False),
    ('7680', '7680億円', False),
    # 負の数
    ('−0.5%', '-0.5%', True),
    ('マイナス0.5', '-0.5%', True),
    ('0.5', '-0.5%', False),
    # 数値として読めない答えは文字列として比べる
    ('3割', '3割', True),
    ('4割', '3割', False),
    # 空の入力
    ('', '3', False),
    ('   ', '3', False),
]

NAME_CASES = [
    # 会社の形態
    ('ソフトバンクグループ', 'ソフトバンクグループ株式会社', True),
    ('トヨタ自動車', '（株）トヨタ自動車', True),
    ('㈱トヨタ自動車', 'トヨタ自動車', True),
    ('トヨタ自動車(株)', 'トヨタ自動車', True),
    ('有限会社山田商店', '山田商店', True),
    ('合同会社山田', '山田', True),
    # ホールディングス = HD
    ('セブン&アイHD', 'セブン&アイ・ホールディングス', True),
    ('セブン＆アイ・ホールディングス', 'セブン&アイHD', True),
    # 全角半角・中黒・空白
    ('三菱UFJフィナンシャル・グループ', '三菱ＵＦＪフィナンシャルグループ', True),
    ('石破 茂', '石破茂', True),
    ('石破　茂', '石破茂', True),
    # 敬称・肩書
    ('石破茂首相', '石破茂', True),
    ('石破さん', '石破', True),
    ('山田氏', '山田', True),
    ('孫正義社長', '孫正義', True),
    ('柳井正会長', '柳井正', True),
    ('トランプ大統領', 'トランプ', True),
    # 旧字体・異体字
    ('高橋', '髙橋', True),
    ('山崎', '山﨑', True),
    # カタカナとひらがな
    ('とよた', 'トヨタ', True),
    # ハイフン類（長音以外）は統一。かなの直後のハイフン・半角の「ｰ」は長音
    ('JR-東日本', 'JR‐東日本', True),
    ('JR−東日本', 'JR―東日本', True),
    ('サ−ビス', 'サービス', True),
    ('サｰビス', 'サービス', True),
    # 違う答え・あいまい一致はしない
    ('ソフトバンク', 'ソフトバンクグループ', False),
    ('ソフトバンクグルプ', 'ソフトバンクグループ', False),
    ('石破', '石破茂', False),
    ('首相', '石破茂', False),
]

COMMON_CASES = [
    # 全種類共通の正規化（term / other）
    ('もやし', 'モヤシ', True),
    ('ﾓﾔｼ', 'もやし', True),
    ('ＡＢＣ', 'abc', True),
    ('「政策金利」', '政策金利', True),
    ('政策、金利。', '政策金利', True),
    ('もやし', 'キャベツ', False),
]


@pytest.mark.parametrize('user_input, answer, expected', NUMBER_CASES)
def test_number(user_input, answer, expected):
    assert is_correct(user_input, answer, [], 'number') is expected


@pytest.mark.parametrize('user_input, answer, expected', NAME_CASES)
def test_name(user_input, answer, expected):
    assert is_correct(user_input, answer, [], 'name') is expected


@pytest.mark.parametrize('answer_type', ['term', 'other'])
@pytest.mark.parametrize('user_input, answer, expected', COMMON_CASES)
def test_common_normalization(user_input, answer, expected, answer_type):
    assert is_correct(user_input, answer, [], answer_type) is expected


@pytest.mark.parametrize('user_input, answer, aliases, answer_type, expected', [
    ('公取委', '公正取引委員会', ['公取委'], 'name', True),
    ('公取委', '公正取引委員会', [], 'name', False),
    ('SoftBank Group', 'ソフトバンクグループ', ['SoftBank Group'], 'name', True),
    ('softbank group', 'ソフトバンクグループ', ['SoftBank Group'], 'name', True),
    ('ＳｏｆｔＢａｎｋ　Ｇｒｏｕｐ', 'ソフトバンクグループ', ['SoftBank Group'], 'name', True),
    ('ソフトバンクグループ株式会社', 'ソフトバンクグループ', ['SoftBank Group'], 'name', True),
    ('石破', '石破茂', ['石破'], 'name', True),
    ('石破首相', '石破茂', ['石破'], 'name', True),
    ('3割', '30%', ['3割'], 'number', True),
])
def test_aliases(user_input, answer, aliases, answer_type, expected):
    assert is_correct(user_input, answer, aliases, answer_type) is expected


def test_input_length_limit():
    assert MAX_INPUT_CHARS == 50
    assert is_correct('あ' * 50, 'あ' * 50) is True
    assert is_correct('あ' * 51, 'あ' * 51) is False
    # 正規化（空白の削除）の前の長さで判定する
    assert is_correct('7680億円' + ' ' * 50, '7680億円', [], 'number') is False


@pytest.mark.parametrize('text, expected', [
    ('7680億円', 768000000000),
    ('1.2万', 12000),
    ('約3万人程度', 30000),
    ('三千五百', 3500),
    ('3割', None),
    ('', None),
    ('億', 10 ** 8),
    ('1.2.3', None),
])
def test_parse_number(text, expected):
    assert parse_number(text) == expected
