"""Reported narrative events; never an authority for world-state updates."""
import hashlib
import json
from .writer_projection import model_metadata

STAGES = ('completed','ongoing','planned','proposed','hypothetical','negated','unknown')
STAGE_LABELS = {'completed':'自述／描述已完成','ongoing':'自述／描述進行中',
                'planned':'計畫，尚未完成','proposed':'提議，尚未確認同意',
                'hypothetical':'假設，非已發生','negated':'明確否定發生','unknown':'階段未確認'}
ACTORS = {'user':'使用者','character':'角色','unknown':'人物未確認','none':'無特定對象'}


def digest(message):
    source={k:message.get(k) for k in ('role','content','at','context')}
    return hashlib.sha256(json.dumps(source,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def annotate(message, observation):
    speaker='user' if message['role']=='user' else 'character'
    accepted=[];rejected=0
    for event in observation.get('story_events',[])[:4]:
        if not isinstance(event,dict):rejected+=1;continue
        actor,target,stage=(event.get(k) for k in ('actor','target','stage'))
        action,evidence=event.get('action',''),event.get('evidence','')
        if (actor not in {'user','character','unknown'} or target not in ACTORS or stage not in STAGES
                or not isinstance(action,str) or not 0<len(action)<=120
                or not isinstance(evidence,str) or not evidence or evidence not in message['content']
                or action not in evidence or (speaker=='character' and actor=='user')):
            rejected+=1;continue
        claim={'speaker':speaker,'actor':actor,'target':target,'stage':stage,
               'action':action,'evidence':evidence}
        if claim not in accepted:accepted.append(claim)
    topic=observation.get('topic','')
    message['_story']={'digest':digest(message),'topic':topic[:80] if isinstance(topic,str) else '',
                       'available':'story_events' in observation,'events':accepted,
                       'act':observation.get('utterance',{}).get('act','unknown'),
                       'purpose':observation.get('utterance',{}).get('purpose','other')}
    return accepted,rejected


def claims(message):
    data=message.get('_story',{})
    return data.get('events',[]) if data.get('digest')==digest(message) else []


def describe(message, include_source=True):
    at=model_metadata({'at':message.get('at','')}).get('at') or '時間未記錄'
    speaker=ACTORS['user' if message['role']=='user' else 'character']
    events=claims(message)
    if not events:return f'{at}，{speaker}說：{message["content"]}'
    lines=[]
    for event in events:
        target='' if event['target']=='none' else '；對象：'+ACTORS[event['target']]
        lines.append(f'{at}，{speaker}的說法：行動者是{ACTORS[event["actor"]]}{target}；'
                     f'{STAGE_LABELS[event["stage"]]}：{event["action"]}。')
    # Source text still covers details the event extractor may have missed.
    if include_source:lines.append(f'{speaker}原話：{message["content"]}')
    return '\n'.join(lines)
