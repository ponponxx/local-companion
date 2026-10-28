"""Explicit Writer reading model. Database state is not the prompt schema."""
from copy import deepcopy
from .writer_projection import model_metadata, writer_projection


def scene_facts(scene):
    result={}
    location=scene.get('character_location') or scene.get('location')
    if location and location not in {'未確認','unknown'}:result['character_location']=location
    if scene.get('user_location'):result['user_location']=scene['user_location']
    if location and location not in {'未確認','unknown'} and scene.get('user_location') and scene['user_location'] not in {'未確認','unknown'} and location!=scene['user_location']:
        result['interaction_setting']='雙方目前分處不同地點，沒有共同到場的確認。'
    if scene.get('positions'):result['positions']=writer_projection(scene['positions'])
    trips=scene.get('journeys',{})
    if trips:
        result['journeys']={person:{k:trip[k] for k in ('origin','destination','status') if trip.get(k)} for person,trip in trips.items()}
        for person,trip in trips.items():
            companions=[other for other,t in trips.items() if other!=person and trip.get('group') and t.get('group')==trip['group'] and t.get('destination')==trip.get('destination') and t.get('status')=='moving']
            if companions:result['journeys'][person]['companions']=companions
    if scene.get('status')=='none':result['status']='unknown'
    pending=scene.get('pending') or scene.get('pending_transition')
    if pending:
        result['movement']={'destination':pending['location'],
            'status':'moving' if pending.get('delivery') in {'delivered','simulated_delivered'} else 'not_departed'}
    return result


def historical_context(context, state):
    """Historical overrides only; equal-to-current facts have one owner."""
    current=scene_facts(state['scene'])
    def differences(snapshot):
        value=scene_facts(snapshot)
        if snapshot.get('activity') and snapshot['activity']!=state.get('activity'):
            value['activity']=snapshot['activity']
        return {key:child for key,child in value.items() if child!=current.get(key)}
    if not context.get('scene'):return {'scene':'unknown'}
    result={}
    before=differences(context['scene'])
    if before:result['scene']=before
    if context.get('scene_after'):
        after=differences(context['scene_after'])
        if after!=before:result['scene_after']=after or 'current'
    return result


def dialogue_facts(messages, state):
    # Start at current facts; thereafter only note historical overrides changing.
    result=[]
    previous={}
    for message in messages:
        row={'speaker':'character' if message['role']=='assistant' else 'user'}
        if message.get('at'):row['at']=message['at']
        context=historical_context(message.get('context',{}),state)
        if context!=previous:row['context']=context or {'scene':'current'}
        previous=context
        result.append(model_metadata(row))
    return result


def finish_contract(contract,state):
    result=deepcopy(contract)
    result['character_definition'].pop('name',None)  # identity owns names
    if isinstance(result.get('user_profile'),list):
        result['user_profile']=[f for f in result['user_profile'] if f['category'] not in {'name','稱呼','姓名'}]
    if 'current_facts' in result:result['current_facts']['scene']=scene_facts(state['scene'])
    from .scene_topic_index import enabled
    if enabled(state) and 'current_facts' in result and not state['scene'].get('user_location'):
        result['current_facts']['scene']['user_location']='未確認'
    if 'current_facts' in result and not result['current_facts'].get('activity'):
        result['current_facts']['activity']='未確認'
    # A travelling character's trip is owned by scene.movement, not activity.
    if (result.get('current_facts',{}).get('scene',{}).get('movement',{}).get('status')=='moving'
            or state['scene'].get('journeys',{}).get('character',{}).get('status')=='moving'):
        result['current_facts'].pop('activity',None)
    scope=contract.get('_information_scope','unknown')
    from .journeys import scene_handoff_note
    handoff=scene_handoff_note(state)
    include_handoff=scope!='general' and not result.get('proactive_story')
    outline=result['decision'].get('response_outline','')
    if handoff and not include_handoff and outline.startswith(handoff):
        result['decision']['response_outline']=outline[len(handoff):]
    elif handoff and include_handoff and handoff not in outline:
        result['decision']['response_outline']=handoff+outline
    if scope in {'current_state','general'}:
        for key in ('appearance','user_appearance','presentation'):
            result.get('current_facts',{}).pop(key,None)
        result.pop('appearance_instruction',None)
        result.pop('user_profile',None)
    if scope=='general':result.pop('current_facts',None)
    if isinstance(result.get('user_profile'),list) and result['user_profile']:
        labels={'interest':'興趣','habit':'習慣','preference':'偏好','goal':'目標','boundary':'界線'}
        result['user_profile']='使用者的個人資料：\n'+'\n'.join(labels.get(f['category'],f['category'])+'：'+f['value'] for f in result['user_profile'])
    elif not result.get('user_profile'):result.pop('user_profile',None)
    if scope!='general':
        from datetime import datetime
        now=datetime.fromisoformat(state['now'])
        today=[x for x in state.get('schedule',[]) if datetime.fromisoformat(x['at']).astimezone(now.tzinfo).date()==now.date()]
        result['daily_plan']={'status':'available' if today else 'unavailable',
            'upcoming':[{'time':datetime.fromisoformat(x['at']).strftime('%H:%M'),
                         'location':x['location'],'activity':x.get('arrival_activity') or x['activity']}
                        for x in sorted(today,key=lambda x:x['at']) if datetime.fromisoformat(x['at'])>now][:3],
            'meaning':'日程是計畫，不代表已出發或完成；沒有資料的現址、行程及昨晚行蹤須保留未知。'}
    if result.get('target_invitation') is None:result.pop('target_invitation',None)
    else:
        result['target_invitation']={key:result['target_invitation'][key] for key in
            ('actor','target','action','status','conditions') if result['target_invitation'].get(key)}
    if not result.get('legacy_background_summary_fallible'):result.pop('legacy_background_summary_fallible',None)
    if result['decision'].get('intimacy_intention')=='none':result['delivery'].pop('adult_expression',None)
    result.pop('_life_share_ref',None)
    result.pop('story_context',None)
    story=result.pop('_memory_packet_text','')
    result.pop('past_memory',None);result.pop('_recent_story_events',None);result.pop('current_interaction',None)
    if story:result['story_context']=story
    return model_metadata(writer_projection(result))
