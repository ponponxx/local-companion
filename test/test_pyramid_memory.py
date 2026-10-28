from copy import deepcopy
import json
import unittest

from companion.pyramid import initial_state, DecisionTrace, TurnInput
from companion.pyramid_memory import add_batch, MemorySearch, ensure_memory, memory_context
from companion.pyramid_runner import PyramidRunner, writer_contract
from test_pyramid import FakeLLM, observation, event, STAMP


def history(texts):
    return [{'role':'user' if i%2==0 else 'assistant','content':t,'at':STAMP,'turn_id':str(i//2)} for i,t in enumerate(texts)]


class MemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_append_notes_preserves_old_details_raw_and_coverage(self):
        state=initial_state(STAMP)
        state['history']=history(['我喜歡茉莉花茶','好，記得了','我今天改喝烏龍茶','知道了'])
        note={'source_index':0,'actor':'user','text':'使用者說喜歡茉莉花茶','evidence':'喜歡茉莉花茶','stage':'statement'}
        add_batch(state,0,state['history'][:2],[note])
        first=deepcopy(state['memory']['notes'])
        add_batch(state,2,state['history'][2:],[])
        self.assertEqual(state['memory']['notes'][:2],first)
        self.assertEqual(state['summary_through'],4)
        self.assertEqual(len(state['history']),4)
        selection=await MemorySearch().select(state,'茉莉花茶')
        self.assertTrue(any('茉莉花茶' in n['text'] and n['reason']=='relevant' for n in selection['items']))
        self.assertTrue(any(n['kind']=='raw' for n in selection['items']))
        self.assertFalse(state['user_profile'])  # recalled past claims never overwrite current facts

    async def test_wrong_actor_or_evidence_fails_atomically(self):
        for actor,evidence in [('character','喜歡'),('user','不存在')]:
            state=initial_state(STAMP);state['history']=history(['喜歡紅茶'])
            before=deepcopy(state)
            with self.assertRaises(ValueError):add_batch(state,0,state['history'],[{'source_index':0,'actor':actor,'text':'茶','evidence':evidence,'stage':'statement'}])
            self.assertEqual(state,before)

    async def test_important_source_marking_and_bounded_selection(self):
        state=initial_state(STAMP);state['history']=history(['請記住這件事：我對花生過敏。','我知道了']+['一般對話'*100]*16)
        for i in range(0,18,2):add_batch(state,i,state['history'][i:i+2],[])
        self.assertTrue(state['memory']['notes'][0]['important'])
        result=await MemorySearch().select(state,'你好')
        self.assertLessEqual(result['used_chars'],result['budget_chars'])
        self.assertFalse(any(n['important'] for n in result['items']))
        result=await MemorySearch().select(state,'花生過敏')
        self.assertTrue(any(n['important'] for n in result['items']))
        self.assertEqual(len(state['memory']['notes']),18)

    async def test_hybrid_semantic_recall_and_failure_is_explicit(self):
        class Encoder:
            async def encode(self,texts,prompt_name=None):
                return [[1.,0.] if '難過' in t or '心情' in t else [0.,1.] for t in texts]
        state=initial_state(STAMP);state['history']=history(['我那天非常難過','嗯'])
        add_batch(state,0,state['history'],[])
        selected=await MemorySearch(Encoder()).select(state,'上次心情如何')
        self.assertEqual(selected['mode'],'hybrid')
        self.assertTrue(any(n['semantic_score']>.9 for n in selected['items']))
        class Broken:
            async def encode(self,*args,**kwargs):raise RuntimeError('offline')
        selected=await MemorySearch(Broken()).select(state,'難過')
        self.assertEqual(selected['mode'],'lexical_fallback')
        self.assertEqual(selected['fallback'],'RuntimeError')
        self.assertTrue(selected['items'])

    async def test_legacy_summary_retained_and_memory_changes_cache_key(self):
        state=initial_state(STAMP);state.pop('memory');state['summary']='舊版本摘要'
        ensure_memory(state)
        self.assertEqual(memory_context(state)['previous_summary_fallible'],'舊版本摘要')
        first=PyramidRunner.observation_key(state,'你好')
        state['memory']['selection']={'items':[{'id':'synthetic','text':'重要事情','source_index':0,'actor':'user','at':STAMP,'stage':'statement','evidence':'重要事情'}]}
        self.assertNotEqual(first,PyramidRunner.observation_key(state,'你好'))

    async def test_invalid_proposal_falls_back_atomically_and_does_not_repeat(self):
        cases = [
            '錯誤JSON',
            json.dumps({'notes': [{'source_index': 0}]}),
            json.dumps({'notes': [
                {'source_index': 0, 'text': '使用者喜歡茶', 'evidence': '喜歡紅茶', 'stage': 'statement'},
                {'source_index': 1, 'text': '角色回應', 'evidence': '改寫的確認', 'stage': 'statement'}]}),
            json.dumps({'notes': [{'source_index': 0, 'text': '茶', 'evidence': '喜歡 紅茶', 'stage': 'statement'}]}),
            json.dumps({'notes': [{'source_index': 0, 'actor': 'character', 'text': '茶', 'evidence': '紅茶', 'stage': 'statement'}]}),
            json.dumps({'notes': [{'source_index': 0, 'text': '', 'evidence': '紅茶', 'stage': 'statement'}]}),
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                state=initial_state(STAMP)
                state['history']=history(['我喜歡紅茶', '好，記得了']+['一般對話']*12)
                state['history'][0]['context']={'scene': {'location': '花園'}}
                original=deepcopy(state['history'])
                llm=FakeLLM([raw]);runner=PyramidRunner(llm);trace=DecisionTrace()
                await runner.compact(state,trace)
                self.assertEqual(state['summary_through'],2)
                self.assertEqual(state['history'],original)
                notes=state['memory']['notes']
                self.assertEqual([n['kind'] for n in notes],['raw_fallback']*2)
                self.assertEqual(notes[0]['actor'],'user')
                self.assertEqual(notes[1]['actor'],'character')
                self.assertEqual(notes[0]['evidence'],original[0]['content'])
                self.assertEqual(notes[0]['context'],original[0]['context'])
                self.assertTrue(any(n['rule']=='memory.proposal_rejected' for n in trace.nodes))
                await runner.compact(state,DecisionTrace())
                self.assertEqual(len(llm.calls),1)

    async def test_memory_transport_failure_still_stops_without_advancing(self):
        from companion.services import ServiceError
        class Offline(FakeLLM):
            async def generate(self,*args,**kwargs): raise ServiceError('offline')
        state=initial_state(STAMP);state['history']=history(['茶']*14)
        with self.assertRaises(ServiceError):
            await PyramidRunner(Offline([])).compact(state,DecisionTrace())
        self.assertEqual(state['summary_through'],0)
        self.assertFalse(state['memory']['notes'])

    async def test_chat_continues_after_rejected_memory_proposal(self):
        state=initial_state(STAMP)
        state['history']=history(['我喜歡紅茶','知道了']+['普通對話']*12)
        bad=json.dumps({'notes':[{'source_index':0,'text':'茶偏好','evidence':'不存在的引用','stage':'statement'}]})
        llm=FakeLLM([bad,json.dumps(observation([event('question',evidence='茶')])),
                     '你提過紅茶。',json.dumps(observation())])
        await PyramidRunner(llm).compact(state,DecisionTrace())
        result=await PyramidRunner(llm).run(state,TurnInput('我喜歡什麼茶','neutral-memory-test',True))
        self.assertEqual(result.reply,'你提過紅茶。')
        self.assertEqual(result.after['summary_through'],2)
        self.assertEqual(result.after['memory']['notes'][0]['kind'],'raw_fallback')
        self.assertNotIn('證據無效',result.reply)

    async def test_cursor_integrity_error_is_not_model_fallback(self):
        state=initial_state(STAMP);state['history']=history(['茶','嗯'])
        before=deepcopy(state)
        with self.assertRaises(ValueError):add_batch(state,1,state['history'],[])
        self.assertEqual(state,before)

    async def test_observer_and_writer_recall_shared_sources_with_independent_contexts(self):
        state=initial_state(STAMP);state['history']=history(['我喜歡茉莉花茶','嗯']+['普通對話']*12)
        llm=FakeLLM([json.dumps({'notes':[]}),json.dumps(observation([event('question',evidence='茶')])),
                     '你提過茉莉花茶。',json.dumps(observation())])
        await PyramidRunner(llm).compact(state,DecisionTrace())
        result=await PyramidRunner(llm).run(state,TurnInput('我喜歡什麼茶','one',True))
        observer=json.loads(llm.calls[1][1]['content'])
        writer=json.loads(llm.calls[2][0]['content'].split('\n',1)[1])
        self.assertIn('茉莉花茶',json.dumps(observer['past_memory'],ensure_ascii=False))
        for memory in (observer['past_memory'],):
            identities=[(n['actor'],n['at'],n['text']) for n in memory['items']]
            self.assertEqual(len(identities),len(set(identities)))
            self.assertTrue(all('source_index' not in n and 'id' not in n for n in memory['items']))
        self.assertIn('茉莉花茶','\n'.join(m['content'] for m in llm.calls[1]))
        self.assertNotIn('past_memory',writer)
        self.assertEqual(len(state['history']),16)
        self.assertEqual(result.after['summary_through'],2)

    async def test_observer_has_no_examples_or_source_compaction(self):
        state=initial_state(STAMP);state['history']=history(['我想喝茶','好啊'])
        original=deepcopy(state['history']);seen=[]
        class ExamplePressure(FakeLLM):
            async def generate(self,messages,on_request=None,**kwargs):
                seen.append(deepcopy(messages))
                if len(messages)>3:
                    # Reproduce the real transport's attempt to silently remove examples.
                    on_request([messages[0],messages[-1]])
                return await super().generate(messages,on_request=on_request,**kwargs)
        result=await PyramidRunner(ExamplePressure([json.dumps(observation())])).run(state,TurnInput('你好','examples-only'))
        self.assertEqual(len(seen),1)
        self.assertEqual(len(seen[0]),3)
        self.assertEqual(state['summary_through'],0)
        self.assertEqual(state['history'][:2],original)
        self.assertFalse(any(n['rule']=='observation.context_examples' for n in result.trace))
        self.assertFalse(any(n['rule']=='memory.coverage' for n in result.trace))

    async def test_optional_examples_do_not_mask_other_service_failures(self):
        from companion.services import ServiceError
        class Offline(FakeLLM):
            count=0
            async def generate(self,*args,**kwargs):
                self.count+=1
                raise ServiceError('offline')
        llm=Offline([]);trace=DecisionTrace()
        with self.assertRaises(ServiceError):await PyramidRunner(llm).observe(initial_state(STAMP),'你好','user',trace)
        self.assertEqual(llm.count,1)
        self.assertFalse(any(n['rule']=='observation.context_examples' for n in trace.nodes))

    async def test_unexpected_generation_capacity_error_never_erases_context(self):
        from companion.services import ServiceError
        state=initial_state(STAMP);state['history']=history(['我喜歡茉莉花茶。','我知道了。'])
        add_batch(state,0,state['history'],[])
        saved=deepcopy(state['memory']['notes']);raw=deepcopy(state['history'])
        class TightLLM(FakeLLM):
            async def generate(self,*args,**kwargs):raise ServiceError('上下文超量')
        with self.assertRaises(ServiceError):
            await PyramidRunner(TightLLM([])).run(state,TurnInput('茉莉花茶','one'))
        self.assertEqual(state['memory']['notes'],saved)
        self.assertEqual(state['history'],raw)
        self.assertEqual(state['summary_through'],2)
