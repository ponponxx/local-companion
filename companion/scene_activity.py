"""Schedule arrival semantics; travel prose must not become a destination activity."""
from copy import deepcopy
import re

# Legacy plans had no arrival activity field. Recognition is deliberately bounded;
# unknown wording remains observable, rather than inventing a destination task.
TRAVEL = re.compile(r'前往|出發去|出發到|趕往|赴往|啟程|在路上|途中|\b(?:travell?ing|heading|en route|on (?:the|my) way)\b',re.I)


def is_travel(activity):
    return bool(TRAVEL.search(activity or ''))


def arrival_activity(item):
    value=item.get('arrival_activity','').strip()
    if value and not is_travel(value):return value
    if is_travel(item.get('activity','')):
        return ''
    return item.get('activity','')


def reconcile_arrival(state, trace):
    """Repair only a schedule-owned activity with a recorded, confirmed arrival."""
    item=state.get('life_current',{})
    if not item or state.get('activity')!=item.get('activity') or not is_travel(state.get('activity','')):
        return False
    scene=state['scene']
    if scene.get('pending') or (scene.get('character_location') or scene.get('location'))!=item.get('location'):
        return False
    arrivals=[e for e in state.get('scene_history',[]) if e['phase'] in {'arrived','moved','closed'}]
    if not arrivals:return False
    last=arrivals[-1]
    if last['phase']!='arrived' or last.get('source')!='daily_schedule' or last['after'].get('location')!=item['location']:
        return False
    before=state['activity'];state['activity']=arrival_activity(item)
    state.setdefault('state_repairs',[]).append({'kind':'arrival_activity','at':state['now'],
        'before':before,'after':state['activity'],'arrival_source':last['id']})
    trace.add(1,'scene.activity_reconciled',{'activity':before,'arrival_source':last['id']},
        {'activity':state['activity'],'availability':state['availability'],'arrival_confirmed':True},
        '日程已確認抵達，停止沿用途中活動；沒有抵達後活動來源則留空，不猜工作或休息；忙閒設定保留。')
    return True
