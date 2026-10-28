"""Proactive story source, selection, cancellation and receipt invariants."""
import json
from copy import deepcopy
from datetime import datetime,timedelta
import unittest
from unittest.mock import AsyncMock
from unittest.mock import patch
import test_pyramid_telegram as fixtures
from test_pyramid import observation,STAMP,FakeLLM
from companion.pyramid import initial_state,PyramidCore,DecisionTrace
from companion.story_core import StoryCore
from companion.story_proactive import StoryProactive
from companion.pyramid_life import PyramidLife
from companion.services import ServiceError
from companion.pyramid_runner import observer_snapshot


def seed(state,stamp=STAMP):
    message={'role':'user','content':'我買了植物圖鑑，還沒有看完','at':stamp,'turn_id':'book',
             'context':{'scene':{'character_location':'書店'}}}
    state['history']=[message]
    StoryCore().record(state,message,observation(topic='植物圖鑑',
        utterance={'act':'share','purpose':'social','value':'植物圖鑑','evidence':message['content'],'ref':'','strength':1},story_events=[]))
    state['energy']=90;state['acute_stress']=state['stress']=0
    state['relationship'].update(comfort=87,affection=87,familiarity=87,initiative=87)
    return message


class ProposalTests(unittest.TestCase):
    def test_source_is_real_future_is_not_assumed_complete(self):
        state=initial_state(STAMP);seed(state)
        pool=StoryProactive().collect(state,datetime.fromisoformat(STAMP)+timedelta(hours=3))
        self.assertEqual(len(pool),1)
        self.assertIn('還沒有看完',pool[0]['story'])
        self.assertEqual(pool[0]['actions'],['ask'])

    def test_legacy_unclassified_and_intimacy_sources_do_not_seed_neutral_topic(self):
        for change in ('legacy','intimacy'):
            state=initial_state(STAMP);message=seed(state)
            if change=='legacy':message.pop('_story')
            else:message['_story']['purpose']='intimacy'
            self.assertFalse(StoryProactive().collect(state,datetime.fromisoformat(STAMP)))

    def test_expiry_source_changes_and_contact_pause(self):
        state=initial_state(STAMP);message=seed(state);proposer=StoryProactive();now=datetime.fromisoformat(STAMP)
        candidate=proposer.collect(state,now)[0]
        self.assertFalse(proposer.valid(state,candidate,now+timedelta(days=4)))
        message['content']='我不看這本書了'
        self.assertFalse(proposer.valid(state,candidate,now))
        paused={'role':'user','content':'先不要主動找我','at':STAMP}
        state['history'].append(paused)
        proposer.control(state,paused,{'story_control':{'scope':'contact','action':'pause','ref':'','evidence':'先不要主動找我'}})
        self.assertTrue(proposer.contact_paused(state))
        state['history'].append({'role':'user','content':'我回來了','at':STAMP})
        self.assertFalse(proposer.contact_paused(state))

    def test_topic_close_and_resume_are_sourced(self):
        state=initial_state(STAMP);seed(state);p=StoryProactive();now=datetime.fromisoformat(STAMP)
        ref=p.collect(state,now)[0]['thread']
        for action,text in [('close','這件事別再問了'),('resume','可以再聊這件事')]:
            message={'role':'user','content':text,'at':STAMP}
            state['history'].append(message)
            p.control(state,message,{'story_control':{'scope':'topic','action':action,'ref':ref,'evidence':text}})
            pool=p.collect(state,now)
            self.assertEqual(bool(pool),action=='resume')

    def test_same_core_busy_short_question_and_sleep_defer(self):
        state=initial_state(STAMP);seed(state);pool=StoryProactive().collect(state,datetime.fromisoformat(STAMP))
        observation={'initiative':True,'proactive_candidates':pool}
        state['availability']='busy'
        decision=PyramidCore().decide(state,[],observation,DecisionTrace())
        self.assertEqual(decision['primary'],'ask');self.assertEqual(decision['story_candidate']['id'],pool[0]['id'])
        state['availability']='sleep'
        self.assertEqual(PyramidCore().decide(state,[],observation,DecisionTrace())['primary'],'defer')

    def test_social_invitation_not_gated_by_adult_mode(self):
        state=initial_state(STAMP);seed(state);pool=StoryProactive().collect(state,datetime.fromisoformat(STAMP))
        pool[0]['actions']=['invite'];state['adult_policy']['mode']='off'
        decision=PyramidCore().decide(state,[],{'initiative':True,'proactive_candidates':pool},DecisionTrace())
        self.assertEqual(decision['primary'],'invite');self.assertEqual(decision['intimacy_intention'],'none')

    def test_fulfilled_character_promise_removes_its_candidate(self):
        state=initial_state(STAMP);seed(state)
        promised={'role':'assistant','content':'我答應下次聊這本書','at':STAMP,'turn_id':'promise'}
        state['history'].append(promised)
        StoryCore().record(state,promised,observation(topic='書籍',utterance={'act':'promise','purpose':'social'}))
        state['events'].append({'id':'promise-ref','turn_id':'promise','kind':'promise','source':'assistant','evidence':'答應下次聊這本書'})
        state['promises']['promise-ref']={'actor':'character','text':'下次聊這本書','fulfilled':False}
        p=StoryProactive();now=datetime.fromisoformat(STAMP)
        self.assertTrue(any(c['thread']=='promise:promise-ref' for c in p.collect(state,now)))
        state['promises']['promise-ref']['fulfilled']=True
        self.assertFalse(any(c['thread']=='promise:promise-ref' for c in p.collect(state,now)))

    def test_ordinary_knowledge_questions_do_not_create_repetitive_followups(self):
        state=initial_state(STAMP);message=seed(state)
        message['_story'].update(act='question',purpose='task')
        self.assertFalse(StoryProactive().collect(state,datetime.fromisoformat(STAMP)))

    def test_answer_alone_does_not_seed_another_followup_question(self):
        state=initial_state(STAMP);message=seed(state)
        message['_story'].update(act='answer',purpose='social')
        self.assertFalse(StoryProactive().collect(state,datetime.fromisoformat(STAMP)))

    def test_back_to_back_reply_can_close_topic_before_any_heartbeat(self):
        state=initial_state(STAMP);seed(state);before=deepcopy(state)
        snapshot=observer_snapshot(state,'user')
        self.assertEqual(state,before)
        ref=next(iter(snapshot['story_threads']))
        message={'role':'user','content':'別再問這本書了','at':STAMP};state['history'].append(message)
        p=StoryProactive()
        p.control(state,message,{'story_control':{'scope':'topic','action':'close','ref':ref,'evidence':message['content']}})
        self.assertFalse(p.collect(state,datetime.fromisoformat(STAMP)))

    def test_answering_progress_is_not_a_permanent_topic_ban(self):
        state=initial_state(STAMP);seed(state);p=StoryProactive();now=datetime.fromisoformat(STAMP)
        ref=p.collect(state,now)[0]['thread']
        answered={'role':'user','content':'這本我已經看完了','at':STAMP}
        state['history'].append(answered)
        StoryCore().record(state,answered,observation(topic='植物圖鑑',utterance={'act':'answer','purpose':'social'}))
        p.control(state,answered,{'story_control':{'scope':'topic','action':'resolve','ref':ref,'evidence':answered['content']}})
        self.assertFalse(p.collect(state,now))
        fresh={'role':'user','content':'我又買了新的植物圖鑑','at':STAMP}
        state['history'].append(fresh)
        StoryCore().record(state,fresh,observation(topic='植物圖鑑',utterance={'act':'share','purpose':'social'}))
        self.assertTrue(p.collect(state,now))


class StoryDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.TelegramPyramidTests.asyncSetUp(self)
        self.now=datetime.fromisoformat('2026-10-17T14:00:00+08:00');self.app.now=lambda:self.now
        state=self.app.pyramid.load();source_at=(self.now-timedelta(hours=3)).isoformat();seed(state,source_at)
        state.update(now=self.now.isoformat(),updated_at=self.now.isoformat(),last_user_at=source_at,
                     life_plan_day=self.now.date().isoformat(),availability='busy',activity='整理書架',schedule=[])
        self.app.pyramid.save_clock(state)
        for k,v in {'life_enabled':'false','proactive_enabled':'true','last_user_at':source_at,
                    'quiet_start':'23:00','quiet_end':'08:00','proactive_daily_limit':'2'}.items():self.store.set(k,v)
        self.life=self.app.pyramid.life

    asyncTearDown=fixtures.TelegramPyramidTests.asyncTearDown

    async def test_story_send_works_without_life_and_is_not_resent_on_restart(self):
        self.llm.results=iter(['你之前買的植物圖鑑，最近讀到哪裡了？',json.dumps(observation())])
        await self.life.tick()
        self.tg.send.assert_awaited_once()
        self.assertEqual(self.life.count_today(self.now),1)
        self.assertEqual(self.store.get('proactive_unanswered'),'1')
        delivered=self.app.pyramid.load()['history'][-1]
        from companion.story_events import digest
        self.assertEqual(delivered['_proactive']['digest'],digest(delivered))
        self.assertTrue(delivered['_proactive']['thread'])
        messages=json.loads(self.store.get('last_text_prompt'))
        self.assertEqual([m['role'] for m in messages],['system','user'])
        contract=json.loads(messages[0]['content'].split('\n',1)[1])
        self.assertEqual(contract['decision']['primary'],'ask')
        self.assertIn('植物圖鑑',contract['proactive_story'])
        self.assertNotIn('story_context',contract)
        self.now+=timedelta(hours=5);self.life=PyramidLife(self.app.pyramid)
        await self.life.tick()
        self.tg.send.assert_awaited_once()

    async def test_quiet_hours_and_two_unanswered_do_not_call_writer(self):
        self.store.set('proactive_unanswered','2');await self.life.tick()
        self.assertFalse(self.llm.calls)
        self.store.set('proactive_unanswered','0');self.now=self.now.replace(hour=23)
        await self.life.tick();self.assertFalse(self.llm.calls)

    async def test_new_message_or_changed_source_during_generation_prevents_send(self):
        for kind in ('inbox','source'):
            self.store.set('pyramid_casual_retry_at','');self.app.inbox_event.clear()
            async def changed(messages,on_request=None,**kwargs):
                if on_request:on_request(messages)
                if kind=='inbox':self.app.inbox_event.set()
                else:
                    state=self.app.pyramid.load();state['history'][0]['content']='已改變的來源'
                    self.app.pyramid.save_clock(state)
                return '最近讀得如何？'
            self.llm.generate=changed
            await self.life.casual(self.app.pyramid.load(),self.now,DecisionTrace())
            self.tg.send.assert_not_awaited()

    async def test_uncertain_delivery_is_not_retried_or_written_as_history(self):
        self.tg.send.side_effect=ServiceError('unavailable')
        self.llm.results=iter(['最近讀得如何？'])
        await self.life.tick()
        self.assertEqual(self.store.db.execute("SELECT status FROM pyramid_outbound WHERE kind='casual'").fetchone()[0],'uncertain')
        self.assertEqual(len(self.app.pyramid.load()['history']),1)
        self.assertEqual(self.life.count_today(self.now),0)
        self.now+=timedelta(hours=5);await self.life.tick()
        self.tg.send.assert_awaited_once()

    async def test_debug_proactive_shows_reason_without_generating(self):
        self.store.set('proactive_enabled','false');await self.life.tick()
        await self.app.pyramid.command('/debug','proactive',self.config.allowed_user)
        self.assertFalse(self.llm.calls)
        self.assertTrue(any('主動訊息已關閉' in call.args[1] for call in self.tg.send.call_args_list))

    async def test_story_and_life_share_one_selector_and_one_daily_quota(self):
        state=self.app.pyramid.load()
        state['life_current']={'at':self.now.isoformat(),'share_worthy':True,'activity':'整理書架'}
        self.app.pyramid.save_clock(state)
        self.llm.results=iter(['那本圖鑑最近讀得如何？',json.dumps(observation())])
        with patch.object(self.app.pyramid.core,'decide',wraps=self.app.pyramid.core.decide) as selector:
            await self.life.casual(self.app.pyramid.load(),self.now,DecisionTrace())
            self.assertEqual(selector.call_count,1)
            self.assertEqual(len(selector.call_args.args[2]['proactive_candidates']),2)
        self.assertEqual(self.life.count_today(self.now),1)

    async def test_failed_post_analysis_has_bounded_idle_retries(self):
        state=self.app.pyramid.load()
        state['_pending_responses']=[{'turn_id':'old','reply':'先前回覆','at':state['history'][0]['at']}]
        self.app.pyramid.save_clock(state)
        self.llm.healthy=AsyncMock(return_value=True)
        self.llm.generate=AsyncMock(side_effect=ServiceError('offline'))
        for _ in range(5):
            await self.app.pyramid.maintain_responses()
            self.now+=timedelta(minutes=3)
        self.assertEqual(self.llm.generate.await_count,3)
        self.assertEqual(self.app.pyramid.memory_tasks.db.execute("SELECT attempts FROM memory_tasks WHERE task_id='old'").fetchone()[0],3)
