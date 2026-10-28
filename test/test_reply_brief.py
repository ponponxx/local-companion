"""Non-sexual conversation availability is separate from activity consent."""
from copy import deepcopy
import unittest
from companion.pyramid import initial_state,PyramidCore,DecisionTrace
from companion.reply_brief import compile_reply_brief
from companion.pyramid_runner import writer_contract,response_consistency
from test_pyramid import observation,STAMP


class ReplyBriefTests(unittest.TestCase):
    def decision(self,state,primary='answer'):
        d=PyramidCore().decide(state,[],observation(),DecisionTrace())
        d['primary']=primary
        return d

    def test_busy_answer_is_brief_without_changing_primary_or_state(self):
        s=initial_state(STAMP);s['availability']='busy';d=self.decision(s)
        before=deepcopy(s);brief=compile_reply_brief(s,d)
        self.assertEqual(brief['chat_engagement'],'brief')
        self.assertEqual(brief['activity_stance'],'not_applicable')
        self.assertEqual(brief['length'],'brief')
        self.assertIn('仍願意交談',brief['outline'])
        self.assertEqual(s,before);self.assertEqual(d['primary'],'answer')

    def test_refusing_activity_does_not_end_conversation(self):
        s=initial_state(STAMP);d=self.decision(s,'refuse')
        brief=compile_reply_brief(s,d)
        self.assertEqual(brief['activity_stance'],'declined')
        self.assertEqual(brief['chat_engagement'],'continue')
        self.assertIn('範圍限於該要求',brief['outline'])

    def test_pause_and_boundary_events_do_not_force_continuation(self):
        s=initial_state(STAMP);s['availability']='busy';d=self.decision(s)
        for kind in ['stop','pause','violation']:
            brief=compile_reply_brief(s,d,[{'kind':kind}])
            self.assertEqual(brief['chat_engagement'],'respect_pause')
            self.assertNotIn('仍願意交談',brief['outline'])
        s['availability']='sleep'
        self.assertEqual(compile_reply_brief(s,d)['chat_engagement'],'rest')

    def test_contract_replaces_vague_goal_and_keeps_stance(self):
        s=initial_state(STAMP);s['availability']='busy';d=self.decision(s,'conditional_accept')
        d['goal']='unrelated_goal'
        c=writer_contract(s,d)
        self.assertEqual(c['decision']['activity_stance'],'conditional')
        self.assertEqual(c['decision']['chat_engagement'],'brief')
        self.assertEqual(c['delivery']['length'],'brief')
        self.assertNotIn('goal',c['decision'])
        self.assertLess(len(c['decision']['response_outline']),100)
        self.assertLess(len(c['delivery_instruction']),50)

    def test_grounded_dismissal_is_flagged_without_rewriting(self):
        s=initial_state(STAMP);s['availability']='busy';d=self.decision(s)
        d['reply_brief']=compile_reply_brief(s,d)
        obs=observation(conversation_signal={'kind':'dismissive','evidence':'走開，別再來找我'})
        result=response_consistency(d,obs,[],reply='走開，別再來找我。')
        self.assertEqual(result['conversation_status'],'mismatch')
        self.assertEqual(result['status'],'mismatch')

    def test_ungrounded_evidence_does_not_flag_and_negative_answer_is_not_dismissal(self):
        s=initial_state(STAMP);d=self.decision(s,'refuse');d['reply_brief']=compile_reply_brief(s,d)
        obs=observation(conversation_signal={'kind':'dismissive','evidence':'走開'})
        result=response_consistency(d,obs,[],reply='現在沒空散步，可以聊兩句。')
        self.assertEqual(result['conversation_status'],'unknown')
        obs['conversation_signal']={'kind':'limited','evidence':'可以聊兩句'}
        result=response_consistency(d,obs,[],reply='現在沒空散步，可以聊兩句。')
        self.assertEqual(result['conversation_status'],'consistent')
        self.assertNotEqual(result['status'],'mismatch')


class ReplyBriefBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_post_observer_requires_explicit_signal_or_unknown(self):
        import json
        from companion.pyramid_runner import PyramidRunner
        from test_pyramid import FakeLLM
        raw=observation();raw.pop('conversation_signal')
        runner=PyramidRunner(FakeLLM([json.dumps(raw)]))
        with self.assertRaises(ValueError):
            await runner.observe(initial_state(STAMP),'你好','assistant',DecisionTrace())

    async def test_post_observer_discards_unquoted_attitude_claim(self):
        import json
        from companion.pyramid_runner import PyramidRunner
        from test_pyramid import FakeLLM
        raw=observation(conversation_signal={'kind':'dismissive','evidence':'不存在的引用'})
        runner=PyramidRunner(FakeLLM([json.dumps(raw)]))
        observed=await runner.observe(initial_state(STAMP),'我可以聊兩句。','assistant',DecisionTrace())
        self.assertEqual(observed['conversation_signal'],{'kind':'unknown','evidence':''})
