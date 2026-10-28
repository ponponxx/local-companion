"""Source-bound conversational working memory. Never writes physical or policy state."""
from copy import deepcopy
import hashlib
import re

VERSION='working-v3'
SCHEMA={'type':'array','maxItems':4,'items':{'type':'object','additionalProperties':False,
 'properties':{'op':{'enum':['add','confirm','replace','close']},'kind':{'enum':['situation','fiction','open_loop','interaction']},
 'subject':{'enum':['user','character','shared']},'ref':{'type':'string'},'text':{'type':'string'},
 'phase':{'enum':['proposed','ongoing','completed','hypothetical','negated','unknown']},'evidence':{'type':'string'}},
 'required':['op','kind','subject','ref','text','phase','evidence']}}
RULES='''working_updates必填，無更新用[]，最多4項。subject是設定所屬人，不是發話者或執行者；「妳把我變成紙鶴」subject=user。text是完整可獨立閱讀的現況短句，用「使用者／角色／雙方」明確指人，不用我你妳；例如「使用者目前是藍色紙鶴」或「銀色書籤由角色保管」。text最多120字，evidence仍須逐字引用current_text，正文與證據分開。只記當前互動、虛構設定、待問，不重記正式地點衣著及Profile。add用空ref；confirm/replace/close用snapshot.working_memory已有ref。對方明確承接既有說法才confirm，confirm保留原設定細節，改內容才replace；提議／假設不算完成。這是故事說法，不等於events，不提交物理狀態。明確更正用replace，實際結束用close；不因換話題或時間經過關閉。角色否認既有共同設定只記衝突，不可抹除。'''
RULES+='''\nsnapshot.working_memory才是已儲存筆記；history出現過不等於已存。current_text確立或承接了仍持續的情境、設定、尚待回答問題，而筆記缺少時必須add，即使本輪沒有新物理事件。已有相同筆記時，另一方明確承接用confirm。只根據current_text寫入，不從背景補造證據。更正自己狀態可直接生效；更改對方或雙方已承接設定須另一方確認，未確認時舊設定仍有效。角色實際回答待問事項後可close；同意稍後回答不算回答。'''


def ensure(state):
    memory=state.setdefault('story_memory',{})
    old=memory.get('working')
    if old and old.get('version')!=VERSION:
        memory['working']={'version':VERSION,'revision':old.get('revision',0)+1,'items':[],'applied':{},'retired_items':deepcopy(old.get('items',[]))}
    working=memory.setdefault('working',{'version':VERSION,'revision':0,'items':[],'applied':{}})
    return working


def source(message,index):
    role='user' if message['role']=='user' else 'character'
    key=str(message.get('turn_id') or message.get('message_id') or index)+':'+role
    digest=hashlib.sha256(message['content'].encode()).hexdigest()
    return {'key':key,'digest':digest,'role':role,'quote':'','index':index,'at':message.get('at','')}


def valid_source(state,s):
    # A quote remains evidence only while its original source is still present and unchanged.
    rows=state.get('history',[])
    i=s.get('index',-1)
    if not isinstance(i,int) or not 0<=i<len(rows):return False
    actual=source(rows[i],i)
    return actual['key']==s['key'] and actual['digest']==s['digest'] and s['quote'] in rows[i]['content']


def formal_status(state,item):
    ref=item.get('formal_ref')
    return state.get('invitations',{}).get(ref,{}).get('status') if ref else None


def current_items(state):
    if state.get('story_memory',{}).get('working',{}).get('version')!=VERSION:return []
    candidates={i['id']:i for i in state.get('story_memory',{}).get('working',{}).get('items',[])
            if i['status']!='closed' and all(valid_source(state,s) for s in i['sources'])
            and (not i.get('formal_ref') or formal_status(state,i) in {'proposed','accepted','started'})}
    def live_dependency(item):
        seen=set()
        while item['status']=='reported' and item.get('pending_replaces'):
            if item['id'] in seen:return False
            seen.add(item['id'])
            item=candidates.get(item['pending_replaces'])
            if item is None:return False
        return True
    return [i for i in candidates.values() if live_dependency(i)]


def formal_reference(state,message,quote):
    source_role='user' if message['role']=='user' else 'assistant'
    for e in reversed(state.get('events',[])):
        evidence=e.get('evidence','')
        if (e.get('turn_id')==message.get('turn_id') and e.get('source')==source_role
            and evidence and (evidence in quote or quote in evidence)):
            ref=e.get('ref') or e.get('id')
            if ref in state.get('invitations',{}):return ref
    return ''


