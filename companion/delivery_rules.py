"""Deterministic wording lookup. Never selects an action or changes persistent state.

The existing DecisionCore formulas remain the sole source of delivery scores.
Four bands use the same 0/25/50/75 thresholds as decision experiments.
Rows are engineering rules, not validated psychological laws.
"""
from copy import deepcopy
import math

VERSION = 'delivery-table-v1'
# One final instruction per dimension: low-to-high, not cumulative instructions.
RULES = {
    'warmth': (
        '禮貌、保留距離，不用親暱稱呼。',
        '友善而克制，不刻意親暱。',
        '自然關心，語氣溫和。',
        '明顯溫暖親近，不討好或擅加承諾。'),
    'agency': (
        '措辭柔和，清楚表達定案。',
        '平實表達立場，不命令對方。',
        '清楚直接，有自己的立場。',
        '語氣篤定、界線清楚，不支配或貶低對方。'),
    'expression': (
        '用簡單短句，情緒表現收斂。',
        '自然口語，少用修飾。',
        '可表達一點感受，避免大段描寫。',
        '情感表達鮮明，但不誇張演出或捏造動作。'),
    'teasing': (
        '直說，不調侃、不用嘲弄口癖。',
        '最多輕微幽默，不諷刺對方。',
        '可用一句輕巧吐槽或反問，仍完整回應。',
        '可用俏皮反問或克制調侃事情，不羞辱、威脅或否定對方。'),
    'personal_disclosure': (
        '只談本輪必要的已知自身資訊。',
        '需要時簡短提及已知近況。',
        '可自然說明有來源的自身感受或近況。',
        '可坦率表達有來源的自身感受，仍不新增經歷或轉移話題。'),
    'surprise_expression': (
        '不用驚呼或意外式開場。',
        '可輕描淡寫表達意外。',
        '可用一句短反問表達驚訝。',
        '可明顯表達驚訝，不連續驚叫或戲劇化指責。'),
}
SOURCES = {
    'warmth': '自在＋好感＋體貼配合，受喜悅／憤怒／厭惡及關係緊張調整',
    'agency': '有主見＋衝動直接，受憤怒／恐懼／悲傷調整',
    'expression': '情感表達×體力',
    'teasing': '愛吐槽×信任與自在的較低值×壓力折減',
    'personal_disclosure': '親密與信任的較低值',
    'surprise_expression': '當下驚訝',
}


def band(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('口吻輸入必須是有限數值')
    return min(3, max(0, int(value // 25)))


def compile_delivery(state, decision, brief):
    """Return Writer wording plus backend-only evidence; no generated judgement."""
    raw = decision['delivery']
    levels = {key: band(raw.get(key, 0)) for key in RULES}
    guards = []
    # Respect explicit pause/repair; stress/distress already zero teasing in Core.
    if brief['chat_engagement'] in {'respect_pause', 'rest'}:
        guards.append('pause_or_rest')
    if decision['primary'] in {'repair', 'support'}:
        guards.append('repair_or_support')
    if guards:
        levels['teasing'] = 0
        levels['surprise_expression'] = 0
    if state.get('stress', 0) >= 75:
        guards.append('high_stress')
        levels['teasing'] = 0
    delivery = {key: RULES[key][levels[key]] for key in RULES}
    delivery['length'] = brief['length']
    delivery['verbal_habits'] = (
        '口癖可省略；不固定加輕哼、反問或口頭禪，不沿用上一句的開頭。'
        if levels['teasing'] else '不使用嘲弄口癖或習慣性反問；直接回應。')
    # Existing adult controls remain in Core; only pass a non-explicit wording cue
    # when the selected action actually has an intimacy intention.
    if decision.get('intimacy_intention', 'none') != 'none':
        delivery['adult_expression'] = (
            '含蓄表達定案中的親近感。', '克制而自然地表達定案中的親近感。',
            '較明確表達定案中的親近感。', '坦率表達定案中的親近感，不自行升級互動。'
        )[band(raw.get('adult_expression', 0))]
    audit = {'version': VERSION, 'inputs': deepcopy(raw), 'sources': SOURCES.copy(),
             'levels': {key: value + 1 for key, value in levels.items()},
             'guards': guards, 'output': deepcopy(delivery)}
    return delivery, audit


def character_identity(story):
    """Only identity/background. Never transfer old free-form behavior instructions."""
    return {key: deepcopy(story[key]) for key in ('name', 'background') if key in story}
