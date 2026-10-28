import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import json
import unittest
from unittest.mock import AsyncMock

from companion import life_events as le
from companion.pyramid import initial_state, DecisionTrace
from companion.pyramid_runner import PyramidRunner, observer_snapshot, OBSERVATION_SCHEMA, RESPONSE_OBSERVATION_SCHEMA, validate_output
from companion.pyramid_memory import catalogue
from companion.story_proactive import StoryProactive
from companion.services import ServiceError
from companion.telegram_settings import FIELDS
import test_pyramid_telegram as fixtures
from test_pyramid import observation


NOW=datetime.fromisoformat('2026-10-18T10:00:00+08:00')


def prepared():
    state=initial_state(NOW.isoformat())
    state['story']={'name':'林青','background':'28歲植物研究員','personality':'溫和','tone':'自然'}
    state.update(activity='整理植物圖鑑',availability='free')
    state['scene'].update(location='書房',character_location='書房',user_location='客廳',status='active')
    state['schedule']=[{'start':'10:00','at':NOW.isoformat(),'activity':'整理植物圖鑑','arrival_activity':'',
        'location':'書房','availability':'free'},
        {'start':'16:00','at':(NOW+timedelta(hours=6)).isoformat(),'activity':'休息','location':'花園','availability':'free'}]
    seed={'start':'10:00','template':'reading_note'}
    drafts=le.validate_seeds([seed],state['schedule'],'2026-10-18')
    le.install(state,drafts,'2026-10-18')
    return state


def opened():
    state=prepared();le.advance(state,NOW,'normal',DecisionTrace());return state


