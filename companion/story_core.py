"""Source-backed episodes and Writer reading projection.

Stores derived annotations only; never changes world facts or response decisions.
Full narrative synthesis and proactive proposals remain separate future work.
"""
from .context_plan import dialogue_groups
from .story_events import annotate, claims, describe, digest
from . import scene_topic_index as indexer


class StoryCore:
    version = 'story-v0.3'

    def record(self, state, message, observation, trace=None, working=True):
        from .memory_sources import source_ref
        message.setdefault('source_ref',source_ref(message,len(state.get('history',[]))-1))
        events,rejected=annotate(message,observation)
        state.setdefault('story_memory',{}).update(version=self.version,episodes=self.episodes(state))
        if indexer.enabled(state):
            index=indexer.build(state)
            state['story_memory']['scene_topic_index']=index
            if trace:trace.add(4,'story.index',{}, {'visits':len(index['visits']),'threads':len(index['threads']),
                'sources':len(index['messages']),'unresolved_visits':sum(not m['visit'] for m in index['messages'].values())},
                '從確認場景與有來源的話題重建連結；不更新世界狀態，不新增模型呼叫。')
        if working:
            from .working_memory import update
            update(state,message,observation.get('working_updates',[]),trace)
        if trace:
            trace.add(4,'story.events',{}, {'events':events,'rejected':rejected,
                                         'available':message['_story']['available']},
                      '同一次觀察結果記為有來源的說法；不是正式物理狀態，也不新增模型呼叫。')

    def episodes(self, state, before=None):
        """Conservative incremental-ready index: explicit topic + stable setting.

        Unknown topics stay separate. Each unit retains complete exchanges and
        can always be rebuilt after source edits/deletion; no summary recursion.
        """
        groups=dialogue_groups(state['history'])
        index=indexer.build(state) if indexer.enabled(state) else None
        result=[]
        origins={}
        for event in state.get('events',[]):
            if event.get('id') and event.get('evidence'):
                origins.setdefault((event.get('turn_id'),event.get('source')),[]).append(event)
        for group in groups:
            if before is not None and group[-1]['_index']>=before:continue
            first=group[0];meta=first.get('_story',{})
            topic=meta.get('topic','') if meta.get('digest')==digest(first) else ''
            if topic in {'當前內容','當前具體主題','none','unknown'}:topic=''
            scene=first.get('context',{}).get('scene',{})
            location=(scene.get('character_location') or scene.get('location') or '')
            indices=[r['_index'] for r in group]
            refs={field:sorted({index['messages'][i][key] for i in indices if index['messages'][i][key]}) for field,key in (('visit_refs','visit'),('thread_refs','thread'))} if index else {}
            merge=bool(result and topic and location and result[-1]['topic']==topic
                       and result[-1]['location']==location and result[-1]['exchanges']<4
                       and all(result[-1].get(k)==v for k,v in refs.items())
                       and result[-1]['source_indices'][-1]+1==indices[0]
                       and str(result[-1]['until'])[:10]==str(first.get('at',''))[:10])
            if not merge:
                result.append({'topic':topic,'location':location,'at':first.get('at',''),
                               'until':'','source_indices':[],'events':[],'interactions':[],
                               'exchanges':0,**refs})
            episode=result[-1]
            episode['source_indices'].extend(indices)
            episode['until']=group[-1].get('at','')
            episode['exchanges']+=1
            for row in group:
                episode['events'].extend(dict(e,source_index=row['_index']) for e in claims(row))
        for episode in result:
            rows=[(index,state['history'][index]) for index in episode['source_indices']]
            origin_ids={event['id']:index for index,row in rows if row.get('turn_id')
                        for event in origins.get((row['turn_id'],'user' if row['role']=='user' else 'assistant'),[])
                        if event['evidence'] in row['content']}
            for identifier in sorted(origin_ids):
                invitation=state.get('invitations',{}).get(identifier)
                if invitation:episode['interactions'].append(dict(invitation,ref=identifier,source_index=origin_ids[identifier]))
        return result

    def continuity(self, state, before):
        # Never include the user half of an exchange whose reply is in recent.
        return [self.entry(state,episode) for episode in self.episodes(state,before)[-2:]]

    def entry(self,state,episode):
        lines=[]
        indexed=indexer.enabled(state);previous=None
        for index in episode['source_indices']:
            row=state['history'][index]
            scene=row.get('context',{}).get('scene',{})
            setting=(indexer.location(scene),scene.get('user_location',''))
            if indexed and setting[0] and setting!=previous:
                lines.append('聊天背景：角色在'+setting[0]+'；使用者'+('在'+setting[1] if setting[1] else '位置未知')+'。此為發話情境，不代表所談事件在此發生。')
                previous=setting
            if indexed:
                from .writer_projection import model_metadata
                from .memory_metadata import reported_time_refs
                at=model_metadata({'at':row.get('at','')}).get('at') or '時間未記錄'
                speaker='使用者對角色說' if row['role']=='user' else '角色回覆使用者'
                refs=reported_time_refs(row)
                if refs:lines.append('本則原話時間用語：'+'；'.join(r['word']+'指'+r['date'] for r in refs)+'。')
                lines.append(at+'，'+speaker+'：「'+row['content']+'」')
            else:lines.append(describe(row))
        if episode['topic']:lines.insert(0,'這段話題：'+episode['topic'])
        from .story_events import ACTORS
        statuses={'proposed':'僅提出，未同意','accepted':'已同意，未開始',
                  'started':'已開始，未完成','completed':'已完成',
                  'rejected':'已拒絕','cancelled':'已取消'}
        for interaction in episode['interactions']:
            lines.append('這項邀請的正式進度：'+ACTORS.get(interaction.get('actor'),'人物未確認')+
                         '邀請'+ACTORS.get(interaction.get('target'),'人物未確認')+'「'+interaction['action']+'」；'+
                         statuses.get(interaction.get('status'),'狀態未知')+
                         ('；條件：'+interaction['conditions'] if interaction.get('conditions') else '')+'。')
        return {'source_index':episode['source_indices'][0],'source_indices':episode['source_indices'],
                'actor':'transcript','at':state['history'][episode['source_indices'][0]].get('at','') if indexed else '',
                'context':{'scene':{'character_location':episode['location']}} if indexed and episode['location'] else {},'text':'\n'.join(lines)}

    def expand_memory(self,state,items,occupied):
        """A retrieved sentence locates an episode, not just a small excerpt."""
        if indexer.enabled(state):return self.expand_indexed(state,items,occupied)
        episodes=self.episodes(state)
        lookup={index:e for e in episodes for index in e['source_indices']}
        seen=set();result=[]
        groups=dialogue_groups(state['history'])
        allowed={r['_index'] for g in groups if not any(r['_index'] in occupied for r in g) for r in g}
        for item in items:
            episode=lookup.get(item.get('source_index')) if item.get('kind') not in {'manual','scene_event'} else None
            if episode is None:result.append(item);continue
            indices=[i for i in episode['source_indices'] if i in allowed]
            key=tuple(indices)
            if not indices or key in seen:continue
            seen.add(key)
            fragment=dict(episode,source_indices=indices,
                          interactions=[i for i in episode['interactions'] if i['source_index'] in indices])
            entry=self.entry(state,fragment)
            result.append(dict(item,actor='unknown',at='',stage='episode',text=entry['text'],context={},excerpt=False))
        return result

    def expand_indexed(self,state,items,occupied):
        episodes=self.episodes(state);lookup={i:e for e in episodes for i in e['source_indices']}
        allowed={r['_index'] for g in dialogue_groups(state['history']) if not any(r['_index'] in occupied for r in g) for r in g}
        scene_query=state.get('memory',{}).get('selection',{}).get('scene_query',{})
        if scene_query.get('explicit'):allowed &= set(scene_query.get('sources',[]))
        result=[];seen=set()
        for item in items:
            if item.get('kind') in {'manual','scene_event','life_event'} or item.get('source_index') not in lookup:
                result.append(item);continue
            hit=lookup[item['source_index']];selected=[hit]
            if hit.get('thread_refs') and not scene_query.get('explicit'):
                related=[e for e in episodes if e is not hit and set(e.get('thread_refs',[])) & set(hit['thread_refs'])
                         and set(e['source_indices']) & allowed]
                if related:selected.append(min(related,key=lambda e:abs(e['source_indices'][0]-hit['source_indices'][0])))
            for episode in sorted(selected,key=lambda e:e['source_indices'][0]):
                indices=[i for i in episode['source_indices'] if i in allowed]
                if not indices or any(i in seen for i in indices):continue
                seen.update(indices)
                fragment=dict(episode,source_indices=indices,interactions=[i for i in episode['interactions'] if i['source_index'] in indices])
                entry=self.entry(state,fragment)
                result.append(dict(item,source_index=indices[0],source_indices=indices,actor='transcript',at=entry['at'],
                                   stage='episode',text=entry['text'],context=entry['context'],excerpt=False))
        return result

    async def query(self,state,text,search,trace=None,**kwargs):
        """Single query entry: short elliptical text gets a sourced topic hint."""
        from .memory_sources import readable
        view=readable(state);query=text
        if len(text.strip())<=16 and any(w in text for w in ('後來','然後','那個','那件','到了','繼續','剛才')):
            previous=[h for h in view.get('history',[]) if not h.get('_memory_hidden')][-2:]
            topics=[h.get('_story',{}).get('topic','') for h in previous]
            hint=' '.join(t for t in topics if t and t not in {'聊天','生活','工作','unknown'})
            if hint:query=text+' '+hint
        selected=await search.select(view,query,trace,**kwargs)
        state.setdefault('memory',{})['selection']=selected
        return selected

    def packet(self,state,decision,recent,items,recap):
        """One reading projection and a separate source audit, no new inference."""
        from .memory_sources import readable,source_ref,excluded
        from .working_memory import current_items
        from .pyramid_memory import tokens
        from . import life_events
        state=readable(state);units=[];occupied={r['_index'] for r in recent};seen=set(occupied)
        query=next((h['content'] for h in reversed(recent) if h['role']=='user'),'')
        q=set(tokens(query));source_audit=[];omitted=[];candidate=decision.get('story_candidate')
        candidate_sources=set((candidate or {}).get('source_indices',[]))
        if candidate and isinstance(candidate.get('source_index'),int):candidate_sources.add(candidate['source_index'])
        recent_sources=occupied|candidate_sources
        scope=decision.get('information_scope','unknown')
        episodic=scope in {'past_events','interaction'} or bool(candidate)
        for note in current_items(state):
            indices={s['index'] for s in note['sources']}
            relevant=bool(indices&recent_sources or note['kind']=='fiction' or (q & set(tokens(note['text']))))
            if candidate and not candidate_sources and note['kind']!='fiction':relevant=False
            if not relevant:omitted.append({'kind':'working','reason':'與當輪話題無來源連結'});continue
            # Native transcript supplies recent facts; notes add only durable status.
            from .working_memory import reading
            content=reading(state,[note])
            units.append({'section':'目前互動','text':content,'sources':sorted(indices),'kind':'working'})
        # Source-origin dedup applies across notes, episodes and summaries.
        life=life_events.current_story(state)
        items=[i for i in items if not (life and i.get('id')=='life-event:'+life['id'])]
        expanded=self.expand_memory(state,items,seen) if episodic else []
        if not episodic and (items or recap):omitted.append({'kind':'episode','reason':'本輪取材範圍不需過去經歷'})
        # Exact long replies can recur under different source IDs. Do not
        # repeat them as historical prose beside an identical native reply.
        duplicate_indices={i for i,h in enumerate(state['history']) if h['role']=='assistant' and len(h['content'])>=80
                           and any(r['role']=='assistant' and r['content']==h['content'] for r in recent)}
        for item in expanded+(list(recap) if episodic else []):
            if life and item.get('id','').startswith('life-event:'+life['id']):continue
            indices={i for i in item.get('source_indices',[item.get('source_index')]) if isinstance(i,int) and i>=0}
            if indices & duplicate_indices:
                omitted.append({'kind':item.get('kind','episode'),'reason':'長回文已由近期原話呈現'});continue
            if indices and indices&seen:omitted.append({'kind':item.get('kind','episode'),'reason':'來源已由近期對話或另一段落呈現'});continue
            if any(i<len(state['history']) and state['history'][i].get('_memory_hidden') for i in indices):continue
            text=item.get('text','').strip()
            if not text:continue
            # Never repeat identical text from different derived stores.
            if any(u['text']==text for u in units):continue
            if item.get('stage')!='episode':
                speaker={'user':'使用者','character':'角色'}.get(item.get('actor'),'來源未明')
                text=speaker+'先前的記錄：'+text
            units.append({'section':'相關往事','text':text,'sources':sorted(indices),'kind':item.get('kind','episode')});seen.update(indices)
        life=life_events.current_story(state)
        if life and scope not in {'general','unknown'} and not candidate and not decision.get('life_share'):
            text=life_events.current_reading(state)
            if text and life['beats'][-1]['text'] not in decision.get('reply_brief',{}).get('response_outline',''):
                units.append({'section':'角色生活','text':text,'sources':[],'kind':'life'})
        # Unprocessed output is available as dialogue, never as verified progress.
        if any(p.get('message_id') in {r.get('message_id') for r in recent} for p in state.get('_pending_responses',[])):
            units.insert(0,{'section':'尚待確認','text':'近期部分角色原話尚待事實分析；可據原話接話，但其中提議、移動或穿脫不能據此當成已完成。','sources':[],'kind':'pending'})
        if decision.get('information_scope')=='past_events':
            from .memory_reply import compile_memory_reply
            answer,audit=compile_memory_reply(state,query,recent,expanded,recap)
            for note in answer['missing_information']+answer['uncertainty']:
                units.append({'section':'缺失與不確定性','text':note,'sources':[],'kind':'uncertainty'})
        # Prefer durable context; related history is optional. All are whole units.
        for u in units:
            source_audit.append({'section':u['section'],'kind':u['kind'],'sources':[source_ref(state['history'][i],i) for i in u['sources'] if i<len(state['history'])]})
        return {'units':units,'audit':{'sources':source_audit,'omitted':omitted}}

    def render(self,packet,limit=None):
        units=packet['units'] if limit is None else packet['units'][:limit]
        groups={}
        for u in units:groups.setdefault(u['section'],[]).append(u['text'])
        return '\n\n'.join(title+'：\n'+'\n'.join(lines) for title,lines in groups.items())

    async def maintain(self,state,search,trace):
        """Warm only bounded missing vectors while idle; never blocks foreground."""
        if hasattr(search,'warm'):return await search.warm(state,trace,batch_size=24)
        return 0
