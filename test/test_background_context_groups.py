from unittest.mock import patch as mock_capacity
"""Bounded, chronological complete turns despite unsolicited background sends."""
from copy import deepcopy
import json
import unittest
from companion.context_plan import dialogue_groups,protected_dialogue_groups,ContextPlanner,compact_json
from companion.pyramid import initial_state,DecisionTrace
from companion.pyramid_runner import PyramidRunner
from companion.story_events import annotate
from test_pyramid import FakeLLM,observation,STAMP


def history(count=3):
    result=[{'role':'user','content':'前一個完整問題','turn_id':'tg:1'},
            {'role':'assistant','content':'前一個完整回答','turn_id':'tg:1'}]
    for i in range(count):
        result.append({'role':'assistant','content':'主動聊天'+str(i),'turn_id':'background:'+str(i),'background_kind':'casual'})
    result.append({'role':'assistant','content':'準備去溫室。','turn_id':'background:notice','background_kind':'scene'})
    return result

class BackgroundGroupTests(unittest.TestCase):
    def test_background_units_are_not_part_of_previous_human_exchange(self):
        h=history();before=deepcopy(h);g=dialogue_groups(h)
        self.assertEqual([r['_index'] for r in g[0]],[0,1])
        self.assertEqual([len(x) for x in g],[2,1,1,1,1])
        self.assertEqual([r['_index'] for x in g for r in x],list(range(len(h))))
        self.assertEqual(h,before)

    def test_required_units_do_not_grow_with_thirty_old_background_sends(self):
        g=dialogue_groups(history(30));keep,optional=protected_dialogue_groups(g,1)
        self.assertEqual([r['content'] for x in keep for r in x],
                         ['前一個完整問題','前一個完整回答','主動聊天29','準備去溫室。'])
        self.assertEqual(len(optional),29)

    def test_writer_also_keeps_current_question_and_foreground_pair(self):
        h=history(2)+[{'role':'user','content':'溫室種藥草嗎？','turn_id':'tg:2'}]
        keep,_=protected_dialogue_groups(dialogue_groups(h),2)
        text=[r['content'] for x in keep for r in x]
        self.assertEqual(text,['前一個完整問題','前一個完整回答','主動聊天1','準備去溫室。','溫室種藥草嗎？'])

    def test_missing_legacy_ids_are_not_guessed_or_split(self):
        h=[{'role':'user','content':'問題'},{'role':'assistant','content':'回覆一'},{'role':'assistant','content':'續句'}]
        self.assertEqual([len(g) for g in dialogue_groups(h)],[3])

    def test_valid_old_observer_metadata_can_identify_casual_message(self):
        h=history(2)
        for row in h:row.pop('background_kind',None)
        for row in h[2:4]:annotate(row,{'utterance':{'act':'question'}})
        keep,_=protected_dialogue_groups(dialogue_groups(h),1)
        self.assertIn('主動聊天1',[r['content'] for x in keep for r in x])
        h[3]['content']='已編輯後的另一句'
        keep,_=protected_dialogue_groups(dialogue_groups(h),1)
        self.assertIn('主動聊天0',[r['content'] for x in keep for r in x])
        self.assertNotIn('已編輯後的另一句',[r['content'] for x in keep for r in x])

    def test_old_background_before_new_completed_exchange_is_not_required(self):
        h=history()+[{'role':'user','content':'新問題','turn_id':'tg:2'},
                     {'role':'assistant','content':'新回答','turn_id':'tg:2'}]
        keep,_=protected_dialogue_groups(dialogue_groups(h),1)
        self.assertEqual([r['content'] for x in keep for r in x],['新問題','新回答'])

class BackgroundCapacityTests(unittest.IsolatedAsyncioTestCase):
    async def test_optional_middle_group_is_added_in_chronological_order(self):
        s=initial_state(STAMP);h=history(3)
        def build(recent,items,recap):return [{'role':'user','content':compact_json({'recent':recent})}]
        result=await ContextPlanner(FakeLLM([])).plan('user_observer',s,DecisionTrace(),build,1024,h,archive=False,memory=False,extra_groups=None)
        recent=json.loads(result[0]['content'])['recent']
        self.assertEqual([r['_index'] for r in recent],list(range(len(h))))

    @mock_capacity("companion.context_plan.CAPACITY",4096)
    @mock_capacity("companion.pyramid_runner.CAPACITY",4096)
    @mock_capacity("companion.services.CONTEXT_CAPACITY",4096)
    async def test_reserve_is_computed_using_entire_required_floor_once(self):
        class Counter(FakeLLM):
            async def count_context(self,messages):
                return 3198 if '必要舊摘要回答' in str(messages) else 3069
            async def generate(self,messages,**kw):
                self.reserve=kw['max_tokens'];return await super().generate(messages,**kw)
        s=initial_state(STAMP)
        s['history']=[{'role':'user','content':'必要舊摘要問題'},{'role':'assistant','content':'必要舊摘要回答'}]+history(3)
        before=deepcopy(s['history']);llm=Counter([json.dumps(observation())]);trace=DecisionTrace()
        result=await PyramidRunner(llm).observe(s,'溫室種藥草嗎？','user',trace)
        self.assertIsInstance(result,dict);self.assertEqual(llm.reserve,832)
        self.assertEqual(len(llm.calls),1);self.assertEqual(s['history'],before)
        failures=[n for n in trace.nodes if n['rule']=='context.capacity_failed']
        self.assertEqual(len(failures),1)
        self.assertEqual(failures[0]['output']['input_tokens'],3198)
        self.assertEqual(failures[0]['output']['component'],'continuity_summary')
        self.assertIn('必要舊摘要回答',str(llm.calls[0]))
        self.assertIn('前一個完整回答',str(llm.calls[0]))
