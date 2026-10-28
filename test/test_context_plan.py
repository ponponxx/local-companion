from unittest.mock import patch as mock_capacity
"""Stage-local token contracts with synthetic, non-private dialogue."""
from copy import deepcopy
import json
import unittest
from companion.context_plan import ContextPlanner,compact_json,continuity_entries
from companion.pyramid import initial_state,DecisionTrace,TurnInput
from companion.pyramid_runner import PyramidRunner,observer_snapshot
from companion.pyramid_memory import MemorySearch,add_batch,memory_context
from companion.services import ServiceError
from test_pyramid import FakeLLM,observation,STAMP


def note(index,text='茶',**extra):
    return {'id':str(index),'source_index':index,'actor':'user','at':STAMP,'stage':'statement',
            'text':text,'evidence':text,'kind':'observation','important':False,'context':{},**extra}


class ContextPlanTests(unittest.IsolatedAsyncioTestCase):
    @mock_capacity("companion.context_plan.CAPACITY",4096)
    @mock_capacity("companion.pyramid_runner.CAPACITY",4096)
    @mock_capacity("companion.services.CONTEXT_CAPACITY",4096)
    async def test_stage_reserves_are_independent_and_preflighted(self):
        class Counter:
            async def count_context(self,messages):
                return 2600+100*len(json.loads(messages[-1]['content'])['memory'])
        state=initial_state(STAMP);state['memory']['selection']={'items':[note(i,text=f'茶{i}') for i in range(6)]}
        original=deepcopy(state)
        def build(recent,items,recap):return [{'role':'user','content':compact_json({'memory':items})}]
        planner=ContextPlanner(Counter());trace=DecisionTrace()
        one=await planner.plan('user_observer',state,trace,build,1024,[],archive=False)
        two=await planner.plan('writer',state,trace,build,768,[],archive=False)
        self.assertEqual(len(json.loads(one[-1]['content'])['memory']),4)
        self.assertEqual(len(json.loads(two[-1]['content'])['memory']),6)
        self.assertEqual(state,original)
        for node in trace.nodes:
            if node['rule']=='context.prepared':
                o=node['output'];self.assertLessEqual(o['input_tokens']+o['output_tokens']+o['safety_tokens'],4096)

    @mock_capacity("companion.context_plan.CAPACITY",4096)
    @mock_capacity("companion.pyramid_runner.CAPACITY",4096)
    @mock_capacity("companion.services.CONTEXT_CAPACITY",4096)
    async def test_complete_recent_exchanges_survive_archive_cursor(self):
        state=initial_state(STAMP)
        state['history']=[{'role':'user' if i%2==0 else 'assistant','content':str(i)} for i in range(6)]
        state['summary_through']=6
        class Counter:
            async def count_context(self,messages):return 1000+400*len(json.loads(messages[0]['content'])['recent'])
        def build(recent,items,recap):return [{'role':'user','content':compact_json({'recent':recent})}]
        before=deepcopy(state)
        messages=await ContextPlanner(Counter()).plan('user_observer',state,DecisionTrace(),build,1024,state['history'],archive=False,memory=False)
        recent=json.loads(messages[0]['content'])['recent']
        self.assertEqual([h['content'] for h in recent],['2','3','4','5'])
        self.assertEqual(state,before)

    @mock_capacity("companion.context_plan.CAPACITY",4096)
    @mock_capacity("companion.pyramid_runner.CAPACITY",4096)
    @mock_capacity("companion.services.CONTEXT_CAPACITY",4096)
    async def test_required_overflow_stops_before_generation(self):
        class Counter:
            async def count_context(self,messages):return 4000
        state=initial_state(STAMP);history=[{'role':'user','content':'茶'},{'role':'assistant','content':'好'}]
        trace=DecisionTrace()
        with self.assertRaises(ServiceError):
            await ContextPlanner(Counter()).plan('user_observer',state,trace,lambda *a:[],1024,history)
        self.assertTrue(any(n['rule']=='context.capacity_failed' for n in trace.nodes))

    async def test_real_tokenizer_missing_is_explicit_failure(self):
        class Unsupported:
            async def count_context(self,messages):return None
        with self.assertRaises(ServiceError):await ContextPlanner(Unsupported()).count([])

    async def test_recall_deduplicates_source_but_not_separate_manual_memories(self):
        state=initial_state(STAMP)
        state['history']=[{'role':'user','content':'請記住這件事：我喜歡紅茶','at':STAMP}]
        add_batch(state,0,state['history'],[])
        state['manual_memories']=[{'id':1,'content':'紅茶要熱的'},{'id':2,'content':'紅茶不要糖'}]
        result=await MemorySearch().select(state,'紅茶',budget_chars=12000,max_items=12)
        self.assertEqual(sum(n['source_index']==0 for n in result['items']),1)
        self.assertEqual(sum(n['source_index']==-1 for n in result['items']),2)

    async def test_summary_is_connected_and_memory_evidence_not_duplicated(self):
        state=initial_state(STAMP);state['history']=[{'role':'user','content':'我喜歡茉莉花茶','at':STAMP}]
        add_batch(state,0,state['history'],[])
        result=memory_context(state)
        self.assertIn('茉莉花茶',result['previous_summary_fallible'])
        self.assertIn('uncertain',result['previous_summary_fallible'])
        self.assertEqual(continuity_entries(state,0),[])
        state['memory']['selection']={'items':[note(0)]}
        self.assertNotIn('evidence',memory_context(state)['items'][0])
        self.assertIn('evidence',state['memory']['notes'][0])

    async def test_recaps_recent_and_long_term_do_not_repeat_same_source(self):
        state=initial_state(STAMP)
        state['history']=[{'role':'user','content':'我喜歡茶','at':STAMP},{'role':'assistant','content':'記住了','at':STAMP}]
        add_batch(state,0,state['history'],[])
        state['memory']['selection']={'items':[note(0),note(0,id='other')]}
        def build(recent,items,recap):return [{'role':'user','content':compact_json({'recent':recent,'items':items,'recap':recap})}]
        messages=await ContextPlanner(FakeLLM([])).plan('writer',state,DecisionTrace(),build,768,state['history'])
        payload=json.loads(messages[0]['content'])
        self.assertEqual(len(payload['recent']),2)
        self.assertFalse(payload['items'])
        self.assertFalse(payload['recap'])

    async def test_observer_snapshot_excludes_decision_scores_and_keeps_event_refs(self):
        state=initial_state(STAMP);state['invitations']['invite-1']={'actor':'user','target':'character','action':'喝茶','purpose':'social','status':'proposed','evidence':'喝茶'}
        view=observer_snapshot(state,'user')
        for key in ['traits','relationship','emotions','need_weights','adult_traits','intimacy','energy','stress']:
            self.assertNotIn(key,view)
        self.assertIn('invite-1',view['invitations'])
        self.assertEqual(view['invitations']['invite-1']['action'],'喝茶')
        self.assertNotIn('evidence',view['invitations']['invite-1'])

    async def test_response_observer_is_scoped_to_current_reply(self):
        state=initial_state(STAMP);state['history']=[{'role':'user','content':'舊話題'*1000}]
        llm=FakeLLM([json.dumps(observation())]);runner=PyramidRunner(llm)
        runner.response_request='喝茶嗎';runner.response_decision={'primary':'accept','target_event':'invite-1','conditions':''}
        await runner.observe(state,'好啊','assistant',DecisionTrace())
        payload=json.loads(llm.calls[0][-1]['content'])
        self.assertEqual(payload['recent_dialogue'],[])
        self.assertEqual(payload['past_memory']['items'],[])
        self.assertEqual(payload['response_context']['request'],'喝茶嗎')
        self.assertEqual(payload['response_context']['decision']['target_event'],'invite-1')
        self.assertNotIn('舊話題',llm.calls[0][-1]['content'])

    async def test_ref_projection_retains_meaning_and_conditions(self):
        state=initial_state(STAMP)
        state['issues']={'issue':{'text':'先前誤解了用餐時間','resolved':False}}
        state['promises']={'promise':{'text':'晚飯後聊書','actor':'character','fulfilled':False}}
        state['loads']={'load':{'text':'工作尚未完成','value':15}}
        state['invitations']={'invite':{'actor':'user','target':'character','action':'散步','purpose':'social','status':'accepted','conditions':'晚飯後'}}
        view=observer_snapshot(state,'assistant')
        self.assertEqual(view['issues']['issue']['text'],'先前誤解了用餐時間')
        self.assertEqual(view['promises']['promise']['text'],'晚飯後聊書')
        self.assertEqual(view['loads']['load']['text'],'工作尚未完成')
        self.assertEqual(view['invitations']['invite']['conditions'],'晚飯後')

    async def test_unarchived_gap_has_sourced_recap_and_remains_searchable(self):
        state=initial_state(STAMP)
        state['history']=[{'role':'user' if i%2==0 else 'assistant','content':f'段落{i}','at':STAMP} for i in range(8)]
        state['history'][2]['content']='我喜歡茉莉花茶'
        recap=continuity_entries(state,6)
        self.assertEqual([n['source_index'] for n in recap],[2,3,4,5])
        result=await MemorySearch().select(state,'茉莉花茶',budget_chars=12000,max_items=12)
        self.assertTrue(any('茉莉花茶' in n['text'] for n in result['items']))
        self.assertEqual(state['summary_through'],0)

    @mock_capacity("companion.context_plan.CAPACITY",4096)
    @mock_capacity("companion.pyramid_runner.CAPACITY",4096)
    @mock_capacity("companion.services.CONTEXT_CAPACITY",4096)
    async def test_large_memory_batch_uses_source_fallback_without_generation(self):
        state=initial_state(STAMP)
        state['history']=[{'role':'user' if i%2==0 else 'assistant','content':'茶'*5000} for i in range(14)]
        class Counter(FakeLLM):
            async def count_context(self,messages):return 4000
        llm=Counter([])
        await PyramidRunner(llm).compact(state,DecisionTrace())
        self.assertEqual(state['summary_through'],2)
        self.assertEqual([n['kind'] for n in state['memory']['notes']],['raw_fallback','raw_fallback'])
        self.assertFalse(llm.calls)
        self.assertEqual(len(state['history'][0]['content']),5000)
