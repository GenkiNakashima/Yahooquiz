# answer_checker.py
"""
記述式の問題の正誤判定（サーバー側でのみ使う）。

ユーザーの入力と、正解・aliases（別表記）の両方に同じ正規化をかけて完全一致で比較する。
あいまい一致（編集距離など）は使わない。

正規化（全種類共通）:
  Unicode NFKC → 英字を小文字に → 旧字体・異体字を変換 → ハイフン類を統一（かなの直後は長音「ー」）
  → カタカナをひらがなに → 空白・句読点・括弧・中黒などの記号を削除
answer_type ごとの追加:
  number: 数値として比較（桁区切り・漢数字・千/万/億/兆・小数）。単位と「約」「およそ」「程度」は無視
  name:   会社の形態（株式会社・(株) など）、人名の敬称・肩書を無視。「ホールディングス」と「HD」は同じ
"""
import re
import unicodedata
from decimal import Decimal, InvalidOperation

ANSWER_TYPES = ('number', 'name', 'term', 'other')
# 記述式にできる answer_type（正規化で表記ゆれを吸収しやすいもの）
TEXT_ANSWER_TYPES = ('number', 'name')
# 記述式の回答の最大文字数（超えた入力は正規化・保存の前に不正解にする。入力欄の maxlength も同じ値）
MAX_INPUT_CHARS = 50

ANSWER_HINTS = {
    'number': '数字のみでOK・単位は不要です（例: 3万、30000 どちらも可）',
    'name': '「株式会社」や、人名の敬称・肩書（さん・社長など）は不要です',
}

# よく出る旧字体・異体字だけを変換する（NFKC で変換されないもの）
_VARIANTS = str.maketrans({
    '髙': '高', '﨑': '崎', '嵜': '崎', '德': '徳', '濵': '浜', '濱': '浜', '邊': '辺', '邉': '辺',
    '澤': '沢', '櫻': '桜', '國': '国', '齋': '斎', '齊': '斉', '廣': '広', '眞': '真', '瀨': '瀬',
    '槇': '槙', '藪': '薮', '龍': '竜', '團': '団', '實': '実', '惠': '恵', '圓': '円', '淺': '浅',
    '曾': '曽', '冨': '富', '條': '条', '傳': '伝', '萬': '万', '彌': '弥', '壽': '寿', '榮': '栄',
})

# NFKC の後に残るハイフン類（全角の「－」は NFKC で "-" になる。長音「ー」と半角の「ｰ」は含めない）
_HYPHENS = '‐‑‒–—―−⁃﹘﹣'
_HYPHEN_RE = re.compile(f'[{_HYPHENS}]')
# かな（または長音）の直後のハイフンは長音の打ち間違いとみなす（サ−ビス → サービス）
_KANA_HYPHEN_RE = re.compile(r'(?<=[ぁ-ゖァ-ヺー])-')

# 削除する記号（句読点・括弧・中黒・引用符など。NFKC 後の文字）
_SYMBOLS_RE = re.compile(
    r'[\s、。,.・･「」『』()\[\]{}【】〔〕〈〉《》〘〙〚〛"\'“”‘’`!?:;/\\|*#&=+~〜…‥]'
)


def _kata_to_hira(text):
    return ''.join(chr(ord(c) - 0x60) if 'ァ' <= c <= 'ヶ' else c for c in text)


def _base(text):
    """NFKC・小文字・異体字・ハイフン類・カタカナ→ひらがな（記号はまだ消さない）"""
    text = unicodedata.normalize('NFKC', text).lower().translate(_VARIANTS)
    text = _HYPHEN_RE.sub('-', text)
    text = _KANA_HYPHEN_RE.sub('ー', text)
    return _kata_to_hira(text)


def normalize_text(text):
    """全種類共通の正規化"""
    return _SYMBOLS_RE.sub('', _base(text))


# ---------------------------------------------------------------------------
# name
# ---------------------------------------------------------------------------

# 会社の形態（NFKC 後。「㈱」は NFKC で「(株)」になる）
_COMPANY_FORMS_RE = re.compile(r'株式会社|有限会社|合同会社|合資会社|合名会社|\(株\)|\(有\)|\(同\)')
_HOLDINGS_RE = re.compile(r'ほーるでぃんぐす|holdings')
# 人名の敬称・肩書（末尾のみ取り除く。長いものから順に試す）
_TITLES = sorted({
    'さん', 'さま', '様', '氏', '殿', '君', 'くん', 'ちゃん',
    '首相', '総理大臣', '総理', '大統領', '副大統領', '国家主席', '主席', '首席', '国王', '女王', '王子',
    '社長', '副社長', '会長', '代表取締役', '取締役', '頭取', '代表', 'ceo', '最高経営責任者',
    '知事', '市長', '町長', '村長', '区長', '大臣', '長官', '議員', '議長', '委員長', '党首', '総裁', '幹事長',
    '監督', '選手', '投手', '捕手', '内野手', '外野手', '容疑者', '被告', '教授', '准教授', '博士', '医師', '弁護士',
}, key=len, reverse=True)


