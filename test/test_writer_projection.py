import json
import unittest
from copy import deepcopy
from companion.writer_projection import writer_projection, INTERNAL_KEYS
from companion.pyramid import initial_state, PyramidCore, DecisionTrace
from companion.pyramid_runner import PyramidRunner, writer_contract, observer_snapshot
from companion.pyramid_memory import memory_context
from test_pyramid import STAMP, observation, FakeLLM
from companion.writer_projection import model_time, model_metadata, dialogue_metadata


class WriterProjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_scene_delta_and_minute_time_preserve_changes_and_unknown(self):
        scene={'location':'書房','status':'active','positions':{}}
        history=[{'role':'user','at':STAMP,'content':'你好','context':{'at':STAMP,'clock_source':'mac_mini_system_clock','scene':scene}},
                 {'role':'assistant','at':STAMP,'content':'嗨','context':{'scene':scene}},
                 {'role':'user','at':STAMP,'content':'去花園了','context':{'scene':scene,'scene_after':{'location':'花園'}}},
                 {'role':'assistant','content':'好'}]
        before=deepcopy(history)
        rows=dialogue_metadata(history,True)
        self.assertEqual(rows[0]['at'],'2026-10-15 00:00')
        self.assertEqual(rows[0]['context'],{'scene':{'location':'書房','status':'active'}})
        self.assertNotIn('context',rows[1])
        self.assertEqual(rows[2]['context'],{'scene_after':{'location':'花園'}})
        self.assertEqual(rows[3]['context'],{'scene':'unknown'})
        self.assertNotIn('clock_source',json.dumps(rows))
        self.assertEqual(history,before)

    async def test_actual_writer_boundary_removes_nested_provenance_without_losing_context(self):
        s=initial_state(STAMP)
        s['history']=[{'role':'user','content':'你好嗎？','at':STAMP,'context':{
            'scene':{'location':'書房','pending':{'id':'private-scene','location':'花園'}}}}]
        s['memory']['selection']={'items':[{'id':'raw:42','source_index':42,'actor':'user',
            'at':STAMP,'stage':'statement','text':'我喜歡紅茶','context':{'message_id':100}}]}
        d=PyramidCore().decide(s,[],observation(),DecisionTrace());d['information_scope']='past_events'
        runner=PyramidRunner(FakeLLM([]))
        runner.refresh_clock(s)
        before=deepcopy(s)
        messages=await runner.prepare_writer(s,d,'規則\n',DecisionTrace(),extra={'nested':{'row':8,'index':9,'value':'保留'}})
        contract=json.loads(messages[0]['content'].split('\n',1)[1])
        def check(value):
            if isinstance(value,dict):
                self.assertFalse(set(value)&INTERNAL_KEYS)
                for child in value.values():check(child)
            if isinstance(value,list):
                for child in value:check(child)
        check(contract)
        self.assertEqual(contract['nested'],{'value':'保留'})
        self.assertIn('我喜歡紅茶',contract['story_context'])
        self.assertNotIn('past_memory',contract)
        self.assertEqual(messages[-1],{'role':'user','content':'你好嗎？'})
        self.assertEqual(s,before)

    async def test_recap_writer_omits_indices_observer_keeps_references(self):
        s=initial_state(STAMP)
        recap=[{'source_index':99,'actor':'character','at':STAMP,'text':'稍後去花園','context':{}}]
        projected=memory_context(s,[],recap,identifiers=False)
        self.assertNotIn('來源99',projected['previous_summary_fallible'])
        self.assertIn('character',projected['previous_summary_fallible'])
        s['user_profile']=[{'id':'99','category':'name','value':'Alex'}]
        self.assertEqual(observer_snapshot(s,'user')['user_profile'][0]['id'],'99')
        self.assertEqual(s['user_profile'][0]['id'],'99')

    async def test_projection_preserves_literal_text_numbers_and_timestamps(self):
        value={'id':'secret','text':'我說 index 12，raw:99 是什麼？','at':STAMP,'items':[{'rowid':5,'stage':'proposed'}]}
        self.assertEqual(writer_projection(value),{'text':value['text'],'at':STAMP,'items':[{'stage':'proposed'}]})
