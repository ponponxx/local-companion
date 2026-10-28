"""Stage-local context allocation. Exact tokenizer preflight; source data stays intact."""
from copy import deepcopy
import json
import time
from .services import ServiceError, ContextCapacityError

from .config import CONTEXT_CAPACITY, CONTEXT_SAFETY

CAPACITY = CONTEXT_CAPACITY
SAFETY = CONTEXT_SAFETY


def compact_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def dialogue_groups(history):
    """Keep user/assistant turns together, including an unfinished latest user turn."""
    groups=[]
    for index,entry in enumerate(history):
        if entry.get('_memory_hidden'):continue
        item=dict(entry,_index=index)
        background = str(entry.get('turn_id','')).startswith('background:')
        previous_background = bool(groups and str(groups[-1][0].get('turn_id','')).startswith('background:'))
        separate_background = groups and (background or previous_background) and entry.get('turn_id') != groups[-1][0].get('turn_id')
        if entry['role']=='user' or not groups or separate_background:groups.append([])
        groups[-1].append(item)
    return groups


def protected_dialogue_groups(groups, protect):
    """Explicit background sends are separate units, never an unlimited reply.

    Preserve the normal tail, latest completed human exchange and the most
    recent observed casual message after it. Template notices retain their own
    latest tail unit; old backgrounds remain optional/retrievable, not deleted.
    Unknown legacy messages keep their original grouping.
    """
    required=set(range(max(0,len(groups)-protect),len(groups))) if protect else set()
    background=lambda g: str(g[0].get('turn_id','')).startswith('background:')
    if protect and any(background(g) for g in groups):
        foreground=next((i for i in range(len(groups)-1,-1,-1)
            if not background(groups[i]) and any(r['role']=='user' for r in groups[i])
            and any(r['role']=='assistant' for r in groups[i])),None)
        if foreground is not None:required.add(foreground)
        from .story_events import digest
        casual=next((i for i in range(len(groups)-1,foreground if foreground is not None else -1,-1)
            if background(groups[i]) and any(
                r.get('background_kind')=='casual' or ((r.get('_story',{}).get('available') or r.get('_story',{}).get('act','unknown')!='unknown') and
                r.get('_story',{}).get('digest')==digest(r)) for r in groups[i])),None)
        if casual is not None:required.add(casual)
    return ([g for i,g in enumerate(groups) if i in required],
            [g for i,g in enumerate(groups) if i not in required])


def continuity_entries(state, before):
    """Sourced recent archive recap, separate from long-term relevance search.

    This is a projection of existing validated notes, not a new model inference.
    Never reframe a proposal as a completed fact; unknown raw fallback stays unknown.
    """
    grouped={}
    for n in state.get('memory',{}).get('notes',[]):
        index=n['source_index']
        if index>=before:continue
        grouped.setdefault(index,[]).append(n)
    # Raw turns omitted from the selected window also need a sourced bridge,
    # even when the archival note cursor has not reached them yet.
    for index,h in enumerate(state['history'][:before]):
        if index not in grouped:
            grouped[index]=[{'actor':'user' if h['role']=='user' else 'character',
                             'at':h.get('at',''),'stage':'uncertain','text':h['content'],
                             'context':h.get('context',{})}]
    result=[]
    for index in sorted(grouped)[-4:]:
        notes=grouped[index]
        text='；'.join(f'[{n["stage"]}] {n["text"]}' for n in notes)
        result.append({'source_index':index,'actor':notes[0]['actor'],'at':notes[0].get('at',''),
                       'text':text[:320], 'excerpt':len(text)>320,
                       'context':deepcopy(notes[0].get('context',{}))})
    if not result and not grouped and not state.get('_legacy_raw_complete'):
        legacy=state.get('memory',{}).get('previous_summary') or state.get('legacy_summary') or state.get('summary','')
        if legacy:result=[{'source_index':-1,'actor':'unknown','at':'','text':'[舊摘要，可錯] '+legacy[:320],'excerpt':len(legacy)>320}]
    return result


def memory_key(item):
    if item.get('kind')=='scene_event':return ('scene',item['id'])
    if item.get('kind')=='manual' or item.get('source_index',-1)<0:return ('manual',item['id'])
    return ('dialogue',item['source_index'])