def normalize_name(text):
    text = _COMPANY_FORMS_RE.sub('', _base(text))
    text = normalize_text(text)
    text = _HOLDINGS_RE.sub('hd', text)
    stripped = True
    while stripped:
        stripped = False
        for title in _TITLES:
            if text.endswith(title) and len(text) > len(title):
                text = text[:-len(title)]
                stripped = True
                break
    return text


# ---------------------------------------------------------------------------
# number
# ---------------------------------------------------------------------------

_APPROX_PREFIXES = ('およそ', 'おおよそ', '約')
_APPROX_SUFFIXES = ('程度',)
# 単位（無視する）。正規化と同じ形にしてから使う。長いものから順に試す
_UNITS = sorted({_kata_to_hira(unicodedata.normalize('NFKC', u).lower()) for u in (
    'パーセント', '%', 'ポイント', 'pt', '円', 'ドル', 'ユーロ', 'ウォン', '元', 'ポンド',
    'トン', 't', 'kg', 'g', 'km', 'm', 'cm', 'mm', 'ha', 'l',
    '人', '名', '件', '台', '社', '店', '校', '歳', '才', '年度', '年', 'か月', 'カ月', 'ヶ月', 'ケ月', '月', '日',
    '時間', '分', '秒', '回', '倍', '個', '本', '枚', '頭', '匹', '戸', '棟', '票', '点', '位', '度', '℃',
)}, key=len, reverse=True)
_KANJI_DIGITS = str.maketrans('〇零一二三四五六七八九', '00123456789')
_SMALL_UNITS = {'十': 10, '百': 100, '千': 1000}
_BIG_UNITS = (('兆', 10 ** 12), ('億', 10 ** 8), ('万', 10 ** 4))
_DECIMAL = r'\d+(?:\.\d+)?'
_SMALL_TOKEN_RE = re.compile(rf'({_DECIMAL})?([十百千])')
_DECIMAL_RE = re.compile(_DECIMAL)


def _strip_affixes(text):
    """「約」「およそ」「程度」と単位を取り除く（「約3万人程度」のように重なっていてもよい）"""
    changed = True
    while changed:
        changed = False
        if text != text.strip('~'):
            text, changed = text.strip('~'), True
        for prefix in _APPROX_PREFIXES:
            if text.startswith(prefix):
                text, changed = text[len(prefix):], True
        for suffix in _APPROX_SUFFIXES + tuple(_UNITS):
            if text.endswith(suffix) and len(text) > len(suffix):
                text, changed = text[:-len(suffix)], True
                break
    return text


def _parse_small(text):
    """1万未満の部分（「7680」「1.2」「3千5百」「十2」など）を数値にする。解釈できなければ None"""
    if not text:
        return None
    total, pos = Decimal(0), 0
    while pos < len(text):
        m = _SMALL_TOKEN_RE.match(text, pos)
        if m:
            total += (Decimal(m.group(1)) if m.group(1) else 1) * _SMALL_UNITS[m.group(2)]
            pos = m.end()
            continue
        m = _DECIMAL_RE.match(text, pos)
        if m and m.end() == len(text):
            total += Decimal(m.group(0))
            pos = m.end()
            continue
        return None
    return total


def parse_number(text):
    """
    数値の答えを Decimal にする（「約7,680億円」→ 768000000000、「1.2万」→ 12000、「三千五百」→ 3500）。
    数値として解釈できなければ None。
    """
    text = _base(text)
    text = re.sub(r'[\s,]', '', text)
    text = _strip_affixes(text.replace('〜', '~'))
    sign = 1
    for minus in ('まいなす', '-'):
        if text.startswith(minus):
            sign, text = -1, text[len(minus):]
            break
    text = text.translate(_KANJI_DIGITS)
    if not text:
        return None
    total, rest = Decimal(0), text
    try:
        for unit, multiplier in _BIG_UNITS:
            if unit in rest:
                head, rest = rest.split(unit, 1)
                value = _parse_small(head) if head else Decimal(1)
                if value is None:
                    return None
                total += value * multiplier
        if rest:
            value = _parse_small(rest)
            if value is None:
                return None
            total += value
    except InvalidOperation:
        return None
    return sign * total


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------

def answers_match(a, b, answer_type='other'):
    """2つの表記が、answer_type の正規化で同じ答えになるか"""
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    if answer_type == 'number':
        na, nb = parse_number(a), parse_number(b)
        if na is not None and nb is not None:
            return na == nb
        # どちらかが数値として読めない（「3割」など）場合は文字列として比べる
    normalize = normalize_name if answer_type == 'name' else normalize_text
    na, nb = normalize(a), normalize(b)
    return bool(na) and na == nb


def is_correct(user_input, answer, aliases=(), answer_type='other'):
    """ユーザーの入力が、正解または aliases のどれかと一致するか"""
    if not isinstance(user_input, str) or not user_input.strip() or len(user_input) > MAX_INPUT_CHARS:
        return False
    return any(answers_match(user_input, target, answer_type) for target in [answer, *(aliases or [])])
