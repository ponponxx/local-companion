"""Six independent emotion intensities; engineering adaptation of Ekman (1992).

Character state is persistent. User affect is a conservative, turn-local
observation, never a character emotion or an established profile fact.
"""
import math
import re

EMOTION_LABELS = {
    'joy': '快樂', 'sadness': '悲傷', 'anger': '憤怒',
    'fear': '恐懼', 'disgust': '厭惡', 'surprise': '驚訝',
}


def normalize_emotions(value):
    value = value if isinstance(value, dict) else {}
    return {key: round(max(0, min(100, number)), 2)
            if isinstance((number := value.get(key, 0)), (int, float))
            and not isinstance(number, bool) and math.isfinite(number) else 0.0
            for key in EMOTION_LABELS}


def emotion_mood(emotions):
    active = sorted(normalize_emotions(emotions).items(), key=lambda pair: pair[1], reverse=True)
    labels = [EMOTION_LABELS[key] for key, strength in active[:2] if strength >= 15]
    return '、'.join(labels) if labels else '平靜'


# Deliberately recognize explicit first-person statements only. This is not a
# general sentiment model; sarcasm, implicit emotions and quoted fiction abstain.
_WORDS = {
    'joy': r'開心|高興|快樂|happy|glad|delighted',
    'sadness': r'難過|傷心|悲傷|沮喪|sad|upset|heartbroken',
    'anger': r'生氣|憤怒|火大|angry|furious|mad',
    'fear': r'害怕|恐懼|緊張|擔心|afraid|scared|worried|anxious',
    'disgust': r'噁心|反感|厭惡|disgusted|repulsed',
    'surprise': r'驚訝|吃驚|震驚|surprised|shocked',
}


def observe_user_emotions(text):
    scores, evidence = normalize_emotions({}), []
    # Exclude quotes and hypothetical clauses rather than interpreting them as
    # a self-report. Unknown means unknown, not neutral or emotionally stable.
    text = re.sub(r'「[^」]*」|『[^』]*』|“[^”]*”|"[^"\n]*"', '', text)
    for clause in re.split(r'[。！？!?；;\n，,]', text):
        if re.search(r'如果|假如|假設|例如|譬如|他說|她說|角色說|(?:\bif\b|for example|he said|she said)', clause, re.I):
            continue
        for key, words in _WORDS.items():
            pattern = (r'我(?:現在|今天|真的|覺得|感到|突然|有點|非常|好|很|超|太|好像|還是|也|又|十分|相當|特別|超級|的確|實在)*'
                       r'(?:' + words + r')|\bI(?:\s+am|\s+feel|\s+felt|[’\x27]m)\s+'
                       r'(?:(?:very|really|so|a little|quite)\s+)?(?:' + words + r')\b')
            match = re.search(pattern, clause, re.I)
            if match and not re.search(r'不是說|並非|不是因為', clause[:match.start()]):
                scores[key] = 60.0
                evidence.append(match.group(0))
    return {'status': 'explicit' if evidence else 'unknown', 'emotions': scores,
            'evidence': evidence, 'source': 'current_user_message',
            'method': 'explicit_self_report_rules_v1'}