def observer_view(state):
    return [{'ref':i['id'],'kind':i['kind'],'subject':i['subject'],'text':i['text'],
             'phase':i['phase'],'status':i['status'],'speaker':i['sources'][0]['role'],
             'conflicts':[s['quote'] for s in i.get('conflicts',[])[-1:]],'formal_status':formal_status(state,i),
             'pending_replaces':i.get('pending_replaces',''),'pending_close':i.get('pending_close',False)}
            for i in current_items(state)]


def reading(state, items=None):
    labels={'user':'使用者','character':'角色','shared':'雙方'}
    phases={'proposed':'只提出','ongoing':'描述進行中','completed':'描述已完成',
            'hypothetical':'假設','negated':'否定說法','unknown':'未確認'}
    result=[]
    for item in current_items(state) if items is None else items:
        formal=formal_status(state,item)
        if item.get('pending_replaces') and item['status']=='reported':prefix='待另一方確認的'+('結束說法' if item.get('pending_close') else '替代說法')+'，舊設定仍有效'
        elif item['status']=='corrected':prefix=labels[item['sources'][0]['role']]+'對自身或原說法的明確更正，目前有效'
        elif formal:prefix='正式邀請：'+{'proposed':'已提出未接受','accepted':'已接受未開始','started':'已開始未完成'}[formal]
        elif item['kind']=='open_loop':prefix='待處理事項（'+('雙方已約定，但' if item['status']=='shared' else '')+'不代表已完成）'
        elif item['kind']=='interaction' and item['phase']=='completed':prefix=('雙方已承接的互動紀錄' if item['status']=='shared' else '單方互動紀錄')+'；不據此推定仍有待執行的約定'
        elif item['status']=='shared':prefix='雙方已承接、尚未解除的共同情節'
        else:prefix=labels[item['sources'][0]['role']]+'的說法，'+phases[item['phase']]
        result.append(prefix+'（主體：'+labels[item['subject']]+'；依據來自'+labels[item['sources'][0]['role']]+'）：'+item['text'])
        if item.get('conflicts'):result.append('後續有不同說法，但未有效解除上述設定；衝突原文留在診斷，不作另一份現況。')
    return ('互動筆記不覆寫正式地點、衣著、邀請進度或權限。\n'+'\n'.join(result)) if result else ''


def contextual_reading(state, candidate=None):
    """Proactive story owns its note scope; durable fiction remains current.

    Foreground behavior is unchanged. Source indices are backend links, never
    a keyword filter, another model judgement or a reason to delete old notes.
    """
    items=current_items(state)
    if candidate is None:return reading(state), {'scope':'foreground','included':len(items),'omitted':0}
    indices=set(candidate.get('source_indices',[]))
    source_index=candidate.get('source_index')
    if isinstance(source_index,int) and 0<=source_index<len(state['history']):
        from .context_plan import dialogue_groups
        indices.update(r['_index'] for g in dialogue_groups(state['history'])
                       if any(r['_index']==source_index for r in g) for r in g)
    selected=[item for item in items if item['kind']=='fiction' or
              any(source['index'] in indices for source in item['sources'])]
    return reading(state,selected), {'scope':'selected_proactive_story','included':len(selected),
        'omitted':len(items)-len(selected),'selected_source_indices':sorted(indices),
        'reason':'只帶選中故事的有來源筆記與持續有效的虛構設定；不帶無關舊互動。'}


def _new(state,working,u,s,pending=''):
    duplicate=next((i for i in current_items(state) if all(i[k]==u[k] for k in ('kind','subject','text','phase'))
                    and i.get('pending_replaces','')==pending),None)
    if duplicate:return duplicate
    identity=hashlib.sha256((s['key']+s['digest']+u['kind']+u['subject']+u['text']+pending).encode()).hexdigest()[:16]
    existing=next((i for i in working['items'] if i['id']==identity),None)
    if existing:return existing
    item={'id':identity,'kind':u['kind'],'subject':u['subject'],'text':u['text'],
          'phase':u['phase'],'status':'reported','sources':[deepcopy(s)],'conflicts':[],
          'pending_replaces':pending,'closed_by':None,'text_format':'canonical-v2'}
    working['items'].append(item)
    return item