class LifeEventTests(unittest.TestCase):
    def test_drafts_never_enter_reading_memory_or_proactive_pool(self):
        state=prepared()
        self.assertEqual(le.current_reading(state),'')
        self.assertEqual(list(le.documents(state)),[])
        self.assertFalse(le.candidates(state,NOW))
        self.assertNotIn('life_event',observer_snapshot(state,'user'))

    def test_start_requires_present_place_activity_and_arrival(self):
        for change in ('place','activity','pending','sleep'):
            state=prepared()
            if change=='place':state['scene']['character_location']='客廳'
            if change=='activity':state['activity']='喝茶'
            if change=='pending':state['scene']['pending']={'location':'書房'}
            if change=='sleep':state['availability']='sleep'
            le.advance(state,NOW,'normal',DecisionTrace())
            self.assertIsNone(le.active(state),change)
        state=prepared();le.advance(state,NOW-timedelta(seconds=1),'normal',DecisionTrace())
        self.assertIsNone(le.active(state))

    def test_once_only_no_user_relationship_or_physical_mutation(self):
        state=prepared();before=deepcopy(state)
        le.advance(state,NOW,'normal',DecisionTrace());first=deepcopy(state)
        le.advance(state,NOW+timedelta(minutes=10),'normal',DecisionTrace())
        self.assertEqual(first,state)
        for k in ('scene','appearance','user_appearance','relationship','user_profile','energy','stress'):
            self.assertEqual(before[k],state[k],k)
        self.assertGreater(state['emotions']['surprise'],before['emotions']['surprise'])
        self.assertEqual(state['history'],[])

    def test_invalid_seed_does_not_mutate_state(self):
        state=prepared();before=deepcopy(state)
        with self.assertRaises(ValueError):le.validate_seeds([{'start':'99:99'}],state['schedule'],'2026-10-18')
        self.assertEqual(before,state)

    def test_only_one_active_and_daily_install_idempotent(self):
        state=opened();book=le.ledger(state)
        le.install(state,[{'id':'bad'}],'2026-10-18')
        self.assertEqual(len(book['stories']),1);self.assertEqual(book['drafts'],[])

    def test_expiry_never_invents_success_or_replays_missed_days(self):
        state=opened()
        le.advance(state,NOW+timedelta(days=1),'normal',DecisionTrace())
        story=le.ledger(state)['stories'][0]
        self.assertEqual(story['status'],'expired');self.assertEqual(len(story['beats']),1)
        missed=prepared();le.advance(missed,NOW+timedelta(days=1),'normal',DecisionTrace())
        self.assertEqual(le.ledger(missed)['stories'],[])

    def test_two_progressions_close_without_user_reply(self):
        state=opened();ref=le.active(state)['id']
        self.assertTrue(le.commit_step(state,ref,{'choice':'compare'},NOW+timedelta(hours=2),'normal',DecisionTrace()))
        with self.assertRaises(ValueError):le.commit_step(state,ref,{'choice':'compare'},NOW+timedelta(hours=4),'normal',DecisionTrace())
        self.assertTrue(le.commit_step(state,ref,{'choice':'leave_open'},NOW+timedelta(hours=4),'normal',DecisionTrace()))
        self.assertIsNone(le.active(state));self.assertEqual(len(le.ledger(state)['stories'][0]['beats']),3)

    def test_suggestions_pause_resume_and_close_are_source_bound(self):
        state=opened();story=le.active(state);trace=DecisionTrace()
        for index,action in enumerate(('suggest','pause','resume','close')):
            message={'content':'先查圖鑑索引','at':NOW.isoformat(),'turn_id':str(index)}
            obs={'life_event_input':{'ref':story['id'],'action':action,'evidence':message['content']}}
            le.contribute(state,obs,message,trace)
            self.assertEqual(len(story['contributions']),index+1)
            self.assertEqual(len(story['beats']),1)
        self.assertEqual(story['status'],'cancelled')
        self.assertEqual(le.candidates(state,NOW),[])

    def test_forged_reference_and_quote_do_not_change_story(self):
        for ref,evidence in [('wrong','試試看'),(None,'根本沒說')]:
            state=opened();before=deepcopy(state)
            le.contribute(state,{'life_event_input':{'ref':ref or le.active(state)['id'],'action':'close','evidence':evidence}},
                          {'content':'試試看','at':NOW.isoformat(),'turn_id':'x'},DecisionTrace())
            self.assertEqual(state,before)

    def test_delivery_separate_from_occurrence_and_latest_only(self):
        state=opened();candidate=le.candidates(state,NOW)[0]
        self.assertEqual(le.active(state)['beats'][0]['disclosure'],'unshared')
        le.disclosure(state,candidate,'uncertain')
        self.assertFalse(le.candidates(state,NOW))
        self.assertEqual(le.active(state)['status'],'open')
        le.commit_step(state,le.active(state)['id'],{'choice':'check_index'},NOW+timedelta(hours=2),'normal',DecisionTrace())
        new=le.candidates(state,NOW+timedelta(hours=2))[0]
        self.assertNotEqual(candidate['id'],new['id'])
        le.disclosure(state,candidate,'delivered')
        self.assertEqual(le.active(state)['beats'][-1]['disclosure'],'unshared')
        le.disclosure(state,new,'delivered');self.assertFalse(le.candidates(state,NOW+timedelta(hours=2)))

    def test_normal_response_disclosure_uses_exact_quote_and_version(self):
        state=opened();story=le.active(state)
        proposal={'life_event_disclosure':{'ref':story['id'],'version':1,'evidence':'圖鑑有一處註記不一致'}}
        le.response_disclosure(state,proposal,'午安',DecisionTrace())
        self.assertEqual(story['beats'][0]['disclosure'],'unshared')
        le.response_disclosure(state,proposal,'圖鑑有一處註記不一致',DecisionTrace())
        self.assertEqual(story['beats'][0]['disclosure'],'delivered')

    def test_archive_retrievable_and_roundtrip_preserves_source(self):
        state=opened();state=json.loads(json.dumps(state))
        docs=[d for d in catalogue(state) if d['kind']=='life_event']
        self.assertEqual(len(docs),1);self.assertIn('註記',docs[0]['text'])
        self.assertIn('尚未確認告知',docs[0]['text'])
        self.assertFalse(state['history'])

    def test_contract_roles_separate_and_existing_proactive_pool_used(self):
        state=opened();pool=StoryProactive().collect(state,NOW)
        self.assertEqual(pool[0]['subtype'],'life_event')
        self.assertTrue(StoryProactive().valid(state,pool[0],NOW))
        self.assertIn('life_event_input',OBSERVATION_SCHEMA['properties'])
        self.assertNotIn('life_event_input',RESPONSE_OBSERVATION_SCHEMA['properties'])
        self.assertIn('life_event_disclosure',RESPONSE_OBSERVATION_SCHEMA['properties'])


class LifeEventIntegrationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.TelegramPyramidTests.asyncSetUp
    asyncTearDown=fixtures.TelegramPyramidTests.asyncTearDown

    def setup_event(self):
        self.now=NOW
        self.app.now=lambda:self.now
        self.store.set('life_enabled','true');self.store.set('quiet_start','23:00');self.store.set('quiet_end','08:00')
        self.llm.healthy=AsyncMock(return_value=True)
        state=opened();self.app.pyramid.save_clock(state)
        return state

    async def test_idle_not_due_no_model_and_due_one_call_with_advice(self):
        state=self.setup_event();trace=DecisionTrace()
        await self.app.pyramid.life.events(state,self.now,trace)
        self.assertEqual(self.llm.calls,[])
        story=le.active(state)
        le.contribute(state,{'life_event_input':{'ref':story['id'],'action':'suggest','evidence':'查查索引吧'}},
                      {'content':'查查索引吧','at':NOW.isoformat(),'turn_id':'advice'},trace)
        self.app.pyramid.save_clock(state)
        self.now+=timedelta(hours=2)
        self.llm.results=iter([json.dumps({'choice':'check_index'})])
        await self.app.pyramid.life.events(state,self.now,trace)
        self.assertEqual(len(self.llm.calls),1)
        self.assertIn('查查索引吧',self.llm.calls[0][1]['content'])
        saved=self.app.pyramid.load();self.assertEqual(len(le.active(saved)['beats']),2)
        self.tg.send.assert_not_awaited()

    async def test_new_input_discards_generated_progress(self):
        state=self.setup_event();self.now+=timedelta(hours=2)
        async def interrupted(*args,**kwargs):
            self.app.inbox_event.set()
            return json.dumps({'choice':'check_index'})
        self.llm.generate=interrupted
        await self.app.pyramid.life.events(state,self.now,DecisionTrace())
        self.assertEqual(len(le.active(self.app.pyramid.load())['beats']),1)

    async def test_cancel_preserves_committed_event(self):
        state=self.setup_event();self.now+=timedelta(hours=2)
        async def cancelled(*args,**kwargs):raise asyncio.CancelledError()
        self.llm.generate=cancelled
        with self.assertRaises(asyncio.CancelledError):await self.app.pyramid.life.events(state,self.now,DecisionTrace())
        self.assertEqual(len(le.active(self.app.pyramid.load())['beats']),1)

    async def test_disabled_sleep_pending_and_image_do_not_generate(self):
        for mode in ('off','sleep','image'):
            state=self.setup_event();self.now+=timedelta(hours=2)
            self.store.set('story_events_enabled','false' if mode=='off' else 'true')
            if mode=='sleep':state['availability']='sleep'
            if mode=='pending':state['_pending_responses']=[{}]
            self.app.image_busy=mode=='image'
            await self.app.pyramid.life.events(state,self.now,DecisionTrace())
        self.assertEqual(self.llm.calls,[])

    async def test_failed_continuation_retries_bounded_without_losing_story(self):
        state=self.setup_event();self.now+=timedelta(hours=2)
        self.llm.generate=AsyncMock(side_effect=ServiceError('test failure'))
        for i in range(5):
            await self.app.pyramid.life.events(state,self.now,DecisionTrace());self.now+=timedelta(minutes=31)
        self.assertEqual(self.llm.generate.await_count,3)
        self.assertEqual(len(le.active(state)['beats']),1)

    async def test_writer_reads_committed_story_once_not_future_focus(self):
        state=self.setup_event();runner=PyramidRunner(self.llm,clock=lambda:NOW)
        # Use the actual decision builder to keep the Writer contract realistic.
        decision=self.app.pyramid.core.decide(state,[],observation(),DecisionTrace())
        decision['information_scope']='current_state'
        messages=await runner.prepare_writer(state,decision,'',DecisionTrace())
        payload=messages[0]['content']
        self.assertEqual(payload.count(le.TEMPLATES['reading_note']['opening']),1)
        self.assertNotIn(le.TEMPLATES['reading_note']['focus'],payload)
        self.assertNotIn(le.active(state)['id'],payload)

    async def test_retrieved_current_event_is_not_duplicated_in_writer(self):
        state=self.setup_event();runner=PyramidRunner(self.llm,clock=lambda:NOW)
        await runner.memory_search.select(state,'圖鑑',DecisionTrace())
        decision=self.app.pyramid.core.decide(state,[],observation(),DecisionTrace())
        decision['information_scope']='past_events'
        messages=await runner.prepare_writer(state,decision,'',DecisionTrace())
        self.assertEqual(messages[0]['content'].count(le.TEMPLATES['reading_note']['opening']),1)

    async def test_daily_plan_does_not_own_scene_event_drafts(self):
        state=self.setup_event();state.pop('life_events');state['life_plan_day']='previous-day'
        items=[{'start':start,'activity':'整理植物圖鑑','location':'青葉莊園書房','availability':'free',
                'mood':'平靜','outcome':'查閱資料','clothing':'襯衫','accessories':'無','arrival_activity':'','share_worthy':False}
               for start in ['00:00','08:00','10:00','16:00','22:00']]
        seed={'start':'10:00','template':'reading_note'}
        items[0]['availability']=items[-1]['availability']='sleep'
        self.llm.results=iter([json.dumps({'activities':items,'new_locations':[],'life_events':[seed]})])
        await self.app.pyramid.life.plan_day(state,self.now,DecisionTrace())
        self.assertEqual(len(le.ledger(state)['drafts']),0)
        self.assertEqual(le.ledger(state)['stories'],[])
        self.assertEqual(le.current_reading(state),'')
        self.assertEqual(len(self.llm.calls),1)

    async def test_command_and_settings_do_not_call_model_or_change_contact_rate(self):
        self.setup_event();before=self.store.get('proactive_cooldown_minutes')
        for value in ('off','fast','on','status'):
            await self.app.pyramid.command('/storyevents',value,42)
        self.assertEqual(self.store.get('story_event_pace'),'fast')
        self.assertEqual(self.store.get('proactive_cooldown_minutes'),before)
        self.assertIn('story_event_pace',FIELDS);self.assertEqual(self.llm.calls,[])

    async def test_reset_removes_authored_events(self):
        self.setup_event();self.store.reset()
        self.assertNotIn('life_events',self.app.pyramid.load())
