import json
import unittest
from copy import deepcopy
from datetime import datetime
from companion.story_proactive import continuation_handoff
from companion.story_events import digest
from companion.pyramid import initial_state,DecisionTrace,PyramidCore
from companion.pyramid_runner import PyramidRunner,writer_prefix
from companion.flow_debug import describe_flow
from test_pyramid import FakeLLM,STAMP


class ContinuationTests(unittest.IsolatedAsyncioTestCase):
    def state(self):
        s=initial_state(STAMP)
        s['history']=[{'role':'user','content':'吃午餐了嗎？','at':STAMP,'turn_id':'meal'},
                      {'role':'assistant','content':'還沒有，我準備去餐廳。','at':STAMP,'turn_id':'meal'}]
        return s

    def candidate(self):
        return {'id':'follow:meal','thread':'story:meal','subtype':'story','source_index':0,'source_indices':[0,1],
                'story':'使用者問：吃午餐了嗎？角色回覆：還沒有，我準備去餐廳。','outline':'再關心午餐。'}

    def sent(self,s,text='剛才說要吃午餐，你現在方便聊嗎？',thread='story:meal'):
        h={'role':'assistant','content':text,'at':STAMP,'turn_id':'background:prior','background_kind':'casual'}
        h['_proactive']={'thread':thread,'digest':digest(h)}
        s['history'].append(h)
        return h

    def test_prior_answer_is_labelled_as_already_spoken_without_copying_sources(self):
        s=self.state();before=deepcopy(s);p,a=continuation_handoff(s,self.candidate())
        self.assertIn('已有角色回覆',p['prior_exchange'])
        self.assertIn('回頭關心',p['intent']);self.assertNotIn('還沒有，我準備',str(p));self.assertEqual(s,before)
        self.assertEqual(a['source_indices'],[0,1])

    def test_revisit_remains_allowed_even_without_new_progress_or_user_reply(self):
        s=self.state();self.sent(s);p,a=continuation_handoff(s,self.candidate())
        self.assertIn('可輕輕再問',p['contact_status']);self.assertIn('可換口吻',p['instruction'])
        self.assertEqual(p['already_sent']['speaker'],'character');self.assertEqual(a['included_indices'],[2])

    def test_user_speaking_does_not_mean_question_answered(self):
        s=self.state();self.sent(s);s['history'].append({'role':'user','content':'我有另一個問題。','at':STAMP})
        p,_=continuation_handoff(s,self.candidate())
        self.assertIn('是否回答此話題未判定',p['contact_status'])

    def test_independent_leaf_story_does_not_reintroduce_hospital_or_other_thread(self):
        s=self.state();self.sent(s,'先去醫院？',thread='hospital')
        p,a=continuation_handoff(s,{'subtype':'life_event','thread':'leaf','story':'溫室葉片。'})
        self.assertNotIn('醫院',str(p));self.assertEqual(a['included_indices'],[])
        self.assertIn('分享',p['intent'])

    def test_changed_source_is_not_a_trusted_disclosure_record(self):
        s=self.state();h=self.sent(s);h['content']='改過的歷史'
        p,_=continuation_handoff(s,self.candidate());self.assertNotIn('contact_status',p)

    def test_long_previous_send_is_omitted_whole_not_cut_into_false_quote(self):
        s=self.state();self.sent(s,'很長的完整訊息。'*100)
        p,a=continuation_handoff(s,self.candidate());self.assertNotIn('already_sent',p)
        self.assertIn('未附原話',p['previous_contact_unavailable']);self.assertEqual(a['omitted_groups'],1)

    def test_previous_contact_already_in_story_is_not_quoted_twice(self):
        s=self.state();h=self.sent(s);c=self.candidate();c['story']+='角色先前主動說：'+h['content']
        p,_=continuation_handoff(s,c);self.assertNotIn('already_sent',p)

    async def test_actual_prompt_and_flow_match_and_foreground_is_unchanged(self):
        s=self.state();trace=DecisionTrace();d=PyramidCore().decide(s,[],
            {'utterance':{'act':'question','purpose':'other'},'appraisals':[]},trace)
        d.update(primary='ask',information_scope='current_state',story_candidate=self.candidate())
        runner=PyramidRunner(FakeLLM([]),clock=lambda:datetime.fromisoformat(s['now']))
        messages=await runner.prepare_writer(s,d,writer_prefix('CH'),trace,output_tokens=256,
            control={'role':'user','content':'請撰寫主動訊息。'})
        contract=json.loads(messages[0]['content'][messages[0]['content'].find('{'):])
        node=next(n for n in trace.nodes if n['rule']=='proactive.continuation')
        self.assertEqual(contract['proactive_continuation'],node['output'])
        self.assertIn('主動接續',describe_flow(node)[0])
        self.assertEqual(str(messages).count('還沒有，我準備去餐廳。'),1)
        self.assertNotIn('source_indices',str(contract['proactive_continuation']))
        foreground=await runner.prepare_writer(s,d,writer_prefix('CH'),DecisionTrace())
        self.assertNotIn('proactive_continuation',str(foreground))