def update(state,message,updates,trace=None):
    working=ensure(state)
    try:index=next(i for i,m in enumerate(state['history']) if m is message)
    except StopIteration:return  # Photo/tool requests are not persistent working memory.
    src=source(message,index);token=src['key']+':'+src['digest']
    if token in working['applied']:return
    accepted=[];rejected=[]
    for u in updates[:4] if isinstance(updates,list) else []:
        if not isinstance(u,dict):rejected.append('格式');continue
        op=u.get('op');kind=u.get('kind');phase=u.get('phase');text=u.get('text','');quote=u.get('evidence','')
        if (op not in {'add','confirm','replace','close'} or kind not in {'situation','fiction','open_loop','interaction'}
            or u.get('subject') not in {'user','character','shared'} or phase not in {'proposed','ongoing','completed','hypothetical','negated','unknown'}
            or not isinstance(text,str) or not 0<len(text)<=120 or not isinstance(quote,str)
            or not quote or len(quote)>300 or quote not in message['content']
            or not any(role in text for role in ('使用者','角色','雙方')) or re.search(r'(?<!自)(?<!忘)我|你|妳',re.sub(r'《[^》]*》','',text))):
            rejected.append('逐字證據／明確人物正文／型別／長度');continue
        evidence=dict(src,quote=quote,statement=text)
        target=next((i for i in current_items(state) if i['id']==u.get('ref')),None)
        if op=='add':
            if u.get('ref'):rejected.append('新增不可指定舊ref');continue
            item=_new(state,working,u,evidence)
        elif target is None:
            rejected.append('引用不存在／已失效');continue
        elif (u['subject']!=target['subject'] and op!='replace') or kind!=target['kind']:
            rejected.append('引用人物／類型不符');continue
        elif op=='confirm':
            if phase not in {'ongoing','completed'} or target['phase'] in {'hypothetical','negated','unknown'} or src['role']==target['sources'][0]['role']:
                rejected.append('缺少另一方實際承接');continue
            if not any(s['key']==evidence['key'] and s['digest']==evidence['digest'] for s in target['sources']):target['sources'].append(evidence)
            target['status']='shared'
            if target['kind']!='open_loop':target['phase']=phase
            item=target
            prior=next((i for i in working['items'] if i['id']==item.get('pending_replaces') and i['status']!='closed'),None)
            if prior:prior.update(status='closed',closed_by=evidence)
            if item.get('pending_close'):item.update(status='closed',closed_by=evidence)
        elif op=='replace':
            if phase in {'proposed','hypothetical','unknown'}:
                rejected.append('未成立的更正');continue
            owns=u['subject']==src['role'] or (target['status']=='reported' and target['sources'][0]['role']==src['role'])
            if phase=='negated' and src['role']=='character' and target['status'] in {'shared','corrected'} and not owns:
                target['conflicts'].append(evidence);item=target
            else:
                # Cross-person/shared changes remain proposals until the other participant adopts them.
                item=_new(state,working,u,evidence,'' if owns else target['id'])
                if owns:
                    target.update(status='closed',closed_by=evidence);item.update(status='corrected',supersedes=target['id'])
        else:
            if phase!='completed':rejected.append('未確認結束');continue
            owns=u['subject']==src['role'] or (target['status']=='reported' and target['sources'][0]['role']==src['role'])
            if owns or kind=='open_loop':
                target.update(status='closed',closed_by=evidence);item=target
            else:
                item=_new(state,working,u,evidence,target['id']);item['pending_close']=True
        if kind=='open_loop' and not item.get('formal_ref'):
            formal_ref=formal_reference(state,message,quote)
            if formal_ref:item['formal_ref']=formal_ref
        accepted.append({'op':op,'ref':item['id'],'text':text,'status':item['status'],'current_text':item['text'],
            'conflict_recorded':evidence in item.get('conflicts',[])})
    working['applied'][token]=True
    working['revision']+=1
    if trace:trace.add(4,'working.updated',{}, {'revision':working['revision'],'accepted':accepted,'rejected':rejected,
        'active':len(current_items(state))},'同次Observer提案；逐字來源、人物及雙方承接檢查。不更新地點、衣著或政策。')


def bootstrap(state,trace=None):
    if 'working' in state.get('story_memory',{}):
        ensure(state);return
    working=ensure(state)
    from .story_events import claims
    # Legacy quotes remain available to the existing history/bridge, never silently
    # reinterpreted as a normalized present-tense statement or shared confirmation.
    working['legacy_claims']=[{'speaker':'user' if m['role']=='user' else 'character',**deepcopy(c)}
        for m in state.get('history',[])[-16:] for c in claims(m)]
