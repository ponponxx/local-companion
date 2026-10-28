"""Settled text bursts use one durable work item without losing source boundaries."""
import asyncio,json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from companion.store import Store,InboxWait
from companion.app import App
from companion.diagnostics import Diagnostics,diagnosed

class BatchTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'db.sqlite3';self.s=Store(self.path)
 def tearDown(self):self.s.close();self.tmp.cleanup()
 def put(self,uid,text,at=100,**extra):
  self.s.ingest({'update_id':uid,'message':{'message_id':uid+100,'date':at,'from':{'id':42},'chat':{'id':42,'type':'private'},'text':text,**extra}},42)
  with self.s.db:
   row=self.s.db.execute('select payload from inbox where update_id=?',(uid,)).fetchone();m=json.loads(row[0]);m['_enqueued_at']=at
   self.s.db.execute('update inbox set payload=? where update_id=?',(json.dumps(m),uid))
 def next(self,at):
  with patch('companion.store.datetime') as clock:
   clock.now.return_value=datetime.fromtimestamp(at,timezone.utc)
   return self.s.next_update(window_seconds=10)
 def states(self):return [tuple(r) for r in self.s.db.execute('select update_id,status from inbox order by update_id')]
 def test_last_message_resets_window_and_claims_together_at_ten_seconds(self):
  self.put(1,'早安',100);self.assertEqual(self.next(105),InboxWait(5));self.put(2,'今天還好嗎？',108)
  self.assertEqual(self.next(110),InboxWait(8));self.assertEqual(self.states(),[(1,'pending'),(2,'pending')])
  uid,m=self.next(118);self.assertEqual(uid,1);self.assertEqual(m['text'],'早安\n今天還好嗎？');self.assertEqual(m['_batch_ids'],[1,2]);self.assertEqual(self.states(),[(1,'running'),(2,'running')])
 def test_source_text_time_and_order_preserved_outside_model_text(self):
  self.put(1,'  第一行  ',100);self.put(2,'第二\n行',102)
  _,m=self.next(112);self.assertEqual(m['text'],'  第一行  \n第二\n行')
  self.assertEqual([p['text'] for p in m['_batch_sources']],['  第一行  ','第二\n行']);self.assertEqual([p['received_at'] for p in m['_batch_sources']],[100,102])
  self.assertNotIn('_enqueued_at',m);self.assertEqual(m['_queue_seconds'],12)
 def test_duplicate_updates_cannot_extend_deadline(self):
  self.put(1,'你好',100)
  self.s.ingest({'update_id':1,'message':{'from':{'id':42},'chat':{'id':42,'type':'private'},'text':'不同內容'}},42)
  _,m=self.next(110);self.assertEqual(m['text'],'你好')
 def test_command_flushes_preceding_chat_without_joining_or_reordering(self):
  self.put(1,'等等我',100);self.put(2,'/language EN',101);self.put(3,'再聊',102)
  self.assertEqual(self.next(103)[1]['text'],'等等我');self.assertEqual(self.next(103)[1]['text'],'/language EN')
  self.assertEqual(self.next(103),InboxWait(9))
 def test_callback_and_voice_are_immediate_boundaries(self):
  for extra in [{'voice':{'file_id':'test'}},{'_settings_callback':{'data':'cfg:test'}}]:
   with self.subTest(extra=extra):
    self.s.db.execute('delete from inbox');self.s.db.commit();self.put(1,'前一句',100);self.put(2,'',101,**extra)
    self.assertEqual(self.next(102)[1]['text'],'前一句');self.assertEqual(self.next(102)[0],2)
 def test_settings_values_are_never_debounced_or_combined(self):
  self.s.set('settings_ui:42',json.dumps({'pending':'trait.assertiveness'}));self.put(1,'87',100);self.put(2,'37',101)
  self.assertEqual(self.s.next_update_kind(),'control');self.assertEqual(self.next(102)[1]['text'],'87');self.assertEqual(self.next(102)[1]['text'],'37')
 def test_reply_to_retired_settings_prompt_is_not_chat_batch(self):
  self.s.set('settings_ui:42',json.dumps({'prompt_ids':[9]}));self.put(1,'87',100,reply_to_message={'message_id':9})
  self.assertEqual(self.next(101)[0],1)
 def test_separate_chats_never_merge(self):
  self.put(1,'甲',100);self.put(2,'乙',101,chat={'id':43,'type':'private'})
  self.assertEqual(self.next(102)[1]['text'],'甲');self.assertIsInstance(self.next(102),InboxWait)
 def test_size_limit_splits_without_dropping_pending_sources(self):
  self.put(1,'a'*2000,100);self.put(2,'b'*2000,101)
  self.assertEqual(len(self.next(102)[1]['text']),2000);self.assertEqual(self.states(),[(1,'running'),(2,'pending')])
 def test_new_arrivals_while_running_remain_a_different_batch(self):
  self.put(1,'第一輪',100);self.next(110);self.put(2,'第二輪',111);self.put(3,'補一句',112)
  self.assertEqual(self.next(122)[1]['_batch_ids'],[2,3])
 def test_wait_survives_restart_without_restarting_countdown(self):
  self.put(1,'先存起來',100);self.assertIsInstance(self.next(105),InboxWait)
  self.s.close();self.s=Store(self.path);self.assertEqual(self.s.recover(),[])
  self.assertEqual(self.next(110)[1]['text'],'先存起來')
 def test_claimed_batch_recovery_fails_once_per_chat_and_never_replays(self):
  self.put(1,'第一句',100);self.put(2,'第二句',101);self.next(111)
  self.assertEqual(self.s.recover(),[42]);self.assertEqual(self.states(),[(1,'failed'),(2,'failed')]);self.assertIsNone(self.next(120))
 def test_batch_terminal_status_and_payload_cleanup_are_all_or_nothing(self):
  for status in ['done','failed']:
   self.s.db.execute('delete from inbox');self.s.db.commit();self.put(1,'甲',100);self.put(2,'乙',101);_,m=self.next(111)
   self.s.finish_update(m['_batch_ids'],status);self.assertEqual(self.states(),[(1,status),(2,status)])
   self.assertEqual([r[0] for r in self.s.db.execute('select payload from inbox')],['{}','{}'])
 def test_legacy_unknown_queue_time_and_direct_callers_remain_immediate(self):
  self.put(1,'舊訊息',100)
  self.s.db.execute("update inbox set payload=? where update_id=1",(json.dumps({'text':'舊訊息','chat':{'id':42}}),));self.s.db.commit()
  self.assertEqual(self.next(101)[0],1)
  self.put(2,'直接呼叫',100);self.assertEqual(self.s.next_update()[0],2)

class WorkerTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'db.sqlite3')
 async def asyncTearDown(self):self.store.close();self.tmp.cleanup()
 async def test_one_handle_and_one_terminal_transaction_for_whole_burst(self):
  for uid,text in [(1,'第一行'),(2,'第二行')]:
   self.store.ingest({'update_id':uid,'message':{'from':{'id':42},'chat':{'id':42,'type':'private'},'text':text}},42)
  # Deterministic settled queue; do not spend real ten seconds in unit tests.
  with self.store.db:self.store.db.execute("update inbox set payload=replace(payload,'_enqueued_at','_unknown_time')")
  app=SimpleNamespace(store=self.store,inbox_event=asyncio.Event(),pyramid=None,busy=False,handle=AsyncMock(),notify=AsyncMock(),idle_summary=AsyncMock())
  original=self.store.next_update;calls=0
  def claim(**kwargs):
   nonlocal calls
   calls+=1
   if calls>1:raise asyncio.CancelledError()
   return original(**kwargs)
  with patch.object(self.store,'next_update',side_effect=claim):
   with self.assertRaises(asyncio.CancelledError):await App.inbox_worker(app)
  app.handle.assert_awaited_once();self.assertEqual(app.handle.await_args.args[1]['text'],'第一行\n第二行')
  self.assertEqual([r[0] for r in self.store.db.execute('select status from inbox')],['done','done'])
 async def test_waiting_uses_no_model_or_background_and_rechecks_on_new_input(self):
  app=SimpleNamespace(store=self.store,inbox_event=asyncio.Event(),pyramid=None,busy=False,handle=AsyncMock(),notify=AsyncMock(),idle_summary=AsyncMock())
  with patch.object(self.store,'next_update',side_effect=[InboxWait(.001),asyncio.CancelledError()]):
   with self.assertRaises(asyncio.CancelledError):await App.inbox_worker(app)
  app.handle.assert_not_called();app.idle_summary.assert_not_called()
 async def test_original_parts_are_archived_with_all_turn_links(self):
  archive=Diagnostics(Path(self.tmp.name)/'diagnostics.sqlite3')
  class Handler:
   diagnostics=archive
   @diagnosed('telegram_input')
   async def handle(self,update_id,message):return 'ok'
  parts=[{'update_id':1,'text':'甲'},{'update_id':2,'text':'乙'}]
  await Handler().handle(1,{'chat':{'id':42},'text':'甲\n乙','_batch_sources':parts})
  row=archive.db.execute('select metadata from runs').fetchone();self.assertEqual(json.loads(row[0])['input_parts'],parts)
  self.assertEqual(archive.db.execute("select count(*) from links where kind='turn_id'").fetchone()[0],2)
  archive.close()

