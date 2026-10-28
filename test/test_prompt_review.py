"""Prompt reading contracts and movement invariants, using neutral fixtures."""
from copy import deepcopy
import json
import unittest
from companion.pyramid import initial_state,PyramidCore,DecisionTrace,TurnInput
from companion.pyramid_runner import PyramidRunner,writer_contract
from companion.writer_context import dialogue_facts
from companion.prompt_audit import audit_writer
from companion.scene_activity import reconcile_arrival
from test_pyramid import STAMP,observation,event,FakeLLM

class PromptReviewTests(unittest.IsolatedAsyncioTestCase):
    def state(self):
        s=initial_state(STAMP);s['scene'].update(location='書房',character_location='書房',status='active')
        s['activity']='整理報表';return s

    async def test_boolean_clarification_gate_ignores_none_placeholder(self):
        for placeholder in ['none','無','unknown']:
            s=self.state();raw=observation(missing_detail=placeholder,needs_clarification=False,
                utterance={'act':'question','purpose':'task','value':'算式','evidence':'六乘七？','ref':'','strength':1})
            result=await PyramidRunner(FakeLLM([json.dumps(raw)])).run(s,TurnInput('六乘七？','math',False))
            self.assertNotEqual(result.decision['primary'],'clarify')
        raw=observation(missing_detail='不知道哪一天',needs_clarification=True)
        observed=await PyramidRunner(FakeLLM([json.dumps(raw)])).observe(self.state(),'哪一天？','user',DecisionTrace())
        self.assertEqual(observed['missing_detail'],'不知道哪一天')

    async def test_current_state_scope_skips_unrelated_appearance_and_old_clock_memories(self):
        s=self.state();s['history']=[{'role':'user','content':'現在八點了','at':STAMP},{'role':'assistant','content':'八點','at':STAMP}]
        s['memory']['selection']={'items':[{'id':'raw:50','source_index':50,'actor':'character','at':STAMP,'stage':'statement','text':'現在八點了'}]}
        llm=FakeLLM([json.dumps(observation(information_scope='current_state',utterance={'act':'question','purpose':'other','value':'近況','evidence':'你好嗎？','ref':'','strength':1})), '很好。',json.dumps(observation())])
        result=await PyramidRunner(llm).run(s,TurnInput('你好嗎？','one',True))
        c=json.loads(llm.calls[1][0]['content'].split('\n',1)[1])
        self.assertNotIn('appearance',c['current_facts'])
        self.assertNotIn('past_memory',c)
        self.assertEqual(result.decision['information_scope'],'current_state')

    async def test_current_activity_has_single_owner_and_earlier_changes_survive(self):
        s=self.state();snapshot={'scene':dict(s['scene'],activity=s['activity'])}
        history=[{'role':'user','at':STAMP,'content':'嗨','context':snapshot} for _ in range(3)]
        rows=dialogue_facts(history,s)
        self.assertEqual(rows[0]['speaker'],'user')
        self.assertTrue(all('role' not in row for row in rows))
        self.assertTrue(all('context' not in row for row in rows))
        old={'scene':dict(s['scene'],location='花園',character_location='花園',activity='喝茶')}
        history=[dict(history[0],context=old),dict(history[1],context=old),history[2]]
        rows=dialogue_facts(history,s)
        self.assertEqual(rows[0]['context']['scene']['activity'],'喝茶')
        self.assertNotIn('context',rows[1])
        self.assertEqual(rows[2]['context']['scene'],'current')

    async def test_audit_finds_internal_fields_duplicate_prose_and_stale_travel(self):
        c={'identity':{'character':'小伴','user':'Alex'},'one':'這是一段完全相同而且很長的測試文字','two':'這是一段完全相同而且很長的測試文字',
           'rowid':3,'current_facts':{'activity':'正在前往書房','scene':{'character_location':'書房'}}}
        audit=audit_writer([{'role':'system','content':'規則\n'+json.dumps(c,ensure_ascii=False)}])
        kinds={i['kind'] for i in audit['issues']}
        self.assertTrue({'internal_field','repeated_structured_text','travel_activity_without_transition'}<=kinds)

class ArrivalReviewTests(unittest.TestCase):
    def state(self):
        s=initial_state(STAMP);s['scene'].update(location='臥室',character_location='臥室',user_location='花園',status='active')
        return s

    def test_trip_receipt_then_reply_still_arrives_at_original_deadline(self):
        s=self.state();core=PyramidCore();trace=DecisionTrace()
        s.update(scene_notice_delivery='external',scene_notifications_enabled=True)
        s['schedule']=[{'at':STAMP,'location':'書房','activity':'研究','availability':'busy'}]
        core.advance(s,'2026-10-15T00:10:00+00:00',trace)
        pending=s['scene']['pending'];core.acknowledge_scene_notice(s,pending['notice_id'],s['now'],trace)
        self.assertEqual(s['scene']['status'],'moving')
        self.assertEqual(s['scene']['character_location'],'臥室')
        core.user_arrived(s,trace,at='2026-10-15T00:12:00+00:00')
        core.advance(s,'2026-10-15T00:20:00+00:00',trace)
        self.assertEqual(s['scene']['character_location'],'書房')
        self.assertEqual(s['scene']['user_location'],'花園')
        self.assertEqual(s['activity'],'研究')
        self.assertEqual(s['scene']['status'],'active')

    def test_arrival_uses_destination_task_and_legacy_repair_requires_arrival_evidence(self):
        core=PyramidCore();trace=DecisionTrace();s=self.state()
        item={'at':STAMP,'location':'書房','activity':'前往書房','arrival_activity':'整理報表','availability':'busy'}
        core._commit_schedule(s,item,trace)
        self.assertEqual(s['activity'],'整理報表')
        # Recreate a legacy bug, without changing the recorded arrival.
        s['activity']=item['activity'];self.assertTrue(reconcile_arrival(s,trace))
        self.assertEqual(s['activity'],'整理報表')
        self.assertFalse(reconcile_arrival(s,trace))
        s['activity']=item['activity'];s['scene_history']=[]
        before=deepcopy(s);self.assertFalse(reconcile_arrival(s,trace));self.assertEqual(s,before)

    def test_explicit_character_move_cancels_old_trip_but_user_move_does_not(self):
        s=self.state();core=PyramidCore();trace=DecisionTrace()
        s['scene']['pending']={'location':'書房','delivery':'delivered','deadline':'2026-10-15T00:20:00+00:00'}
        s['scene']['status']='moving';s['activity']='前往書房'
        core.apply(s,[event('move',actor='user',target='user',field='location',value='廚房',evidence='我到了廚房')],'user','我到了廚房','user',trace)
        self.assertIsNotNone(s['scene']['pending']);self.assertEqual(s['scene']['status'],'moving')
        core.apply(s,[event('move',actor='character',target='character',field='location',value='花園',evidence='我到了花園')],'assistant','我到了花園','char',trace)
        self.assertIsNone(s['scene']['pending']);self.assertEqual(s['scene']['location'],'花園')
        self.assertEqual(s['scene']['character_location'],'花園');self.assertEqual(s['activity'],'')
