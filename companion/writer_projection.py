"""Writer-only projection. Backend provenance is never deleted or renumbered."""
from copy import deepcopy
from datetime import date, datetime


def model_time(clock):
    """One human-readable clock sentence; retain full clock provenance in state."""
    today = date.fromisoformat(clock['date'])
    weekday = '一二三四五六日'[today.weekday()]
    hour, minute = clock['local_time'].split(':')[:2]
    return (f'今天是{today.year}年{today.month}月{today.day}日星期{weekday}，'
            f'現在是{int(hour)}點{minute}分。')


def model_metadata(value):
    """Short model timestamps, no transport clock provenance. Never edit prose."""
    if isinstance(value, dict):
        result={}
        for key, child in value.items():
            if key == 'clock_source':continue
            if key in {'at','deadline','valid_from','valid_to','delivered_at'} and isinstance(child,str):
                try:child=datetime.fromisoformat(child).strftime('%Y-%m-%d %H:%M')
                except ValueError:pass
            result[key]=model_metadata(child)
            if key in {'scene','scene_after','context'} and isinstance(result[key],dict):
                result[key]={k:v for k,v in result[key].items() if v not in ('',None,{},[])}
        if isinstance(result.get('context'),dict) and result.get('at')==result['context'].get('at'):
            result['context'].pop('at',None)
        return result
    if isinstance(value,list):return [model_metadata(child) for child in value]
    return deepcopy(value)


def dialogue_metadata(messages, content=False):
    """Scene snapshots become a baseline and explicit changes, not repeated dumps."""
    result=[]
    previous=None
    for message in messages:
        cleaned=model_metadata(message)
        row={'speaker':'character' if message['role']=='assistant' else 'user'}
        if cleaned.get('at'):row['at']=cleaned['at']
        if content:row['content']=message['content']
        context=cleaned.get('context',{})
        scene=context.get('scene')
        if scene is None:
            row['context']={'scene':'unknown'}
            previous=None
        else:
            changes={}
            if scene!=previous:changes['scene']=scene
            after=context.get('scene_after')
            if after is not None and after!=scene:changes['scene_after']=after
            if changes:row['context']=changes
            previous=after if after is not None else scene
        result.append(row)
    return result


INTERNAL_KEYS = frozenset({
    'id', 'row', 'rowid', 'index', 'source_index', 'message_index', 'history_index',
    'source_ref','source_digest','message_id', 'turn_id', 'event_id', 'source_id', 'update_id', 'ref',
    'revision', 'source_revision', 'target_event', 'clock_source', 'source', 'notice_id', 'sent_at',
})


def writer_projection(value):
    if isinstance(value, dict):
        return {key: writer_projection(child) for key, child in value.items()
                if key not in INTERNAL_KEYS and not key.startswith('_')}
    if isinstance(value, list):
        return [writer_projection(child) for child in value]
    return deepcopy(value)
