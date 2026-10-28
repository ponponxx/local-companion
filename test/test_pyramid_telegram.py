import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock
from companion.app import App
from companion.config import Config
from companion.store import Store
from companion.pyramid_import import import_legacy_state
from companion.services import ServiceError
from test_pyramid import FakeLLM, observation, event


class TelegramPyramidTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        self.store=Store(self.path/'memory.sqlite3')
        self.config=replace(Config.load(require_credentials=False),chat_core='pyramid',data_dir=self.path,router_mode='off')
        self.tg=AsyncMock();self.images=AsyncMock();self.images.installed.return_value=False
        self.llm=FakeLLM([])
        self.app=App(self.config,self.store,self.tg,self.llm,AsyncMock(),self.images)
        self.app.pyramid.search.embedder=None
        self.app.pyramid.external.fetch=AsyncMock(return_value=b'<rss><channel/></rss>')

    async def asyncTearDown(self):
        await self.app.pyramid.embedder.close();self.store.close();self.temp.cleanup()

    def results(self,*values):self.llm.results=iter(values)
    def obs(self,*events):return json.dumps(observation(list(events)),ensure_ascii=False)
    def state(self):return json.loads(self.store.get('pyramid_state'))
    async def drain(self):
        from companion.pyramid import DecisionTrace
        from companion.pyramid_runner import PyramidRunner
        state=self.app.pyramid.load()
        await self.app.pyramid.recover_response(state,PyramidRunner(self.app.llm,self.app.pyramid.core,self.app.pyramid.search,clock=self.app.now),DecisionTrace())

    async def text(self,text,uid):
        await self.app.handle(uid,{'chat':{'id':42},'text':text})
        await TelegramPyramidTests.drain(self)  # Simulated idle worker, separate from foreground delivery.

    async def test_retired_harness_setting_cannot_enable_prompt_search_or_external_review(self):
        self.store.set('harness_observer_enabled','true')
        self.store.set('harness_failure_policy','continue')
        self.app.gpt_reviewer=AsyncMock()
        self.results(self.obs(),'你好。',self.obs())
        await self.text('你好',901)
        self.assertEqual(len(self.llm.calls),3)  # front Observer, Writer, idle response Observer
        self.assertFalse(self.app.gpt_reviewer.mock_calls)
        trace=json.loads(self.store.get('last_pyramid_trace'))
        self.assertFalse(any(n['rule'].startswith('harness.') for n in trace))
        self.assertTrue(all('<harness_hint>' not in m['content'] for call in self.llm.calls for m in call))
        sent=[c.args[1] for c in self.tg.send.await_args_list]
        self.assertTrue(any(s.endswith('你好。') for s in sent))
        self.assertFalse(any('系統檢查' in s or 'Prompt搜尋' in s for s in sent))

    async def test_retired_harness_command_cannot_reenable_search(self):
        self.assertFalse(await self.app.pyramid.command('/harness','on',42))
        self.assertFalse(self.store.get('harness_observer_enabled'))
        self.assertFalse(self.llm.calls)

    async def test_two_turns_persist_raw_context_profile_and_exact_writer_prompt(self):
        self.results(self.obs(event('profile_add',target='user',field='preference',value='茉莉花茶',evidence='我喜歡茉莉花茶。')),
                     '我記住了。',self.obs(),self.obs(event('question',evidence='我喜歡什麼茶？')),'茉莉花茶。',self.obs())
        await self.text('我喜歡茉莉花茶。',1)
        first=self.state();self.assertEqual(len(first['history']),2)
        self.assertEqual(len(first['user_profile']),1)
        self.assertEqual(len(self.store.profile_facts()),1)
        await self.text('我喜歡什麼茶？',2)
        self.assertEqual(len(self.state()['history']),4)
        self.assertIn('我喜歡茉莉花茶。',[m['content'] for m in self.llm.calls[4]])
        self.assertEqual(json.loads(self.store.get('last_text_prompt')),self.llm.calls[4])
        self.assertEqual(self.store.db.execute('SELECT status FROM pyramid_turns WHERE update_id=2').fetchone()[0],'done')
        self.assertTrue(any(c.args[0]==42 and c.args[1].startswith('【目前狀態｜系統紀錄】') and c.args[1].endswith('\n\n茉莉花茶。') for c in self.tg.send.await_args_list))
        self.assertTrue(all('【目前狀態' not in m['content'] for m in self.state()['history']))
        self.assertTrue(all('【目前狀態' not in m['content'] for m in self.llm.calls[4]))
        self.assertIsNone(self.app.router)

    async def test_actual_delivery_precedes_response_analysis_and_failure_keeps_reply(self):
        owner=self
        class CheckedLLM(FakeLLM):
            async def generate(self,messages,**kwargs):
                if len(self.calls)==2:
                    owner.tg.send.assert_awaited_once()
                    owner.assertTrue(owner.tg.send.await_args.args[1].endswith('\n\n回答。'))
                return await super().generate(messages,**kwargs)
        self.app.llm=CheckedLLM([self.obs(), '回答。','invalid json'])
        await self.text('你好',1)
        state=self.state()
        self.assertEqual([m['content'] for m in state['history']],['你好','回答。'])
        self.assertEqual(len(state['_pending_responses']),1)
        self.assertEqual(self.store.db.execute('SELECT status FROM pyramid_turns').fetchone()[0],'delivered_pending_analysis')
        self.assertEqual(self.store.last_delivered_exchange()['assistant'],'回答。')
        # Recover the delivered reply, then process a new turn without resending it.
        self.app.llm=FakeLLM([self.obs(),self.obs(),'新回答。',self.obs()])
        await TelegramPyramidTests.drain(self)
        await self.text('再聊一下',2)
        self.assertFalse(self.state()['_pending_responses'])
        self.assertEqual(len(self.state()['history']),4)
        self.assertEqual(sum(c.args[0]==42 and c.args[1].endswith('\n\n回答。') for c in self.tg.send.await_args_list),1)

    async def test_failed_send_does_not_commit_character_or_prompt(self):
        self.results(self.obs(),'回答。')
        self.tg.send.side_effect=ServiceError('send_failed')
        with self.assertRaises(ServiceError):await self.text('你好',1)
        self.assertEqual([m['role'] for m in self.state()['history']],['user'])
        self.assertFalse(self.store.get('last_prompt'))
        self.assertIsNone(self.store.last_delivered_exchange())

    async def test_admin_controls_and_reset_do_not_resurrect_old_memory(self):
        self.results(self.obs(),'記得。',self.obs())
        await self.text('你好',1)
        await self.text('/language EN',2)
        await self.text('/setrelation trust 87',3)
        state=self.app.pyramid.load()
        self.assertEqual(state['language'],'EN');self.assertEqual(state['relationship']['trust'],87)
        await self.text('/remember 旅行暗號是橘子',4)
        state=self.app.pyramid.load()
        self.assertTrue(state['manual_memories'])
        await self.text('/reset',5)
        self.assertFalse(self.store.get('pyramid_state'))
        self.assertFalse(self.app.pyramid.load()['history'])
        await self.text('/forget 1',6)
        self.assertFalse(self.app.pyramid.load()['manual_memories'])

    async def test_old_background_writers_are_not_called(self):
        self.app.life_tick=AsyncMock();self.app.analyze_relationship=AsyncMock();self.app.summarize=AsyncMock()
        self.app.pyramid.life.tick=AsyncMock()
        await self.app.maintenance()
        self.app.pyramid.life.tick.assert_awaited_once()
        self.app.life_tick.assert_not_called();self.app.analyze_relationship.assert_not_called();self.app.summarize.assert_not_called()

    async def test_image_snapshot_reads_new_hair_clothes_and_location(self):
        state=self.app.pyramid.load();state['appearance']['hair']='盤髮';state['appearance']['clothing']={'socks':'未穿著'}
        state['scene'].update(status='active',location='書房');self.app.pyramid.save(state)
        snap=self.app.visual_snapshot()
        self.assertEqual(snap['scene_facts']['facts']['character.hair'],'盤髮')
        self.assertIn('未穿著',snap['current_clothing']);self.assertEqual(snap['current_scene'],'書房')

    async def test_full_legacy_import_keeps_archived_originals_and_summary_cursor(self):
        for text in ['很久以前的細節','回覆','近期訊息']:self.store.add_message('user',text)
        self.store.save_summary('舊摘要',2)
        imported=import_legacy_state(self.store.db_path,full_history=True)
        self.assertEqual(len(imported['history']),3);self.assertEqual(imported['summary_through'],2)
        self.assertEqual(imported['legacy_summary'],'舊摘要')
        self.assertEqual(imported['history'][0]['content'],'很久以前的細節')

    async def test_photo_turn_is_transient_but_appearance_and_tool_context_survive(self):
        from companion.pyramid_runner import model_view
        greeting='午安';request='我幫妳盤髮完成了，拍一張照片給我看。';reply='好，我拍給你看。'
        self.app.enqueue_visual_from_chat=AsyncMock()
        self.results(self.obs(),greeting,self.obs(),
                     self.obs(event('image_request',evidence=request),
                              event('appearance',actor='character',field='hair',value='盤髮',evidence='盤髮完成了'),
                              event('disclosure',evidence=request)),reply,self.obs(),
                     self.obs(),'晚上好。',self.obs())
        await self.text('你好',1)
        await self.text(request,2)
        state=self.state()
        self.assertEqual([m['content'] for m in state['history']],['你好',greeting])
        self.assertEqual(state['appearance']['hair'],'盤髮')
        self.assertFalse(model_view(state)['recent_disclosures'])
        self.assertFalse(json.loads(self.store.get('last_strategy'))['memory_retained'])
        self.assertIn(request,[m['content'] for m in json.loads(self.store.get('last_text_prompt'))])
        self.assertIn(request,[m['content'] for m in self.store.recent(include_tools=True)])
        self.assertNotIn(request,[m['content'] for m in self.store.recent()])
        await self.text('晚上好',3)
        current_observer=json.loads(self.llm.calls[6][1]['content'])
        self.assertNotIn(request,[m['content'] for m in current_observer['recent_dialogue']])
        self.assertNotIn(reply,[m['content'] for m in self.llm.calls[7]])
        imported=import_legacy_state(self.store.db_path,full_history=True)
        self.assertNotIn(request,[m['content'] for m in imported['history']])
        scopes=[r[0] for r in self.store.db.execute('SELECT memory_scope FROM messages ORDER BY id')]
        self.assertEqual(scopes,['chat','chat','tool','tool','chat','chat'])
        from companion.pyramid_memory import catalogue
        self.assertFalse(any(request in d['text'] or reply in d['text'] for d in catalogue(self.state())))

    async def test_model_only_image_classification_excludes_both_messages(self):
        self.results(self.obs(event('image_request',evidence='再一張')), '我拍給你。', self.obs())
        _,decision=await self.app.pyramid.handle(1,42,'再一張',photo_request=False)
        self.assertFalse(decision['memory_retained'])
        self.assertFalse(self.state()['history'])
        self.assertFalse(self.store.recent_since_summary())
        self.assertEqual([r[0] for r in self.store.db.execute('SELECT memory_scope FROM messages')],['tool','tool'])
        # Unseen tool messages cannot re-enter through the next-load catch-up.
        self.store.add_message('user','快門給妳按',memory_scope='tool')
        self.assertFalse(self.app.pyramid.load()['history'])

    async def test_photo_response_recovery_uses_transient_request_without_remembering_it(self):
        request='再拍一張';self.results(self.obs(event('image_request',evidence=request)), '我來拍。','invalid json')
        await self.app.pyramid.handle(1,42,request,photo_request=True)
        self.assertFalse(self.state()['history'])
        self.assertFalse(self.state()['_pending_responses'][0]['retain_memory'])
        self.results(self.obs(),self.obs(),'午安。',self.obs())
        await TelegramPyramidTests.drain(self)
        await self.text('午安',2)
        recovery=json.loads(self.llm.calls[3][-1]['content'])
        self.assertEqual(request,recovery['response_context']['request'])
        following=json.loads(self.llm.calls[4][1]['content'])
        self.assertNotIn(request,[m['content'] for m in following['recent_dialogue']])
        self.assertEqual([m['content'] for m in self.state()['history']],['午安','午安。'])

    async def test_photo_tool_rows_never_fill_legacy_summary_window(self):
        for i in range(8): self.store.add_message('user','一般聊天'+str(i))
        for i in range(20): self.store.add_message('user','拍照要求'+str(i),memory_scope='tool')
        self.assertFalse(self.store.summary_batch())
        self.assertEqual(len(self.store.recent_since_summary()),8)
