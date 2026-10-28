"""Foreground delivery, deferred response ownership and dependency ordering."""
import asyncio
from copy import deepcopy
import json
import unittest
from unittest.mock import AsyncMock
import test_pyramid_telegram as fixtures
from test_pyramid import FakeLLM, observation, event, STAMP
from companion.pyramid import DecisionTrace, initial_state
from companion.pyramid_runner import PyramidRunner, RESPONSE_OBSERVATION_SCHEMA, validate_output


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.TelegramPyramidTests.asyncSetUp
    asyncTearDown=fixtures.TelegramPyramidTests.asyncTearDown
    results=fixtures.TelegramPyramidTests.results
    obs=fixtures.TelegramPyramidTests.obs
    state=fixtures.TelegramPyramidTests.state

    async def foreground(self,text='你好',uid=1):
        return await self.app.pyramid.handle(uid,42,text)

    async def test_foreground_has_two_calls_and_durable_story_source_before_return(self):
        self.results(self.obs(),'午安。')
        await self.foreground()
        self.assertEqual(len(self.llm.calls),2)
        self.assertEqual([h['content'] for h in self.state()['history']],['你好','午安。'])
        self.assertEqual(len(self.state()['_pending_responses']),1)
        self.assertEqual(self.store.last_delivered_exchange()['assistant'],'午安。')
        self.assertTrue(any(n['rule']=='story.prepared' for n in json.loads(self.store.get('last_pyramid_trace'))))

    async def test_settings_do_not_wait_for_pending_model_work(self):
        self.results(self.obs(),'午安。')
        await self.foreground()
        await self.app.handle(2,{'chat':{'id':42},'text':'/language EN'})
        self.assertEqual(len(self.llm.calls),2)
        self.assertEqual(self.app.pyramid.load()['language'],'EN')
        self.assertTrue(self.state()['_pending_responses'])

    async def test_new_chat_uses_confirmed_state_without_waiting_for_old_annotation(self):
        self.results(self.obs(),'我把頭髮綁成馬尾了。')
        await self.foreground()
        before=self.state()['appearance']['hair']
        self.results(self.obs(),'謝謝。')
        await self.foreground('新髮型很好看',2)
        payload=json.loads(self.llm.calls[2][1]['content'])
        self.assertEqual(payload['snapshot']['appearance']['hair'],before)
        self.assertEqual(len(self.llm.calls),4)
        self.assertEqual(len(self.state()['_pending_responses']),2)
        self.assertIn('尚待事實分析',str(self.llm.calls[3]))

    async def test_failed_annotation_does_not_block_new_decision_or_resend(self):
        self.results(self.obs(),'午安。');await self.foreground()
        self.results('broken json')
        self.assertFalse(await self.app.pyramid.recover_response(self.app.pyramid.load(),PyramidRunner(self.llm),DecisionTrace()))
        self.results(self.obs(),'我們繼續聊。')
        await self.foreground('繼續',2)
        self.assertEqual(len(self.state()['history']),4)
        self.assertEqual(self.tg.send.await_count,2)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM messages WHERE content='繼續'").fetchone()[0],1)

    async def test_cancelled_idle_response_keeps_checkpoint_and_no_partial_state(self):
        self.results(self.obs(),'午安。')
        await self.foreground()
        self.llm.healthy=AsyncMock(return_value=True)
        entered=asyncio.Event()
        async def generate(*args,**kwargs):
            entered.set();await asyncio.Event().wait()
        self.llm.generate=generate
        task=asyncio.create_task(self.app.idle_summary(response_only=True))
        await asyncio.wait_for(entered.wait(),1)
        self.app.inbox_event.set()
        await asyncio.wait_for(task,1)
        self.assertEqual(len(self.state()['_pending_responses']),1)
        self.assertEqual(len(self.state()['history']),2)
        self.assertEqual(self.tg.send.await_count,1)

    async def test_late_background_result_cannot_overwrite_newer_state(self):
        self.results(self.obs(),'午安。')
        await self.foreground()
        self.llm.healthy=AsyncMock(return_value=True)
        original_generate=self.llm.generate
        self.results(self.obs())
        async def generate(*args,**kwargs):
            newer=self.app.pyramid.load();newer['activity']='新的活動'
            self.app.pyramid.save_clock(newer)
            return await original_generate(*args,**kwargs)
        self.llm.generate=generate
        await self.app.pyramid.maintain_responses()
        self.assertEqual(self.state()['activity'],'新的活動')
        self.assertFalse(self.state()['_pending_responses'])

    async def test_new_chat_cancels_background_and_retains_annotation_task(self):
        self.results(self.obs(),'午安。');await self.foreground()
        self.llm.healthy=AsyncMock(return_value=True)
        entered=asyncio.Event();release=asyncio.Event();cancelled=[]
        original=self.llm.generate;self.results(self.obs())
        async def generate(*args,**kwargs):
            entered.set()
            try:await release.wait()
            except asyncio.CancelledError:cancelled.append(True);raise
            return await original(*args,**kwargs)
        self.llm.generate=generate
        task=asyncio.create_task(self.app.idle_summary(response_only=True))
        await asyncio.wait_for(entered.wait(),1)
        self.store.ingest({'update_id':2,'message':{'from':{'id':42},'chat':{'id':42,'type':'private'},'text':'繼續聊'}},42)
        self.app.inbox_event.set();await asyncio.wait_for(task,1)
        self.assertTrue(cancelled)
        self.assertEqual(len(self.state()['_pending_responses']),1)
        self.assertEqual(len(self.llm.calls),2)

    async def test_idle_completion_is_idempotent_and_keeps_story(self):
        self.results(self.obs(),'我正在讀書。')
        await self.foreground()
        self.llm.healthy=AsyncMock(return_value=True)
        self.results(json.dumps(observation(story_events=[{'actor':'character','target':'none','action':'讀書','stage':'ongoing','evidence':'正在讀書'}])))
        await self.app.pyramid.maintain_responses()
        first=deepcopy(self.state())
        await self.app.pyramid.maintain_responses()
        self.assertEqual(self.state(),first)
        self.assertFalse(first['_pending_responses'])
        self.assertEqual(len(self.llm.calls),3)
        self.assertEqual(len(first['history']),2)
        self.assertEqual(self.store.db.execute('SELECT status FROM pyramid_turns').fetchone()[0],'done')
        self.assertTrue(first['history'][-1]['_story']['events'])
        trace=json.loads(self.store.get('last_pyramid_trace'))
        self.assertTrue(any(n['rule']=='telegram.response_recovered' for n in trace))

    async def test_background_move_keeps_story_digest_valid(self):
        from companion.story_events import claims
        self.results(self.obs(),'我走到花園了。')
        await self.foreground()
        self.llm.healthy=AsyncMock(return_value=True)
        self.results(json.dumps(observation([event('move',actor='character',target='character',field='location',value='花園',evidence='我走到花園了')],
            story_events=[{'actor':'character','target':'none','action':'走到花園','stage':'completed','evidence':'我走到花園了'}])))
        await self.app.pyramid.maintain_responses()
        self.assertTrue(claims(self.state()['history'][-1]))

    async def test_pending_response_does_not_block_independent_life(self):
        self.results(self.obs(),'午安。');await self.foreground()
        self.llm.healthy=AsyncMock(return_value=False)
        self.app.pyramid.life.tick=AsyncMock()
        await self.app.maintenance()
        self.app.pyramid.life.tick.assert_awaited_once()

    def test_queue_timestamp_is_diagnostic_and_payload_is_erased_on_finish(self):
        self.store.ingest({'update_id':50,'message':{'from':{'id':42},'chat':{'id':42,'type':'private'},'text':'你好'}},42)
        uid,message=self.store.next_update()
        self.assertEqual(uid,50);self.assertGreaterEqual(message['_queue_seconds'],0)
        self.assertNotIn('_enqueued_at',message)
        self.store.finish_update(uid)
        self.assertEqual(self.store.db.execute('SELECT payload FROM inbox WHERE update_id=50').fetchone()[0],'{}')


class ResponseContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_response_request_keeps_events_appraisal_story_but_no_user_analysis(self):
        llm=FakeLLM([json.dumps(observation())]);runner=PyramidRunner(llm)
        result=await runner.observe(initial_state(STAMP),'午安','assistant',DecisionTrace())
        self.assertEqual(result['user_affect']['status'],'unknown')
        self.assertNotIn('user_affect',RESPONSE_OBSERVATION_SCHEMA['properties'])
        self.assertIn('story_events',RESPONSE_OBSERVATION_SCHEMA['properties'])
        self.assertIn('appraisals',RESPONSE_OBSERVATION_SCHEMA['properties'])
        self.assertNotIn('profile_add',llm.calls[0][0]['content'])

    def test_real_validator_rejects_unknown_fields_and_missing_required(self):
        body={k:v for k,v in observation().items() if k in RESPONSE_OBSERVATION_SCHEMA['properties']}
        validate_output(body,RESPONSE_OBSERVATION_SCHEMA)
        with self.assertRaises(ValueError):validate_output({**body,'user_affect':{}},RESPONSE_OBSERVATION_SCHEMA)
        body.pop('events')
        with self.assertRaises(ValueError):validate_output(body,RESPONSE_OBSERVATION_SCHEMA)