class IntegrationTests(unittest.IsolatedAsyncioTestCase):
 import test_pyramid_telegram as fixtures
 asyncSetUp=fixtures.TelegramPyramidTests.asyncSetUp
 asyncTearDown=fixtures.TelegramPyramidTests.asyncTearDown
 results=fixtures.TelegramPyramidTests.results
 obs=fixtures.TelegramPyramidTests.obs
 async def test_burst_reaches_one_observer_writer_and_one_chat_source(self):
  self.results(self.obs(),'早安，我很好。')
  for uid,text in [(1,'早安'),(2,'今天好嗎？')]:
   self.store.ingest({'update_id':uid,'message':{'from':{'id':42},'chat':{'id':42,'type':'private'},'text':text}},42)
  with self.store.db:self.store.db.execute("update inbox set payload=replace(payload,'_enqueued_at','_unknown_time')")
  original=self.store.next_update;calls=0
  def claim(**kwargs):
   nonlocal calls
   calls+=1
   if calls>1:raise asyncio.CancelledError()
   return original(**kwargs)
  with patch.object(self.store,'next_update',side_effect=claim):
   with self.assertRaises(asyncio.CancelledError):await self.app.inbox_worker()
  self.assertEqual(len(self.llm.calls),2)
  state=json.loads(self.store.get('pyramid_state'));self.assertEqual([h['content'] for h in state['history'] if h['role']=='user'],['早安\n今天好嗎？'])
  self.assertEqual([r[0] for r in self.store.db.execute('select status from inbox')],['done','done'])
 async def test_failure_marks_all_members_failed_and_notifies_once(self):
  from companion.services import ServiceError
  for uid in [1,2]:self.store.ingest({'update_id':uid,'message':{'from':{'id':42},'chat':{'id':42,'type':'private'},'text':'中性測試'}},42)
  with self.store.db:self.store.db.execute("update inbox set payload=replace(payload,'_enqueued_at','_unknown_time')")
  original=self.store.next_update;calls=0
  def claim(**kwargs):
   nonlocal calls
   calls+=1
   if calls>1:raise asyncio.CancelledError()
   return original(**kwargs)
  app=SimpleNamespace(store=self.store,inbox_event=asyncio.Event(),pyramid=None,busy=False,handle=AsyncMock(side_effect=ServiceError('test')),notify=AsyncMock())
  with patch.object(self.store,'next_update',side_effect=claim):
   with self.assertRaises(asyncio.CancelledError):await App.inbox_worker(app)
  app.notify.assert_awaited_once();self.assertEqual([r[0] for r in self.store.db.execute('select status from inbox')],['failed','failed'])
