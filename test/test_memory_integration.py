"""Cross-boundary memory behavior: source ownership, conservative late commits and recall."""
import asyncio,json,unittest
from copy import deepcopy
from datetime import datetime,timedelta
from unittest.mock import AsyncMock
from companion.memory_sources import bind,readable,source_ref
from companion.memory_tasks import MemoryTasks,baseline,rebase
from companion.pyramid import initial_state,DecisionTrace
from companion.pyramid_memory import MemorySearch,catalogue
from companion.story_core import StoryCore
from companion.pyramid_runner import PyramidRunner
from test_pyramid import STAMP,observation,event
import test_pyramid_telegram as fixtures

class SourceTests(unittest.TestCase):
 def test_legacy_identity_survives_metadata_edits(self):
  s=initial_state(STAMP);s['history']=[{'role':'user','content':'我喝茶','at':STAMP}];bind(s);a=source_ref(s['history'][0]);s['history'][0]['context']={'scene':{'location':'花園'}};bind(s);self.assertEqual(source_ref(s['history'][0]),a)
 def test_exclusion_never_deletes_or_reinterprets_raw_sources(self):
  s=initial_state(STAMP);s['history']=[{'role':'user','content':'喜歡茉莉花茶','message_id':1},{'role':'user','content':'今天晴天','message_id':2},{'role':'user','content':'喜歡茉莉花茶','message_id':3}]
  s['memory_exclusions']=[{'text':'茉莉花茶','before_message_id':2,'before_index':1,'sources':[]}]
  v=readable(s);self.assertTrue(v['history'][0]['_memory_hidden']);self.assertEqual(v['history'][1]['content'],'今天晴天');self.assertEqual(v['history'][2]['content'],'喜歡茉莉花茶');self.assertEqual(s['history'][0]['content'],'喜歡茉莉花茶')
 def test_late_move_and_manual_clothing_do_not_override_new_state(self):
  s=initial_state(STAMP);s['_last_message_id']=10;p={'message_id':10,'baseline':baseline(s)}
  obs=observation([event('move',actor='character',target='character',field='location',value='花園',evidence='到了'),event('appearance',actor='character',target='character',field='top',value='藍色襯衫',evidence='穿好')])
  s['scene']['character_location']='圖書館';s['appearance']['clothing']['top']={'description':'紅色襯衫','worn':True};s['_clothing_edits']={'top':STAMP}
  result=rebase(obs,p,s,DecisionTrace());self.assertFalse(result['events']);self.assertTrue(result['_late_annotation'])
 def test_late_analysis_can_fill_unchanged_domain_but_not_replay_affect(self):
  s=initial_state(STAMP);s['_last_message_id']=1;p={'message_id':1,'baseline':baseline(s)};s['_last_message_id']=2
  obs=observation([event('move',actor='character',target='character',field='location',value='花園',evidence='到了')]);obs['appraisals']=[{'need':'relatedness'}]
  result=rebase(obs,p,s,DecisionTrace());self.assertEqual(len(result['events']),1);self.assertFalse(result['appraisals'])

class IntegratedTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=fixtures.TelegramPyramidTests.asyncSetUp
 asyncTearDown=fixtures.TelegramPyramidTests.asyncTearDown
 async def test_forget_preserves_other_transcript_pending_and_diagnostic_trace(self):
  old=self.store.remember('茉莉花茶');self.store.add_message('user','我喜歡茉莉花茶');self.store.add_message('user','今天晴天')
  s=self.app.pyramid.load();s['_pending_responses']=[{'turn_id':'parked','reply':'午安','at':STAMP,'background_attempts':3}];self.app.pyramid.save_clock(s);self.store.set('last_story_background_trace','trace')
  self.assertTrue(self.store.forget(old));after=self.app.pyramid.load();self.assertEqual(len(after['history']),2);self.assertEqual(len(after['_pending_responses']),1);self.assertEqual(self.store.get('last_story_background_trace'),'trace')
  self.assertFalse(any('茉莉花茶' in d['text'] for d in catalogue(after)));self.assertTrue(any('晴天' in d['text'] for d in catalogue(after)))
 async def test_stalled_head_does_not_starve_later_annotation(self):
  s=self.app.pyramid.load();s['_pending_responses']=[{'turn_id':'old','reply':'舊','at':STAMP,'background_attempts':3},{'turn_id':'new','reply':'新','at':STAMP}];self.app.pyramid.save_clock(s)
  tasks=MemoryTasks(self.store.db);self.assertEqual(tasks.eligible(s['_pending_responses'],datetime.fromisoformat(STAMP))['turn_id'],'new')
 async def test_retry_budget_persists_and_cancellation_does_not_consume_it(self):
  t=MemoryTasks(self.store.db);p={'turn_id':'a','at':STAMP,'reply':'午安'};now=datetime.fromisoformat(STAMP);t.sync([p])
  for _ in range(3):t.fail(p,now,'失敗');now+=timedelta(minutes=3)
  restored=MemoryTasks(self.store.db);self.assertIsNone(restored.eligible([p],now));self.assertEqual(restored.overview()['stalled'],1)
 async def test_foreground_search_never_encodes_missing_documents(self):
  e=AsyncMock();e.process=type('Process',(),{'returncode':None})();e.encode.return_value=[[1.,0.]]
  s=initial_state(STAMP);s['history']=[{'role':'user','content':'茉莉花茶','at':STAMP}];search=MemorySearch(e,foreground=True)
  result=await search.select(s,'茉莉花茶');self.assertTrue(result['items']);self.assertTrue(all(c.kwargs.get('prompt_name')!='Retrieval-document' for c in e.encode.await_args_list))
 async def test_actual_snapshot_source_edit_invalidates_old_annotation_text(self):
  mid=self.store.add_message('user','我喝紅茶');s=self.app.pyramid.load();self.app.pyramid.save_clock(s)
  with self.store.db:self.store.db.execute('UPDATE messages SET content=? WHERE id=?',('我喝綠茶',mid))
  loaded=self.app.pyramid.load();self.assertEqual(loaded['history'][0]['content'],'我喝綠茶');self.assertEqual(loaded['history'][0]['source_ref'],'message:'+str(mid))
 async def test_writer_packet_reuses_recent_source_instead_of_repeating_it(self):
  from companion.pyramid import PyramidCore
  from test_pyramid import FakeLLM
  s=initial_state(STAMP);s['history']=[{'role':'user','content':'昨天我在家喝茉莉花茶','at':STAMP},{'role':'assistant','content':'我記住了。','at':STAMP},{'role':'user','content':'昨天喝什麼？','at':STAMP}]
  await StoryCore().query(s,'昨天喝什麼',MemorySearch(),DecisionTrace());d=PyramidCore().decide(s,[],observation(),DecisionTrace());d['information_scope']='past_events';trace=DecisionTrace()
  messages=await PyramidRunner(FakeLLM([])).prepare_writer(s,d,'規則\n',trace)
  self.assertEqual(str(messages).count('昨天我在家喝茉莉花茶'),1);self.assertFalse(any(m['content'].startswith('【系統提供的當前互動記憶，') for m in messages));self.assertTrue(any(n['rule']=='memory.packet' for n in trace.nodes))

class ForgottenTaskTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=fixtures.TelegramPyramidTests.asyncSetUp
 asyncTearDown=fixtures.TelegramPyramidTests.asyncTearDown
 async def test_forgotten_pending_source_never_reenters_model(self):
  from test_pyramid import FakeLLM
  m=self.store.remember('秘密茶葉');mid=self.store.add_message('assistant','我收到秘密茶葉了。')
  s=self.app.pyramid.load();s['history'][-1]['turn_id']='test-secret';s['_pending_responses']=[{'turn_id':'test-secret','reply':'我收到秘密茶葉了。','message_id':mid,'at':STAMP}];self.app.pyramid.save_clock(s)
  self.store.forget(m);llm=FakeLLM([]);state=self.app.pyramid.load()
  self.assertTrue(await self.app.pyramid.recover_response(state,PyramidRunner(llm),DecisionTrace()))
  self.assertFalse(llm.calls);self.assertFalse(state['_pending_responses']);self.assertEqual(self.store.db.execute('select count(*) from messages').fetchone()[0],1)

 async def test_forgotten_user_source_also_disables_its_derived_pending_reply(self):
  from test_pyramid import FakeLLM
  m=self.store.remember('秘密茶葉');uid=self.store.add_message('user','我喜歡秘密茶葉');aid=self.store.add_message('assistant','我會記住你的偏好。')
  s=self.app.pyramid.load()
  for h in s['history']:h['turn_id']='same-turn'
  s['_pending_responses']=[{'turn_id':'same-turn','reply':'我會記住你的偏好。','message_id':aid,'at':STAMP}];self.app.pyramid.save_clock(s)
  self.store.forget(m);state=self.app.pyramid.load();llm=FakeLLM([])
  self.assertTrue(await self.app.pyramid.recover_response(state,PyramidRunner(llm),DecisionTrace()))
  self.assertFalse(llm.calls);self.assertFalse(state['_pending_responses']);self.assertEqual(self.store.db.execute('select count(*) from messages').fetchone()[0],2)

