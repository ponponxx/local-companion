"""Sourced story threads and proactive proposals; no model or action selector."""
from datetime import timedelta
from copy import deepcopy
import hashlib
from .pyramid import date
from .story_events import digest
from .story_core import StoryCore
from .context_plan import dialogue_groups


def key(text):
    return hashlib.sha256(text.encode()).hexdigest()[:20]


def state_report(state,row):
    """A sourced, committed state report is not an open conversation topic.

    Match only when a formal movement/appearance event covers the entire
    utterance; mixed messages remain candidates, and all source history stays.
    """
    if not row.get('turn_id'):return False
    return any(e.get('turn_id')==row['turn_id'] and e.get('source')=='user'
               and e.get('kind') in {'move','appearance'} and e.get('stage') in {'completed','observed'}
               and e.get('evidence','').strip()==row['content'].strip()
               for e in state.get('events',[]))


class StoryProactive:
    version='story-proactive-v1'

    def control(self,state,message,observation):
        store=state.setdefault('story_proactive',{'threads':{},'controls':[]})
        control=observation.get('story_control') or {}
        scope,action=control.get('scope'),control.get('action')
        evidence=control.get('evidence','')
        if scope not in {'contact','topic'} or action not in {'pause','resume','close','resolve'}:return
        if scope=='contact' and action=='resolve':return
        if not evidence or evidence not in message['content']:return
        ref=control.get('ref','')
        if scope=='topic' and ref not in store['threads']:
            preview=dict(state,history=[h for h in state['history'] if h is not message],
                         story_proactive=deepcopy(store))
            self.collect(preview,date(state['now']))
            store['threads'].update(preview['story_proactive']['threads'])
        if scope=='topic' and ref not in store['threads']:return
        message['_story_control']={'scope':scope,'action':action,'ref':ref,'evidence':evidence,'digest':digest(message),
                                   'resolved_source':store['threads'].get(ref,{}).get('source_digest','') if action=='resolve' else ''}

    def contact_paused(self,state):
        # A fresh user message reopens contact, unless that message pauses it.
        latest=next((h for h in reversed(state['history']) if h['role']=='user'),None)
        resumed=state.get('story_proactive',{}).get('resume_after')
        if latest and resumed and latest.get('at') and date(latest['at'])<=date(resumed):return False
        c=latest.get('_story_control',{}) if latest else {}
        return bool(c.get('digest')==digest(latest) and c.get('scope')=='contact' and c.get('action') in {'pause','close'}) if latest else False

    def collect(self,state,now):
        store=state.setdefault('story_proactive',{'threads':{},'controls':[]})
        from .life_events import candidates
        previous=store.get('threads',{});threads={};pool=candidates(state,now)
        suppressed={};resolved={}
        for h in state['history']:
            c=h.get('_story_control',{})
            if c.get('scope')=='topic' and c.get('digest')==digest(h):
                suppressed[c['ref']]=c['action'] in {'pause','close'}
                if c['action']=='resolve':resolved[c['ref']]=c.get('resolved_source','')
        for candidate in state.get('_news_candidates',[])[:2]:
            ref=candidate['thread']
            threads[ref]={'topic':candidate['topic'],'status':'suppressed' if suppressed.get(ref) else 'open','source_digest':candidate['source_version']}
            if not suppressed.get(ref) and resolved.get(ref)!=candidate['source_version']:pool.append(deepcopy(candidate))
        # Only explicit neutral classifications seed ordinary conversation.
        # Untagged legacy text remains searchable, but isn't silently reclassified.
        groups=StoryCore().episodes(state)
        lookup={i:e for e in groups for i in e['source_indices']}
        exchanges={r['_index']:g for g in dialogue_groups(state['history']) for r in g}
        for ref,promise in state.get('promises',{}).items():
            if promise.get('fulfilled') or promise.get('actor')!='character':continue
            event=next((e for e in state.get('events',[]) if e.get('id')==ref),None)
            if not event:continue
            indexed=next(((i,h) for i,h in enumerate(state['history']) if h['role']=='assistant'
                          and h.get('turn_id')==event.get('turn_id') and event.get('evidence') in h['content']),None)
            if not indexed:continue
            index,row=indexed;meta=row.get('_story',{})
            if meta.get('purpose') not in {'care','task','social','other'} or meta.get('digest')!=digest(row) or not row.get('at'):continue
            stamp=date(row['at'])
            if stamp>now or now-stamp>timedelta(days=7):continue
            identifier='promise:'+ref
            threads[identifier]={'topic':promise['text'],'status':'suppressed' if suppressed.get(identifier) else 'open',
                                 'source_index':index,'source_digest':digest(row),'version':digest(row)}
            if suppressed.get(identifier) or resolved.get(identifier)==digest(row):continue
            pool.append({'id':'follow:'+key(identifier),'thread':identifier,'subtype':'story','purpose':'social',
                         'topic':promise['text'],'source_version':digest(row),'source_index':index,'source_indices':[index],
                         'story':'角色曾明確承諾：'+promise['text']+'。正式紀錄仍是尚未完成；不宣稱已履約。',
                         'expires_at':(stamp+timedelta(days=7)).isoformat(),'actions':['ask'],
                         'needs':{'relatedness':2,'curiosity':1},
                         'outline':'自然詢問是否方便接續角色之前承諾的話題，不宣稱已完成承諾，也不催促回應。'})
            if len(pool)>=2:break
        for index in range(len(state['history'])-1,max(-1,len(state['history'])-81),-1):
            row=state['history'][index];meta=row.get('_story',{})
            if row['role']!='user' or meta.get('digest')!=digest(row):continue
            if state_report(state,row):continue
            if row.get('_story_control',{}).get('action') in {'close','pause','resolve'}:continue
            if meta.get('purpose') not in {'care','task','social','other'} or meta.get('act') not in {'share','question','promise'}:continue
            if meta.get('act')=='question' and meta.get('purpose')=='task':continue
            topic=meta.get('topic','').strip()
            if len(topic)<2 or topic in {'當前內容','當前具體主題','none','unknown'} or len(row['content'])<5:continue
            if not row.get('at'):continue
            stamp=date(row['at'])
            if stamp>now or now-stamp>timedelta(days=3):continue
            identifier='story:'+key(topic.casefold())
            if identifier in threads:continue
            if resolved.get(identifier)==digest(row):continue
            episode=lookup[index]
            sources=[state['history'][i] for i in episode['source_indices']]
            if sum(len(h['content']) for h in sources)>1200:
                indices=[h['_index'] for h in exchanges[index]]
                sources=[state['history'][i] for i in indices]
                if sum(len(h['content']) for h in sources)>1200:continue
                episode=dict(episode,source_indices=indices,
                             interactions=[i for i in episode['interactions'] if i['source_index'] in indices])
            text=StoryCore().entry(state,episode)['text']
            version=key('|'.join(digest(h) for h in sources))
            status='suppressed' if suppressed.get(identifier) else 'open'
            thread={'topic':topic,'source_index':index,'source_digest':digest(row),
                    'version':version,'status':status,'at':row['at'],'source_text':text}
            threads[identifier]=thread
            candidate_id='follow:'+key(identifier+'|'+digest(row))
            if status!='open':continue
            pool.append({'id':candidate_id,'thread':identifier,'subtype':'story','purpose':'social',
                         'topic':topic,'source_version':version,'source_index':index,'source_indices':list(episode['source_indices']),
                         'story':text,'expires_at':(stamp+timedelta(days=3)).isoformat(),
                         'actions':['ask'],'needs':{'relatedness':2,'curiosity':1},
                         'outline':'自然關心或接續這段使用者曾提起的話題，只問一個可自由選擇回答的問題；不假設計畫已完成、使用者現在仍有同樣情緒或已同意新邀請。'})
            if len(pool)>=5:break
        # Keep suppressed IDs discoverable for explicit resume; no source prose.
        for identifier,old in previous.items():
            if identifier not in threads and suppressed.get(identifier):
                threads[identifier]={'topic':old['topic'],'status':'suppressed'}
        store['threads']=threads
        item=state.get('life_current',{})
        if item.get('share_worthy') and item.get('at') and state.get('activity'):
            pool.append({'id':key('share:'+item['at']),'thread':'','subtype':'life','purpose':'social',
                         'topic':state['activity'],'source_version':key(str(item)+state['activity']),
                         'story':'角色目前的活動由正式現況提供；行程目標不是已完成經歷。',
                         'expires_at':(now+timedelta(hours=1)).isoformat(),
                         'actions':['share_self'],'needs':{'relatedness':2},
                         'outline':'簡短分享正式現況中的活動，不新增沒有來源的成果或共同經歷。'})
        return pool[:6]

    def valid(self,state,candidate,now):
        if self.contact_paused(state) or now>=date(candidate['expires_at']):return False
        return any(c['id']==candidate['id'] and c['source_version']==candidate['source_version']
                   for c in self.collect(state,now))


