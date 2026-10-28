"""Explicit wearer-owned appearance extraction, within the existing Observer.

No extra inference call. Text evidence gates remain strict. This contract makes
ownership explicit but cannot prove that the model understood a sentence.
"""
from .visual_state import SLOT_NAMES,is_removed

SCHEMA={'type':'array','maxItems':8,'items':{'type':'object','additionalProperties':False,
 'required':['wearer','slot','operation','phase','value','evidence'], 'properties':{
  'wearer':{'enum':['user','character']},'slot':{'enum':['hair',*SLOT_NAMES]},
  'operation':{'enum':['wear','remove','style']},
  'phase':{'enum':['completed','observed','proposed','negated','unknown']},
  'value':{'type':'string','maxLength':160},'evidence':{'type':'string','maxLength':240}}}}
RULES=('appearance_changes逐項描述本輪外觀，沒有就空陣列。wearer是衣物／髮型屬於誰，不是幫忙的人；'
 '以下兩例限speaker=user：『我幫妳披好了外套』wearer=character，『妳幫我披好了外套』wearer=user。'
 'speaker=character時我指character、你指user；不能替user建立外觀變化。'
 '穿好／披上且已完成用operation=wear、phase=completed；已穿著用observed；脫下用remove。'
 '邀請、將要、假設用proposed或不列；還沒、沒有做用negated或不列，不能當已完成。'
 'slot是衣著槽或hair；value寫引文裡的具體衣物名稱，remove可空字串。evidence只引用current_text原文，不改字。'
 '一般events不再放appearance事件，勿把外觀塞入care_benefit／pose。')


def events(rows,text,trace,source='user'):
    result=[];rejected=[]
    for row in rows:
        evidence=row['evidence'];operation=row['operation'];slot=row['slot']
        if source=='assistant' and row['wearer']!='character':
            rejected.append('角色回文不能替user更新外觀');continue
        if not evidence or evidence not in text:
            rejected.append('外觀引文不在本輪原文');continue
        if row['phase'] not in {'completed','observed'}:
            rejected.append('外觀仍是提議、否定或未知');continue
        if (slot=='hair') != (operation=='style'):
            rejected.append('外觀操作與槽位不相容');continue
        if operation=='wear' and (not row['value'] or row['value'] not in evidence or is_removed(row['value'])):
            rejected.append('衣物名稱未出現在引文或穿著操作矛盾');continue
        result.append({'kind':'appearance','actor':row['wearer'],'target':row['wearer'],
            'stage':row['phase'],'field':slot,'value':'已脫下' if operation=='remove' else row['value'],
            'evidence':evidence,'strength':0,'ref':''})
    if rows:trace.add(2 if source=='user' else 5,'appearance.observation',{}, {'accepted':len(result),'rejected':rejected},'外觀所屬人與穿脫獨立提取；沿用同次Observer與逐字證據，不猜缺失。')
    return result
