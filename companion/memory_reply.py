"""Source-bound recall handoff, not a semantic judge or another world state.

Only inspected/selected sources and valid current notes can contribute. A
source-checked utterance is a recorded claim, never proof its action occurred.
"""
from copy import deepcopy
from .scene_topic_index import build
from .story_events import claims, digest, ACTORS, STAGE_LABELS
from .memory_metadata import reported_time_refs
from .writer_projection import model_metadata
from .working_memory import current_items

VERSION = 'memory-reply-v1'
FORMAL_PHASES = {'proposed':'已提出、未接受', 'accepted':'已接受、未開始',
    'started':'已開始、未完成', 'completed':'已完成', 'rejected':'已拒絕', 'cancelled':'已取消'}


def compile_memory_reply(state, query, recent, retrieved, recap):
    """Return a compact typed packet and separate provenance for backend trace.

    This does not prove a source answers every part of the question. No numeric
    confidence, invented event, missing-source completion, or role inference.
    """
    history=state.get('history',[])
    selected=set();retrieval_sources=set()
    for row in recent:
        if isinstance(row.get('_index'),int):selected.add(row['_index'])
    for item in list(retrieved)+list(recap):
        if item.get('kind') in {'manual','scene_event','life_event'}:continue
        indices={i for i in item.get('source_indices',[item.get('source_index')]) if isinstance(i,int)}
        selected.update(indices)
        if item in retrieved:retrieval_sources.update(indices)
    # Current input is a question, not evidence of the event it asks about.
    if recent and recent[-1].get('role')=='user':selected.discard(recent[-1].get('_index'))
    selected={i for i in selected if 0<=i<len(history)}
    scene_query=state.get('memory',{}).get('selection',{}).get('scene_query',{})
    if scene_query.get('explicit'):selected &= set(scene_query.get('sources',[]))
    ordered_sources=sorted(selected,key=lambda i:(i in retrieval_sources,i),reverse=True)
    facts=[];bindings=[];missing=[];uncertainty=[];seen=set()
    def add(fact,indices):
        signature=repr(fact)
        if signature in seen:return
        seen.add(signature);facts.append(fact);bindings.append(sorted(set(indices)))
    # Valid current notes are already a protected Writer component. Reference
    # their current status, rather than promoting obsolete corrections.
    for note in current_items(state):
        sources=[s['index'] for s in note['sources']]
        if not (set(sources)&selected):continue
        if note.get('pending_replaces') or note.get('pending_close'):continue
        if len(facts)>=2:break
        add({'basis':'有效互動筆記',
             'subject':{'user':'使用者','character':'角色','shared':'雙方'}[note['subject']],
             'content':note['text'],
             'status':{'shared':'雙方已承接','corrected':'有效更正','reported':'單方說法'}.get(note['status'],'單方說法')},sources)
        if note.get('conflicts'):uncertainty.append('有效筆記有未解決的不同說法，不擅自解除或合併。')
    # Formal invitation status is stronger than old dialogue descriptions, but
    # only a selected source's matching invitation may be handed off.
    formal_refs=set()
    for i in ordered_sources:
        row=history[i]
        role='user' if row['role']=='user' else 'assistant'
        for event in state.get('events',[]):
            ref=event.get('ref') or event.get('id')
            if (not row.get('turn_id') or event.get('turn_id')!=row['turn_id']
                or event.get('source')!=role or not event.get('evidence')
                or event['evidence'] not in row['content'] or ref in formal_refs):continue
            item=state.get('invitations',{}).get(ref,{})
            phase=FORMAL_PHASES.get(item.get('status'))
            if not phase:continue
            formal_refs.add(ref)
            if len(facts)>=3:continue
            add({'basis':'正式進度',
                 'actor':ACTORS.get(item.get('actor'),'人物未確認'),
                 'target':ACTORS.get(item.get('target'),'人物未確認'),
                 'content':item['action'],'status':phase},[i])
    for i in ordered_sources:
        row=history[i]
        for claim in claims(row):
            if len(facts)>=3:break
            if claim['actor']=='unknown':missing.append('來源的行動者尚未確認，不能用發話者代替。')
            if claim['stage']=='unknown':missing.append('來源的事件階段尚未確認，不能推成完成。')
            add({'basis':'原話說法',
                 'speaker':ACTORS[claim['speaker']], 'actor':ACTORS[claim['actor']],
                 **({'target':ACTORS[claim['target']]} if claim['target']!='none' else {}), 'content':claim['action'],
                 'status':STAGE_LABELS[claim['stage']],
                 **({'spoken_at':model_metadata({'at':row.get('at','')}).get('at') or '未記錄',
                     'time_words':reported_time_refs(row)} if reported_time_refs(row) else {})},[i])
    index=build(state)
    contexts=[]
    # Snapshot is the context of a statement, never the event's implied venue.
    context_sources=selected if scene_query.get('explicit') else set()
    for i in sorted(context_sources,reverse=True):
        link=index['messages'][i]
        if not link['character_location'] and not link['user_location']:continue
        contexts.append({'spoken_at':model_metadata({'at':history[i].get('at','')}).get('at') or '未記錄','meaning':'發話背景，不代表事件地點',
            'character_location':link['character_location'] or '未記錄',
            'user_location':link['user_location'] or '未記錄'})
        break
    if scene_query.get('ambiguous'):
        uncertainty.append('指定地點有多次到訪；只列不同次的來源，不任選一次或混成同一事件。')
    if any(not index['messages'][i]['visit'] for i in context_sources):
        uncertainty.append('部分原話未綁定到訪，不補推抵達或同行。')
    if not selected:missing.append('本輪未選到可核對的過去原文；不代表事情沒有發生。')
    elif not facts:missing.append('只有原話，沒有可結構化核對的人物／事件進度；可引用原話，不補造行動。')
    if contexts and contexts[0]['user_location']=='未記錄':
        missing.append('使用者當時位置未記錄。')
    packet={'available_facts':facts,'missing_information':list(dict.fromkeys(missing)),
            'uncertainty':list(dict.fromkeys(uncertainty)),
            'answer_scope':'只回答來源支持的部分；正式進度優先於舊說法。其餘說明未記錄或不確定，不把未取到當作沒發生。'}
    if contexts:packet['speech_context']=contexts[0]
    audit={'version':VERSION,'query':query,'selected_sources':sorted(selected),'fact_sources':bindings,
           'source_digests':{str(i):digest(history[i]) for i in sorted(selected)},'formal_refs':sorted(formal_refs),
           'packet':deepcopy(packet)}
    return packet,audit
