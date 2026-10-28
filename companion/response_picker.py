"""Bounded response actions + interpersonal attitude, separate from prose.

Action labels draw on dialogue acts and ESConv; rules/scales are product
decisions, not a clinically validated psychological model.
"""
from .emotions import normalize_emotions

ACTION_DESCRIPTIONS = {
    'answer': '直接回答當前問題，根據可用事實，不知道就說明。',
    'acknowledge': '承接對方剛說的具體內容，不代表同意所有主張。',
    'ask': '只問一個與當前內容有關、尚未知道答案的問題。',
    'clarify': '先查近期上下文，仍缺關鍵資訊才澄清。',
    'reflect_feelings': '試著接住對方具體感受，以試探方式表達理解；不要斷言完全知道對方怎麼想，情緒不明時不下定論。',
    'affirm': '根據具體內容肯定或支持，不空泛保證結果。',
    'share_self': '分享有來源的角色近況，不能借用使用者經歷。',
    'express_feelings': '表達角色自己的感受，不把感受歸給使用者。',
    'suggest': '提出可選擇的建議，不虛構已完成行動。',
    'play': '承接玩笑，按界線與關係調整，不羞辱。',
    'repair': '具體處理誤解或不滿，有錯才道歉，不空泛承諾。',
    'disagree': '清楚表達不同觀點，針對事情而非貶低對方。',
    'set_boundary': '說明自己的界線，保持尊重，不以懲罰或威脅表達。',
}

# Each strategy defines compatible actions, not arbitrary combinations. Optional
# second actions have an explicit none option; no compulsory follow-up question.
ACTION_OPTIONS = {
    'support': (['reflect_feelings', 'affirm'], ['none', 'ask', 'affirm']),
    'playful': (['play'], ['none', 'ask']),
    'followup': (['acknowledge', 'express_feelings'], ['none', 'ask', 'disagree']),
    'share_self': (['share_self'], ['none', 'express_feelings']),
    'repair': (['repair'], ['none', 'clarify', 'set_boundary']),
    'direct_answer': (['answer'], ['none', 'clarify', 'suggest']),
    'clarify': (['clarify'], ['none']),
    'open_response': (['acknowledge', 'answer', 'express_feelings'], ['none', 'ask', 'disagree', 'set_boundary']),
}


def pick_response(plan, traits, relationship, state, user_affect=None):
    """Pure rules v1: choose a small output; never update the input state."""
    own = normalize_emotions(state.get('emotions'))
    user = normalize_emotions((user_affect or {}).get('emotions'))
    primary = ACTION_OPTIONS[plan['strategy']][0][0]
    secondary = 'none'
    if plan['strategy'] in {'support', 'followup'} and state['energy'] >= 30:
        if plan['policy']['curiosity'] >= 55 and max(user['anger'], user['fear']) < 60:
            secondary = 'ask'
    if plan['strategy'] == 'share_self' and max(own.values()) >= 30:
        secondary = 'express_feelings'
    motivation = plan.get('motivation', {})
    dominant_need = motivation.get('dominant_goal', {}).get('source_need')
    coping = motivation.get('coping', {}).get('strategy')
    if coping == 'conserve_capacity':
        secondary = 'none'
    elif coping == 'explore' and 'ask' in ACTION_OPTIONS[plan['strategy']][1] and state['energy'] >= 30:
        secondary = 'ask'
    # Agency describes this reply, not the character's stable assertiveness.
    warmth = plan['policy']['warmth'] * .6 + traits['agreeableness'] * .4
    warmth += own['joy'] * .12 - own['anger'] * .15 - own['disgust'] * .12
    agency = plan['policy']['directness'] * .6 + traits['extraversion'] * .2 + state['energy'] * .2
    agency += own['anger'] * .12 - own['fear'] * .2 - own['sadness'] * .1 + own['surprise'] * .05
    if dominant_need == 'relatedness':
        warmth += 8
    elif dominant_need == 'autonomy':
        agency += 8
    elif dominant_need == 'competence':
        agency += 5
    if coping == 'conserve_capacity':
        agency -= 10
    if max(user['sadness'], user['fear']) >= 50 and plan['strategy'] == 'support':
        warmth += 10
        agency -= 15
    if plan['variant'] == 'brief':
        secondary = 'none'
    direction = plan.get('intimacy', {}).get('direction')
    if direction in {'warm_tease', 'playful_probe', 'deepen_intimacy'}:
        warmth += 6
        agency += 4 if direction == 'warm_tease' else 8
    elif direction == 'cool_down':
        agency -= 8
    return {'primary_action': primary, 'secondary_action': secondary,
            'warmth': round(max(0, min(100, warmth))),
            'agency': round(max(0, min(100, agency)))}


def response_instructions(response):
    primary, secondary = response['primary_action'], response['secondary_action']
    steps = [ACTION_DESCRIPTIONS[primary]]
    if secondary != 'none':
        steps.append(ACTION_DESCRIPTIONS[secondary])
    return {'ordered_actions': steps,
            'attitude': 'warmth是本輪親近程度（低則有分寸，高則親切）；agency是本輪主導程度（低則留選擇，高則明確表態）。數值不可朗讀。'}
