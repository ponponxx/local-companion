"""Versioned activity opportunities, not claims of completed satisfaction.

Only normalized exact aliases match. Drawing and modeling are different roles;
unknown activities never inherit competence from a broad task/social label.
"""
import re
from copy import deepcopy

ACTIVITY_RULES = {
    'dance': {
        'label': '跳舞',
        'aliases': ['跳舞', '跳舞邀請', '一起跳舞', '陪我跳舞', '跳一支舞', '跳支舞', 'dance', 'dancing'],
        'opportunity_needs': ['competence'],
        'accept_helps': {'relatedness': 2, 'competence': 2, 'homeostasis': 0},
        'reason': '參與技能活動，可運用／掌握舞步；同時提供相處機會。未評估個別舞技或是否喜歡跳舞。',
    },
    'draw_sketch': {
        'label': '畫素描',
        'aliases': ['畫素描', '畫一張素描', '素描創作', '幫我畫素描', '繪製素描', 'draw a sketch', 'sketch drawing'],
        'opportunity_needs': ['competence'],
        'accept_helps': {'relatedness': 2, 'competence': 3, 'homeostasis': 0},
        'reason': '角色是繪畫者，有運用技巧與完成作品的機會。',
    },
    'sketch_model': {
        'label': '當素描模特兒',
        'aliases': ['當素描模特兒', '當素描模特', '素描模特兒', '素描模特', '擔任素描模特兒', '當我的素描模特兒', 'sketch model', 'pose for a sketch'],
        'opportunity_needs': [],
        'accept_helps': {'relatedness': 2, 'competence': 0, 'homeostasis': 0},
        'reason': '角色是被描繪者，不把被欣賞直接當成繪畫能力或勝任收益。',
    },
    'receive_massage': {
        'label': '接受按摩',
        'aliases': ['接受按摩', '被按摩', '讓對方按摩', 'receive massage', 'receive a massage'],
        'opportunity_needs': [],
        'accept_helps': {'relatedness': 2, 'competence': 0, 'homeostasis': 3},
        'reason': '角色是接受照顧者，可能緩解已有的疲累／壓力；邀請不憑空提高恢復需求。',
    },
    'give_massage': {
        'label': '提供按摩',
        'aliases': ['提供按摩', '幫對方按摩', '替使用者按摩', '幫我按摩', 'give massage', 'give a massage'],
        'opportunity_needs': ['competence'],
        'accept_helps': {'relatedness': 2, 'competence': 2, 'homeostasis': 0},
        'reason': '角色是提供照顧者，可運用技巧；不能把替別人按摩算成自己休息。',
    },
}


def normalize(value):
    return re.sub(r'[\s，。！？、,.!?]+', '', value).casefold()


def describe_activity(event, rules=None):
    value = event.get('value', '')
    for key, rule in (ACTIVITY_RULES if rules is None else rules).items():
        if normalize(value) in {normalize(v) for v in [key, *rule['aliases']]}:
            return {'event_id': event['id'], 'input': value, 'matched': True,
                    'key': key, **deepcopy(rule)}
    # Preserve the existing care-purpose rule without pretending to know a role
    # from a generic "按摩". No new competence is inferred from purpose alone.
    care = event.get('field') == 'care'
    role_note = '若只寫按摩，尚未辨識接受／提供角色。' if normalize(value) in {'按摩', 'massage'} else ''
    return {'event_id': event['id'], 'input': value, 'matched': False, 'key': 'unknown', 'fallback': care,
            'label': value or '未提供活動', 'opportunity_needs': [],
            'accept_helps': {'relatedness': 2, 'competence': 0,
                             'homeostasis': 3 if care else 0},
            'reason': '活動未收錄，不新增勝任機會。' +
                      ('照顧用途仍沿用原本恢復收益規則。' if care else '不從任務／相處分類猜測能力或恢復收益。') + role_note}
