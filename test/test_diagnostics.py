"""Synthetic diagnostic persistence, failures and correlation. Never send to Telegram."""
import asyncio,json,logging,sqlite3,tempfile,unittest
from pathlib import Path
from dataclasses import replace
import httpx
from companion.diagnostics import Diagnostics,DiagnosticLogHandler,diagnosed,event,link,record_state
from companion.config import Config
from companion.services import LLM,ServiceError
from companion.pyramid import DecisionTrace,initial_state
from companion.context_plan import ContextPlanner
from test_pyramid import STAMP

class DiagnosticTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'diagnostics.sqlite3';self.archive=Diagnostics(self.path)
 def tearDown(self):
  try:self.archive.close()
  except Exception:pass
  self.tmp.cleanup()
 def events(self,kind):return [json.loads(r[0]) for r in self.archive.db.execute('SELECT data FROM events WHERE kind=?',(kind,))]
 def call(self):return dict(self.archive.db.execute('SELECT * FROM model_calls ORDER BY id DESC LIMIT 1').fetchone())
 async def llm(self,handler,count=100):
  class Counter(LLM):
   async def count_context(self,messages):return count
  self.client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
  self.addAsyncCleanup(self.client.aclose)
  llm=Counter(replace(Config.load(require_credentials=False),token='secret-token-not-a-prompt'),self.client)
  llm.diagnostics=self.archive
  return llm
 def stream(self):
  body='data: '+json.dumps({'choices':[{'delta':{'content':'測試回答。'},'finish_reason':'stop'}]})+'\n\ndata: '+json.dumps({'usage':{'completion_tokens':3},'choices':[]})+'\n\ndata: [DONE]\n\n'
  return httpx.Response(200,text=body)
 def test_trace_and_state_persist_without_debug_and_remain_out_of_state(self):
  state=initial_state(STAMP);original=json.dumps(state,ensure_ascii=False)
  with self.archive.run('telegram_input',{'update_id':12}) as rid:
   link('turn_id','tg:12');DecisionTrace().add(1,'test.stage',{'text':'原始輸入'},{'ok':True},'test')
   record_state(state,'full')
  self.assertEqual(self.events('trace')[0]['inputs']['text'],'原始輸入')
  self.assertEqual(self.events('state.saved')[0]['snapshot']['scene'],state['scene'])
  self.assertEqual(json.dumps(state,ensure_ascii=False),original)
  self.assertEqual(self.path.stat().st_mode&0o777,0o600)
  self.assertEqual(self.archive.db.execute('SELECT value FROM links WHERE run_id=?',(rid,)).fetchone()[0],'tg:12')
 async def test_model_records_actual_prepared_request_response_and_settings(self):
  captured=[]
  def handler(req):captured.append(json.loads(req.content));return self.stream()
  llm=await self.llm(handler,count=5000)
  request=[{'role':'user','content':'完整測試Prompt'}];self.assertEqual(await llm.generate(request,max_tokens=32),'測試回答。')
  row=self.call();self.assertEqual(json.loads(row['prepared'])['messages'],captured[0]['messages'])
  self.assertEqual(json.loads(row['requested'])['max_tokens'],32);self.assertEqual(row['output'],'測試回答。');self.assertEqual(row['status'],'completed')
  self.assertNotIn('secret-token-not-a-prompt',str(dict(row)))
 async def test_failed_http_request_is_not_disguised_as_success(self):
  llm=await self.llm(lambda _:httpx.Response(500,json={}))
  with self.assertRaises(ServiceError):await llm.generate([{'role':'user','content':'測試'}],max_tokens=32)
  row=self.call();self.assertEqual(row['status'],'failed');self.assertIsNotNone(row['prepared']);self.assertIsNone(row['output'])
  self.assertEqual(json.loads(row['error'])['type'],'ServiceError')
 async def test_partial_cancelled_output_and_cancellation_are_preserved(self):
  llm=await self.llm(lambda _:self.stream())
  async def cancelled(text):raise asyncio.CancelledError()
  with self.assertRaises(asyncio.CancelledError):await llm.generate([{'role':'user','content':'測試'}],on_text=cancelled,max_tokens=32)
  self.assertEqual(self.call()['status'],'cancelled');self.assertEqual(self.events('model.partial')[0]['raw'],'測試回答。')
 async def test_preflight_failure_has_intended_prompt_but_no_prepared_request(self):
  def handler(_):raise AssertionError('must not reach network')
  llm=await self.llm(handler,count=9000)
  with self.assertRaises(ServiceError):await llm.generate([{'role':'user','content':'不可丟失原文'}],max_tokens=32)
  row=self.call();self.assertIn('不可丟失',row['requested']);self.assertIsNone(row['prepared']);self.assertEqual(row['status'],'failed')
 async def test_planner_rejected_packet_is_archived_without_any_model_call(self):
  class Counter:
   async def count_context(self,messages):return 9000
  with self.archive.run('telegram_input'):
   with self.assertRaises(ServiceError):await ContextPlanner(Counter()).plan('user_observer',initial_state(STAMP),DecisionTrace(),
    lambda *a:[{'role':'user','content':'必要原文'}],1024,[],archive=False,memory=False)
  self.assertEqual(self.events('context.rejected_request')[0]['messages'][0]['content'],'必要原文')
  self.assertEqual(self.archive.db.execute('SELECT count(*) FROM model_calls').fetchone()[0],0)
 async def test_parallel_contexts_and_parent_links_do_not_mix(self):
  class Owner:
   diagnostics=self.archive
   @diagnosed('foreground')
   async def handle(inner,update_id,text):
    event('input',{'text':text});await asyncio.sleep(0)
    with self.archive.run('child'):event('child',{'text':text})
  await asyncio.gather(Owner().handle(1,'甲'),Owner().handle(2,'乙'))
  rows=self.archive.db.execute("SELECT run_id,data FROM events WHERE kind='input'").fetchall()
  self.assertEqual(len({r['run_id'] for r in rows}),2)
  self.assertEqual(self.archive.db.execute("SELECT count(*) FROM runs WHERE parent_id IS NOT NULL").fetchone()[0],2)
 def test_interrupted_work_survives_restart_and_import_is_idempotent(self):
  self.archive.execute("INSERT INTO runs(kind,started_at,status,metadata) VALUES('old','time','running','{}')")
  source=sqlite3.connect(':memory:');source.row_factory=sqlite3.Row
  source.executescript('CREATE TABLE pyramid_turns(update_id,status,trace,assistant_id);CREATE TABLE settings(key,value);')
  source.execute('INSERT INTO pyramid_turns VALUES(1,?,?,2)',('failed',json.dumps([{'rule':'model.failed','inputs':{'messages':[{'role':'user','content':'舊測試原文'}]},'output':{}}])))
  self.archive.recover_interrupted();self.archive.import_available(source);self.archive.import_available(source)
  self.assertEqual(self.archive.db.execute("SELECT count(*) FROM runs WHERE kind='legacy_trace'").fetchone()[0],1)
  self.assertEqual(self.archive.db.execute("SELECT status FROM runs WHERE kind='old'").fetchone()[0],'interrupted')
  self.assertIn('舊測試原文',str(self.events('legacy.trace')));source.close()
 def test_only_companion_application_logs_are_captured(self):
  handler=DiagnosticLogHandler(self.archive)
  handler.emit(logging.LogRecord('httpx',20,'',0,'do-not-copy-url',(),None))
  with self.archive.run('foreground'):handler.emit(logging.LogRecord('companion',40,'',0,'測試失敗',(),None))
  self.assertEqual(len(self.events('application.log')),1)
 async def test_report_reading_does_not_recursively_record_its_output(self):
  with self.archive.run('original') as rid:event('sample',{'text':'測試'})
  class Owner:
   diagnostics=self.archive
   @diagnosed('telegram_input')
   async def handle(inner,update_id,message):event('should_be_suppressed',{'text':self.archive.report(rid)})
  before=self.archive.db.execute('SELECT count(*) FROM runs').fetchone()[0]
  await Owner().handle(1,{'text':'/debug record '+str(rid)})
  self.assertEqual(self.archive.db.execute('SELECT count(*) FROM runs').fetchone()[0],before)
  self.assertFalse(self.events('should_be_suppressed'))
  reader=Diagnostics.readonly(self.path)
  try:self.assertIn('測試',reader.report(rid))
  finally:reader.close()
 async def test_boolean_telegram_receipt_keeps_typing_actions_working(self):
  from companion.services import Telegram
  async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'ok':True,'result':True}))) as client:
   tg=Telegram('synthetic-test-token',client);tg.diagnostics=self.archive
   result=await tg.call('sendChatAction',{'chat_id':1,'action':'typing'})
  self.assertIs(result,True)
  self.assertIsNone(self.events('telegram.receipt')[0]['message_id'])

 async def test_recording_does_not_change_pipeline_messages_or_add_model_calls(self):
  from companion.pyramid import TurnInput
  from companion.pyramid_runner import PyramidRunner
  from test_pyramid import FakeLLM,observation
  results=[]
  for enabled in (False,True):
   llm=FakeLLM([json.dumps(observation()),'你好。',json.dumps(observation())])
   if enabled:
    with self.archive.run('synthetic_pipeline'):
     result=await PyramidRunner(llm).run(initial_state(STAMP),TurnInput('你好','same',True))
   else:result=await PyramidRunner(llm).run(initial_state(STAMP),TurnInput('你好','same',True))
   results.append((llm.calls,result.reply,result.decision,result.after))
  self.assertEqual(results[0],results[1]);self.assertEqual(len(results[0][0]),3)

 async def test_diagnostic_write_failure_does_not_stop_generation(self):
  llm=await self.llm(lambda _:self.stream());self.archive.close()
  self.assertEqual(await llm.generate([{'role':'user','content':'繼續測試'}],max_tokens=32),'測試回答。')
  self.assertGreater(self.archive.errors,0)
