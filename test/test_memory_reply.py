"""Recall handoff tests use neutral source-linked fixtures only."""
from copy import deepcopy
from datetime import datetime
import json
import unittest
from companion.memory_reply import compile_memory_reply
from companion.reply_brief import attach_memory_answer
from companion.story_events import annotate
from companion.context_plan import dialogue_groups
from companion.pyramid import PyramidCore,DecisionTrace
from companion.pyramid_runner import PyramidRunner,writer_prefix
from companion.pyramid_memory import MemorySearch
from test_scene_topic_index import state,visit,turn
from test_pyramid import FakeLLM


def rows(s):return [r for g in dialogue_groups(s['history']) for r in g]
def old_sources(s):return [{'source_indices':list(range(len(s['history']))),'source_index':0}]

class MemoryReplyTests(unittest.TestCase):
    def test_planned_claim_keeps_actor_and_not_completion(self):
        s=state();m=turn(s,'我打算明天才寄信，尚未寄出。')
        annotate(m,{'story_events':[dict(actor='user',target='none',stage='planned',action=m['content'],evidence=m['content'])]})
        before=deepcopy(s);packet,audit=compile_memory_reply(s,'寄了嗎？',rows(s),[],[])
        fact=packet['available_facts'][0]
        self.assertEqual(fact['actor'],'使用者');self.assertIn('尚未完成',fact['status'])
        self.assertEqual(s,before);self.assertEqual(audit['selected_sources'],[0,1])

    def test_current_question_not_used_as_evidence(self):
        s=state();m=turn(s,'我昨天已經寄出信了嗎？',reply='')
        # Simulate pre-reply history ending with a user question.
        s['history'].pop();packet,audit=compile_memory_reply(s,m['content'],rows(s),[],[])
        self.assertEqual(audit['selected_sources'],[])
        self.assertFalse(packet['available_facts']);self.assertTrue(packet['missing_information'])

    def test_formal_acceptance_is_not_started(self):
        s=state();m=turn(s,'明天一起泡茶吧。');m['turn_id']='invite-turn'
        s['events']=[dict(id='tea',ref='',source='user',turn_id=m['turn_id'],evidence='一起泡茶')]
        s['invitations']['tea']=dict(actor='user',target='character',action='泡茶',status='accepted')
        packet,_=compile_memory_reply(s,'泡好了嗎？',rows(s),[],[])
        formal=[f for f in packet['available_facts'] if '正式進度' in f['basis']]
        self.assertEqual(formal[0]['status'],'已接受、未開始')

    def test_unselected_old_source_does_not_supply_facts(self):
        s=state();turn(s,'我叫舊同事喝茶。');turn(s,'我現在只想看書。',topic='看書')
        packet,audit=compile_memory_reply(s,'看書的事？',rows(s)[2:],[],[])
        self.assertNotIn(0,audit['selected_sources'])
        self.assertNotIn('舊同事',json.dumps(packet,ensure_ascii=False))

    def test_scene_event_index_never_impersonates_transcript(self):
        s=state();turn(s,'這是無關而未選的原文。')
        packet,audit=compile_memory_reply(s,'到哪？',[],[dict(kind='scene_event',source_index=0)],[])
        self.assertFalse(audit['selected_sources']);self.assertFalse(packet['available_facts'])

    def test_changed_source_invalidates_claims(self):
        s=state();m=turn(s,'我寄出信了。');m['content']='我沒有寄出信。'
        packet,_=compile_memory_reply(s,'寄出了嗎？',rows(s),[],[])
        self.assertFalse(packet['available_facts']);self.assertTrue(packet['missing_information'])

    def test_relative_dates_use_source_speech_clock(self):
        s=state();turn(s,'我打算明天面試。',at='2026-10-21T23:50:00+08:00')
        s['now']='2026-10-22T00:10:00+08:00'
        packet,_=compile_memory_reply(s,'今天嗎？',rows(s),[],[])
        self.assertIn({'word':'明天','date':'2026-10-22'},packet['available_facts'][0]['time_words'])

    def test_multiple_visits_keep_ambiguity_and_unknown_user_location(self):
        s=state();visit(s,'one','花園');turn(s,'我想看紅色的書。')
        visit(s,'two','花園','2026-10-21T12:00:00+08:00',before='茶館')
        turn(s,'我想看藍色的書。',at='2026-10-21T12:01:00+08:00')
        s['memory']={'selection':{'scene_query':{'explicit':True,'ambiguous':True,'sources':[0,1,2,3]}}}
        packet,_=compile_memory_reply(s,'花園那次？',rows(s),[],[])
        self.assertTrue(any('多次到訪' in item for item in packet['uncertainty']))
        self.assertEqual(packet['speech_context']['user_location'],'未記錄')
        self.assertIn('不代表',packet['speech_context']['meaning'])
        encoded=json.dumps(packet,ensure_ascii=False)
        for key in ('source_index','digest','turn_id','ref','one','two'):self.assertNotIn('"'+key+'"',encoded)

    def test_outline_does_not_reselect_or_add_clarification(self):
        contract={'decision':{'primary':'answer','response_outline':'直接回答本輪問題。','activity_stance':'not_applicable'}}
        result=attach_memory_answer(contract,{'available_facts':[]})
        self.assertEqual(result['decision']['primary'],'answer')
        self.assertEqual(result['decision']['activity_stance'],'not_applicable')
        self.assertIn('不另加追問',result['decision']['response_outline'])

class MemoryReplyIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_packet_is_in_counted_prompt_and_trace(self):
        s=state();turn(s,'我打算明天才寄信，現在尚未寄出。')
        q='我已經寄出信了嗎？';await MemorySearch().select(s,q)
        s['history'].append({'role':'user','content':q,'at':s['now']})
        d=PyramidCore().decide(s,[],{'utterance':{'act':'question','purpose':'other'},'appraisals':[]},DecisionTrace())
        d.update(primary='answer',information_scope='past_events',content=q)
        llm=FakeLLM([]);trace=DecisionTrace()
        messages=await PyramidRunner(llm,clock=lambda:datetime.fromisoformat(s['now'])).prepare_writer(s,d,writer_prefix('CH'),trace)
        body=json.loads(messages[0]['content'].split('\n',1)[1])
        self.assertNotIn('memory_answer',body['decision'])
        node=next(n for n in trace.nodes if n['rule']=='memory.packet')
        self.assertEqual(node['output']['text'],body.get('story_context',''))
        self.assertIn('尚未寄出',str(messages))
        self.assertFalse(llm.calls)
        self.assertTrue(any(n['rule']=='context.prepared' for n in trace.nodes))

    async def test_telegram_load_enables_mode_after_import_and_reset(self):
        from test_pyramid_telegram import TelegramPyramidTests
        fixture=TelegramPyramidTests();await fixture.asyncSetUp()
        try:
            first=fixture.app.pyramid.load()
            self.assertEqual(first['memory_index_mode'],'scene-topic-v1')
            first.pop('memory_index_mode');fixture.app.pyramid.save_clock(first)
            self.assertEqual(fixture.app.pyramid.load()['memory_index_mode'],'scene-topic-v1')
        finally:await fixture.asyncTearDown()

class MemoryReplyPriorityTests(unittest.TestCase):
    def test_selected_retrieval_wins_over_unrelated_recent_claims(self):
        s=state();turn(s,'我主管要修改藍色封面的月報。',topic='月報')
        for text in ('我想買茶。','我想看花。','我想看樹。'):
            turn(s,text,topic='隨口閒聊')
        packet,_=compile_memory_reply(s,'主管要改什麼？',rows(s),[{'source_indices':[0,1],'source_index':0}],[])
        self.assertIn('藍色封面',packet['available_facts'][0]['content'])

    def test_scene_scope_does_not_use_required_dialogue_from_other_visit(self):
        s=state();turn(s,'我在上次花園談過報告。');turn(s,'這是別次喝茶的事。',where='茶館')
        s['memory']={'selection':{'scene_query':{'explicit':True,'sources':[0,1]}}}
        packet,audit=compile_memory_reply(s,'花園那次？',rows(s),[],[])
        self.assertEqual(audit['selected_sources'],[0,1])
        self.assertNotIn('別次喝茶',json.dumps(packet,ensure_ascii=False))
