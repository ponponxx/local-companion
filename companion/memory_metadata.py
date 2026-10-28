"""Backend-owned timestamps and scene provenance; never inferred by a model."""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import re


def clock_context(at, source='virtual_clock'):
    value=datetime.fromisoformat(at) if isinstance(at,str) else at
    return {'now':value.isoformat(),'date':value.date().isoformat(),
            'yesterday_date':(value.date()-timedelta(days=1)).isoformat(),
            'tomorrow_date':(value.date()+timedelta(days=1)).isoformat(),'local_time':value.strftime('%H:%M:%S'),
            'weekday':['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'][value.weekday()],
            'timezone':getattr(value.tzinfo,'key',None) or str(value.tzinfo),'source':source}


def scene_snapshot(state):
    scene=state['scene']
    result={k:deepcopy(scene.get(k,'')) for k in ('status','location','character_location','user_location','positions')}
    result['activity']=state.get('activity','')
    if scene.get('journeys'):result['journeys']=deepcopy(scene['journeys'])
    pending=scene.get('pending')
    if pending:
        result['pending_transition']={k:pending.get(k,'') for k in ('location','activity','delivery','deadline')}
    return result


def message_context(state, at=None, source=None):
    clock=state.get('clock',{})
    return {'at':at or state['now'],'clock_source':source or clock.get('source','virtual_clock'),
            'scene':scene_snapshot(state)}


def with_scene_after(context, state):
    result=deepcopy(context)
    after=scene_snapshot(state)
    if result.get('scene')!=after:result['scene_after']=after
    return result


def dialogue_context(state, transient=None):
    messages=state['history'][state['summary_through']:]+(transient or [])
    return [{'message_index':i+1,'role':h['role'],'at':h.get('at',''),
             'context':deepcopy(h.get('context',{}))} for i,h in enumerate(messages)]


def record_scene(state, phase, before, source, at=None, actor='character'):
    after=scene_snapshot(state)
    at=at or state['now']
    data={'at':at,'phase':phase,'actor':actor,'source':source,'before':deepcopy(before),'after':after,
          'history_index':len(state['history']),'clock_source':state.get('clock',{}).get('source','virtual_clock')}
    data['id']=hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:20]
    history=state.setdefault('scene_history',[])
    if not any(e['id']==data['id'] for e in history):history.append(data)
    before_location=before.get('character_location') or before.get('location')
    after_location=after.get('character_location') or after.get('location')
    if state.get('story_events_enabled') and phase in {'arrived','moved'} and after_location!=before_location:
        from .life_events import plan_visit
        plan_visit(state,data['id'],datetime.fromisoformat(at))
    return data


def target_dates(query, now):
    """Resolve common calendar references using the supplied host/sandbox clock."""
    stamp=datetime.fromisoformat(now)
    values=set(re.findall(r'\b\d{4}-\d{2}-\d{2}\b',query))
    aliases=[(r'今天|今日|\btoday\b',0),(r'前天|\bday before yesterday\b',2),
             (r'(?<!前)昨天|昨日|昨晚|(?<!before )\byesterday\b',1)]
    for pattern,days in aliases:
        if re.search(pattern,query,re.I):values.add((stamp.date()-timedelta(days=days)).isoformat())
    return sorted(values)


def date_matches(at, dates, now):
    if not dates:return True
    if not at:return False
    try:return datetime.fromisoformat(at).astimezone(datetime.fromisoformat(now).tzinfo).date().isoformat() in dates
    except (ValueError,TypeError):return False


def searchable_text(item):
    scene=item.get('context',{}).get('scene',{})
    after=item.get('context',{}).get('scene_after',{})
    return ' '.join(str(v) for v in (item.get('at',''),after.get('location',''),after.get('character_location',''),scene.get('location',''),scene.get('character_location',''),
                    scene.get('user_location',''),scene.get('activity',''),item['text']) if v)


def chronology_key(item):
    try: stamp=datetime.fromisoformat(item.get('at','')).timestamp()
    except (ValueError,TypeError): stamp=float('-inf')
    return stamp,item.get('source_index',0)


def compact_context(value):
    """Omit unknown/empty metadata in bounded recall, without changing its source."""
    if isinstance(value,dict):
        result={k:compact_context(v) for k,v in value.items()}
        return {k:v for k,v in result.items() if v not in ('',None,{},[])}
    if isinstance(value,list):return [compact_context(v) for v in value]
    return deepcopy(value)


def reported_time_refs(message):
    """Literal relative-date anchors, not assertions that an event occurred."""
    from zoneinfo import ZoneInfo
    try:
        at=datetime.fromisoformat(message.get('at',''))
        if at.tzinfo is None:return []
        at=at.astimezone(ZoneInfo('Asia/Taipei'))
    except (TypeError,ValueError):return []
    text=message.get('content','');refs=[]
    for pattern,word,offset in [(r'前天|\bday before yesterday\b','前天',-2),
        (r'(?<!前)昨天|昨日|昨晚|(?<!before )\byesterday\b','昨天',-1),
        (r'今天|今日|\btoday\b','今天',0),(r'明天|明日|\btomorrow\b','明天',1)]:
        if re.search(pattern,text,re.I):refs.append({'word':word,'date':(at+timedelta(days=offset)).date().isoformat()})
    return refs
