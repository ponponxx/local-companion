"""Explicit photo-only wardrobe instructions; never writes character state."""
from copy import deepcopy
import re
from .visual_state import SLOT_NAMES,is_removed

SCHEMA={'type':'array','maxItems':4,'items':{'type':'object','additionalProperties':False,
 'required':['slot','operation','intent','value','evidence'],
 'properties':{'slot':{'enum':list(SLOT_NAMES)},
  'operation':{'enum':['wear','remove']},'intent':{'enum':['requested','reference','hypothetical','negated']},
  'value':{'type':'string','maxLength':80},'evidence':{'type':'string','maxLength':120}}}}
RULES=(' photo_clothing_overrides是本張照片的造型要求，不是修改正式狀態。appearance_authoritative只表示原穿著基準；不能因此漏掉user_request的換裝要求。'
       '例如user_request「這張穿紅色外套」必須輸出[{"slot":"outerwear","operation":"wear","intent":"requested","value":"紅色外套","evidence":"穿紅色外套"}]。'
       '「保持現在的衣著」輸出[]。photo_subject固定為character；如果只描述拍攝者自己穿甚麼，不修改畫面角色，輸出[]。'
       ' photo_clothing_overrides lists only explicit wardrobe instructions in the CURRENT user_request for this photo; otherwise []. '
       'The subject is fixed by photo_subject=character, never the viewer. Only change the portrait subject when the request asks for it. '
       'intent=requested for direct instructions, reference for past/current descriptions, hypothetical/negated for those cases. '
       'value is the concrete garment quoted verbatim inside evidence, evidence must be an exact quote of user_request. '
       'remove may use empty value. Never infer changes from dialogue, personality or pose. '
       'The program applies validated slots to this photo only, without changing real current clothing. '
       'Do not repeat wardrobe instructions in expression or camera.')


def project(slots,rows,request):
    result=deepcopy(slots);accepted=[];rejected=0
    for row in rows:
        if not isinstance(row,dict):rejected+=1;continue
        slot=row.get('slot');evidence=row.get('evidence','');value=row.get('value','');op=row.get('operation')
        if (row.get('wearer','character')!='character' or row.get('intent')!='requested' or slot not in SLOT_NAMES
                or not isinstance(evidence,str) or not evidence or evidence not in request or op not in {'wear','remove'}):
            rejected+=1;continue
        if op=='wear' and (not isinstance(value,str) or not value or len(value)>80 or value not in evidence or is_removed(value)):
            rejected+=1;continue
        # Evidence may quote only the garment noun. Resolve its local clause
        # before accepting it; explicit viewer clothes never dress the portrait.
        clauses=[c for c in re.split(r'[，。；！？,;!?\n]',request) if evidence in c]
        viewer=re.compile(r'我(?:也|正|在|會|要|已經|已|正在|不|沒|沒有)?(?:穿|脫|換)|\bI(?: am|\x27m)? (?:wear|wearing|put on|take off)',re.I)
        if any(viewer.search(c) for c in clauses):
            rejected+=1;continue
        if slot in accepted:  # conflicting operations are not resolved by order
            raise ValueError('同一張照片對同一衣著槽有多項修改，請一次指定一種穿著。')
        result[slot]='未穿著' if op=='remove' else value
        if op=='wear' and slot=='dress':
            result.update(top='未穿著',bottom='未穿著')
        elif op=='wear' and slot in {'top','bottom'} and result.get('dress') not in {None,'未確認','未穿著'}:
            result['dress']='未穿著'
        accepted.append(slot)
    return result,accepted,rejected


def render(slots):
    return '；'.join(f'{SLOT_NAMES[k]}：{v}' for k,v in slots.items() if k in SLOT_NAMES)