class LiteralProfileTests(unittest.TestCase):
 def test_explicit_self_description_and_correction_use_existing_reducer(self):
  from companion.memory_literals import profile_proposals
  from companion.pyramid import PyramidCore
  s=initial_state(STAMP);core=PyramidCore()
  for i,text in enumerate(['我叫阿哲，請用這個名字稱呼我。','我喜歡綠茶。','更正一下，我現在喜歡紅茶，不是綠茶。']):
   proposals=profile_proposals(text,s);self.assertTrue(proposals);core.apply(s,proposals,'user',text,'literal:'+str(i),DecisionTrace())
  self.assertTrue(any(p['category']=='name' and p['value']=='阿哲' for p in s['user_profile']))
  self.assertEqual([p['value'] for p in s['user_profile'] if p['category']=='preference'],['紅茶'])
 def test_quoted_third_party_hypothetical_past_and_secrets_not_promoted(self):
  from companion.memory_literals import profile_proposals
  s=initial_state(STAMP)
  for text in ['他說我叫小明。','如果我喜歡紅茶會如何？','昨天我喝了綠茶。','我喜歡的密碼是secret','我不喜歡紅茶。']:
   self.assertFalse(profile_proposals(text,s),text)

class DerivedProfileExclusionTests(unittest.TestCase):
 def test_forgotten_source_hides_derived_profile_without_erasing_independent_fact(self):
  s=initial_state(STAMP);s['history']=[{'role':'user','content':'我喜歡茉莉花茶','turn_id':'a','message_id':1}]
  s['events']=[{'id':'e1','turn_id':'a','source':'user'}]
  s['user_profile']=[{'id':'1','category':'preference','value':'茉莉花茶','source':'e1'},{'id':'2','category':'name','value':'阿哲','source':'manual'}]
  s['memory_exclusions']=[{'text':'茉莉花茶','sources':[],'before_message_id':1,'before_index':0}]
  self.assertEqual([f['value'] for f in readable(s)['user_profile']],['阿哲']);self.assertEqual(len(s['user_profile']),2)

class ProfileReadingTests(unittest.TestCase):
 def test_writer_profile_has_explicit_user_owner_and_recompiles_without_duplicate(self):
  from companion.pyramid_runner import writer_contract
  from companion.writer_context import finish_contract
  from companion.pyramid import PyramidCore
  s=initial_state(STAMP);s['user_profile']=[{'category':'name','value':'阿哲'},{'category':'preference','value':'紅茶'}]
  d=PyramidCore().decide(s,[],observation(),DecisionTrace());d['information_scope']='interaction'
  c=writer_contract(s,d);self.assertEqual(c['user_profile'],'使用者的個人資料：\n偏好：紅茶')
  self.assertEqual(finish_contract(c,s)['user_profile'],c['user_profile']);self.assertEqual(c['identity']['user'],'阿哲')

class SceneReadingTests(unittest.TestCase):
 def test_unknown_user_location_is_not_proof_of_separation(self):
  from companion.writer_context import scene_facts
  for value in ['', '未確認', 'unknown']:
   self.assertNotIn('interaction_setting',scene_facts({'character_location':'溫室','user_location':value}))
  self.assertIn('interaction_setting',scene_facts({'character_location':'溫室','user_location':'書房'}))

class InvalidSourceTests(unittest.TestCase):
 def test_unavailable_canonical_source_cannot_reenter_through_derived_note(self):
  s=initial_state(STAMP);s['history']=[{'role':'user','content':'我喜歡紅茶','_memory_hidden':True,'turn_id':'a'}]
  s['memory']['notes']=[{'id':'old','source_index':0,'actor':'user','text':'使用者喜歡紅茶','evidence':'紅茶','at':STAMP,'important':True,'stage':'observed'}]
  s['events']=[{'id':'e','turn_id':'a','source':'user'}];s['user_profile']=[{'category':'preference','value':'紅茶','source':'e'}]
  self.assertFalse(readable(s)['user_profile']);self.assertFalse(catalogue(s));self.assertTrue(s['history'][0]['content'])

class QuestionProfileTests(unittest.TestCase):
 def test_questions_do_not_become_profile_values_but_declarations_still_work(self):
  from companion.memory_literals import profile_proposals
  s=initial_state(STAMP)
  for text in ['我現在最喜歡喝什麼？','我喜歡哪種茶？','我喜歡紅茶嗎？','我叫什麼，請用這個名字稱呼我？']:
   self.assertFalse(profile_proposals(text,s),text)
  self.assertEqual(profile_proposals('我喜歡紅茶，妳呢？',s)[0]['value'],'紅茶')