class ContextPlanner:
    def __init__(self,llm):
        self.llm=llm
        self._counts=None

    async def count(self,messages):
        counter=getattr(self.llm,'count_context',None)
        if counter is None:return None  # Deterministic unit-test providers have no tokenizer.
        key=compact_json(messages)
        if self._counts is not None and key in self._counts:
            self._count_hits+=1
            return self._counts[key]
        count=await counter(messages)
        if count is None:raise ServiceError('本地模型不提供上下文計數；無法確認資料完整性，已停止。')
        if self._counts is not None:self._counts[key]=count
        return count

    async def plan(self,stage,state,trace,build,output_tokens,history,protect=1,archive=True,memory=True,extra_groups=2,
                   continuity=None, story_budget=None, recent_first=False, packet_budget=None, shrink_packet=None):
        started=time.monotonic();self._counts={};self._count_hits=0
        try:
            return await self._plan(stage,state,trace,build,output_tokens,history,protect,archive,memory,extra_groups,continuity,story_budget,recent_first,packet_budget,shrink_packet)
        finally:
            trace.add(1,'pipeline.context',{'stage':stage},
                      {'tokenizer_calls':len(self._counts),'cache_hits':self._count_hits},
                      '相同messages僅在本次組裝重用計數；送模型前仍獨立驗證容量。',seconds=round(time.monotonic()-started,3))
            self._counts=None

    async def _plan(self,stage,state,trace,build,output_tokens,history,protect=1,archive=True,memory=True,extra_groups=2,
                    continuity=None, story_budget=None, recent_first=False, packet_budget=None, shrink_packet=None):
        writer_stage=stage in {'writer','proactive_writer'}
        groups=dialogue_groups(history)
        selected,remaining=protected_dialogue_groups(groups,protect)
        required=len(selected)
        limit=CAPACITY-output_tokens-SAFETY
        pool=deepcopy(state.get('memory',{}).get('selection',{}).get('items',[])) if memory else []
        pool.sort(key=lambda n:({'important':0,'relevant':1,'recent':2}.get(n.get('reason'),3),
                                -n.get('lexical_score',0)-n.get('semantic_score',0)))
        chosen=[];recap=[];seen=set()
        def rows():return [r for g in selected for r in g]
        def available_summary():
            recent=rows();before=min((r['_index'] for r in recent),default=len(state['history']))
            return (continuity or continuity_entries)(state,before) if archive else []
        def make():return build(rows(),chosen,recap)
        last_count=None
        async def fits():
            nonlocal last_count
            while True:
                messages=make()
                packet_size=None
                if packet_budget is not None:
                    system=messages[0]['content'];start=system.find('{')
                    if start>=0:
                        packet=json.loads(system[start:]).get('story_context','')
                        if packet:packet_size=await self.count([{'role':'user','content':packet}])
                last_count=await self.count(messages)
                packet_over=packet_size is not None and packet_size>packet_budget
                if (packet_over or last_count is not None and last_count>limit) and shrink_packet and shrink_packet():continue
                return not packet_over and (last_count is None or last_count<=limit)
        # All mandatory components must be counted together. Otherwise the
        # Observer borrows output space using a smaller floor, then fails again
        # when its equally mandatory continuity recap is added.
        summaries=available_summary()
        mandatory_recap=bool(summaries and not recent_first and continuity is None)
        if mandatory_recap:recap=[summaries[-1]]
        if not await fits():
            trace.add(1,'context.capacity_failed',{'stage':stage},
                      {'input_limit':limit,'input_tokens':last_count,'protected_messages':len(rows()),
                       **({'component':'continuity_summary'} if mandatory_recap else {})},
                      '必要事實、完整保護往返與必要摘要合計超量；以完整必要底座配置後備，不清空來源。')
            from .diagnostics import event
            event('context.rejected_request',{'stage':stage,'messages':make(),'input_tokens':last_count,'output_tokens':output_tokens,'input_limit':limit})
            raise ContextCapacityError(stage,last_count,output_tokens,limit)
        if summaries and not recent_first and not mandatory_recap:
            recap=[summaries[-1]]
            if not await fits():recap=[]  # Optional StoryCore unit, removed whole.
        # Add complete older exchanges first; rebuilding the recap prevents overlap.
        deferred=remaining if extra_groups is None else remaining[-extra_groups:] if extra_groups else []
        # Native chat messages mean an actual contiguous conversation, never
        # search hits. Older sources have exactly one route: StoryCore.packet.
        if writer_stage:
            first=min((groups.index(g) for g in selected),default=len(groups))
            count=2 if extra_groups is None else extra_groups
            deferred=groups[max(0,first-count):first]
            trace.add(4,'context.channel_boundary',{'stage':stage},
                      {'protected_groups':required,'optional_adjacent_groups':len(deferred)},
                      '原生對話只取連續尾段；檢索命中不改變近期對話，歷史只經故事閱讀版提供。')
        candidates=deferred
        for group in reversed(candidates):
            old_selected=selected;old_recap=recap
            selected=sorted([group]+selected,key=lambda g:g[0]['_index'])
            summary=available_summary();recap=[] if recent_first else summary[-1:]
            if not await fits():selected=old_selected;recap=old_recap;break
        for entry in reversed([] if writer_stage else (available_summary() if recent_first else available_summary()[:-1])):
            candidate=[entry]+recap;old=recap;recap=candidate
            if not await fits():recap=old;break
        occupied={('dialogue',r['_index']) for r in rows()}|{
            ('dialogue',index) for r in recap for index in r.get('source_indices',[r['source_index']])}
        content_seen={('user' if r['role']=='user' else 'character',' '.join(r['content'].split())) for r in rows()}
        content_seen.update((r['actor'],' '.join(r['text'].split())) for r in recap)
        duplicate_count=0;omitted_count=0
        for item in pool:
            key=memory_key(item)
            if key in seen or key in occupied:duplicate_count+=1;continue
            content_key=(item['actor'],' '.join(item['text'].split()))
            if stage in {'writer','proactive_writer'} and content_key in content_seen:
                duplicate_count+=1;continue
            content_seen.add(content_key)
            seen.add(key);chosen.append(item)
            if not await fits():chosen.pop();omitted_count+=1
        # Bound the story contribution with the actual tokenizer, independently
        # from the total request limit. Remove complete units, never fragments.
        story_tokens=None
        if story_budget is not None:
            kept_chosen,kept_recap=chosen,recap
            chosen=[];recap=[]
            base_count=await self.count(make())
            chosen,recap=kept_chosen,kept_recap
            while chosen or recap:
                total=await self.count(make())
                if total is None or base_count is None or total-base_count<=story_budget:break
                if chosen:chosen.pop();omitted_count+=1
                else:recap.pop(0)
            total=await self.count(make())
            story_tokens=None if total is None or base_count is None else total-base_count
        messages=make();count=await self.count(messages)
        trace.add(1,'context.selected_sources',{'stage':stage},
                  {'recent_dialogue':deepcopy(rows()),'memory_items':deepcopy(chosen),'continuity':deepcopy(recap)},
                  '本次編輯核實際選定的來源；模型最終Prompt仍以model.request為準。')
        trace.add(1,'context.prepared',{'stage':stage},
                  {'input_tokens':count,'output_tokens':output_tokens,'capacity':CAPACITY,'safety_tokens':SAFETY,
                   'recent_first':recent_first,'recent_messages':len(rows()),'recent_groups':len(selected),'protected_groups':required,
                   'continuity_sources':[r['source_index'] for r in recap],
                   'memory_items':len(chosen),'duplicates_omitted':duplicate_count,'memory_omitted':omitted_count,
                   'characters':sum(len(m['content']) for m in messages),'tokenizer_verified':count is not None},
                  '各階段獨立以實際token計數分配；保護完整往返，餘量放摘要及去重記憶，不修改來源。')
        if recent_first and count is not None:
            try:
                system=messages[0]['content'];start=system.find('{')
                contract=json.loads(system[start:]) if start>=0 else None
                if isinstance(contract,dict):
                    pinned=[m for m in messages if m['content'].startswith('【系統提供的當前互動記憶，')]
                    minimal=[messages[0]]+pinned+[{'role':'user','content':''}]
                    sections={'total':count,'recent_messages':count-await self.count(minimal),'current_interaction':0,'story_context':0}
                    if pinned:
                        sections['current_interaction']=count-await self.count([m for m in messages if m is not pinned[0]])
                    if 'story_context' in contract:
                        reduced=dict(contract);reduced.pop('story_context')
                        changed=[dict(messages[0],content=system[:start]+compact_json(reduced))]+messages[1:]
                        sections['story_context']=count-await self.count(changed)
                    trace.add(4,'context.sections',{},sections,'各區為移除該區的實測token差，含模板效應，不能直接相加。')
            except Exception as exc:
                trace.add(4,'context.sections_unavailable',{}, {'error':type(exc).__name__},'可選區段計數未完成；完整messages已通過容量預檢，不影響送出。')
        if continuity is not None:
            trace.add(4,'story.prepared',{},
                      {'version':'story-v0.3' if recent_first else 'story-v0.2','tokens':story_tokens,
                       'continuity_sources':[index for r in recap for index in r.get('source_indices',[r['source_index']])],
                       'memory_items':len(chosen),'uncovered_exchanges':len(available_summary())-len(recap),
                       'token_budget':story_budget,'archive_through':state.get('summary_through',0)},
                      '從完整來源往返編譯近期故事；未整理原文仍可使用，不額外呼叫模型。')
        return messages