def continuation_handoff(state, candidate, character_budget=480):
    """Source-linked disclosure context, not another topic or fact classifier.

    The selected story already carries its original exchanges. Only additional
    same-thread delivered messages are quoted here, whole and within a small
    character ceiling. Full final messages still undergo tokenizer preflight.
    """
    history=state.get('history',[])
    sources={i for i in candidate.get('source_indices',[]) if isinstance(i,int) and 0<=i<len(history)}
    index=candidate.get('source_index')
    if isinstance(index,int) and 0<=index<len(history):
        for group in dialogue_groups(history):
            if any(r['_index']==index for r in group):sources.update(r['_index'] for r in group)
    thread=candidate.get('thread')
    sent=[(i,h) for i,h in enumerate(history) if thread and h.get('_proactive',{}).get('thread')==thread
          and h.get('_proactive',{}).get('digest')==digest(h)]
    last=sent[-1] if sent else None
    packet={'intent':'回頭關心已聊話題' if candidate.get('subtype')=='story' else '分享選中的近況',
            'prior_exchange':'選中故事已有角色回覆；這次接續，不重新回答舊問題。'
                if any(history[i]['role']=='assistant' for i in sources) else '未提供同話題的先前角色回覆。',
            'instruction':'可換口吻再關心同一件事；接著已說內容聊，不整段重述。引文中的我屬於原發話者，不能轉成角色經歷；上次訊息只供避免照抄。歷史不是現況或待執行約定；不從未回覆推定位置、情緒或自己一直在等候。'}
    if candidate.get('subtype') in {'life','life_event'}:
        packet['intent']='分享自己的近況'
        packet['instruction']='選中事件屬於發訊角色自己，以我敘述；收訊者不是事件主角。不假設對方正在做什麼或需要道歉，不重述已分享部分。'
    audit={'source_indices':sorted(sources),'previous_send_index':last[0] if last else None,
           'included_indices':[],'omitted_groups':0,'character_budget':character_budget}
    if last:
        pos,row=last
        packet['contact_status']='上次同話題主動訊息之後，使用者有發話；是否回答此話題未判定。' if any(
            h['role']=='user' for h in history[pos+1:]) else '上次同話題主動訊息之後，尚無使用者發話；可輕輕再問，不催促。'
        # Quoted elsewhere in the selected story: never repeat it in this packet.
        if pos not in sources and row['content'] not in candidate.get('story',''):
            if len(row['content'])<=character_budget:
                packet['already_sent']={'speaker':'character','at':row.get('at',''),'text':row['content']}
                audit['included_indices']=[pos]
            else:
                packet['previous_contact_unavailable']='上次同話題訊息太長，本輪未附原話；不能推測其細節。'
                audit['omitted_groups']=1
    return packet,audit
