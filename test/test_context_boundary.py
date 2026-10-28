"""Conversation position and historical evidence are separate channels."""
import json,unittest
from copy import deepcopy
from companion.context_plan import ContextPlanner
from companion.pyramid import initial_state,PyramidCore,DecisionTrace
from companion.pyramid_runner import PyramidRunner,OBSERVATION_SCHEMA
from companion.story_core import StoryCore
from test_pyramid import STAMP,FakeLLM,observation

OLD='我在天文台觀測行星，準備記錄望遠鏡的讀數。'*10

def state():
 s=initial_state(STAMP);s['memory_index_mode']='scene-topic-v1'
 s['scene'].update(location='植物圖書館',character_location='植物圖書館',user_location='花園')
 s['history']=[{'role':'user','content':'昨天在天文台做什麼？','at':STAMP},{'role':'assistant','content':OLD,'at':STAMP}]
 for i in range(5):s['history'] += [{'role':'user','content':f'我在閱讀植物資料第{i}頁','at':STAMP},{'role':'assistant','content':f'這頁介紹第{i}類植物。','at':STAMP}]
 s['history'] += [{'role':'user','content':'植物為什麼需要陽光？','at':STAMP}]
 s['memory']['selection']={'items':[{'id':'old','source_index':1,'actor':'character','at':STAMP,'stage':'source_transcript','text':OLD,'kind':'raw','important':False,'reason':'relevant','lexical_score':1,'context':{}}]}
 return s

def decision(s,scope):
 d=PyramidCore().decide(s,[],observation(),DecisionTrace());d['information_scope']=scope;return d

class BoundaryTests(unittest.IsolatedAsyncioTestCase):
 async def test_recalled_old_reply_never_becomes_native_chat(self):
  s=state();before=deepcopy(s)
  m=await PyramidRunner(FakeLLM([])).prepare_writer(s,decision(s,'past_events'),'規則\n',DecisionTrace())
  native=[h['content'] for h in m[1:]]
  self.assertNotIn(OLD,native)
  self.assertEqual(native,[h['content'] for h in s['history'][-7:]])
  self.assertEqual(s['history'],before['history'])
  self.assertIn(OLD,json.loads(m[0]['content'].split('\n',1)[1])['story_context'])
  self.assertEqual(str(m).count(OLD),1)
 async def test_general_and_unknown_do_not_receive_recalled_episode_or_recap(self):
  for scope in ['general','unknown','current_state']:
   s=state();m=await PyramidRunner(FakeLLM([])).prepare_writer(s,decision(s,scope),'規則\n',DecisionTrace())
   self.assertNotIn(OLD,str(m));self.assertEqual(m[-1]['content'],'植物為什麼需要陽光？')
   c=json.loads(m[0]['content'].split('\n',1)[1])
   if 'current_facts' in c:self.assertEqual(c['current_facts']['scene']['character_location'],'植物圖書館')
 async def test_capacity_cannot_skip_neighbor_to_add_distant_relevant_native_turn(self):
  s=state();s['history'][-4]['content']='非常長的連續前文'*1000
  class Counter(FakeLLM):
   async def count_context(self,m):return sum(len(r['content']) for r in m)
  m=await PyramidRunner(Counter([])).prepare_writer(s,decision(s,'past_events'),'規則\n',DecisionTrace())
  self.assertNotIn(OLD,[h['content'] for h in m[1:]])
  self.assertEqual(m[-1]['content'],s['history'][-1]['content'])
 def test_packet_rejects_out_of_scope_inputs_even_if_caller_supplies_them(self):
  s=state();p=StoryCore().packet(s,{'information_scope':'general'},[dict(s['history'][-1],_index=12)],s['memory']['selection']['items'],StoryCore().continuity(s,8))
  self.assertNotIn(OLD,StoryCore().render(p));self.assertTrue(p['audit']['omitted'])
 def test_exact_long_reply_under_another_source_not_repeated_in_story(self):
  s=state();s['history'][-2]['content']=OLD
  recent=[dict(s['history'][-2],_index=11),dict(s['history'][-1],_index=12)]
  p=StoryCore().packet(s,{'information_scope':'past_events'},recent,s['memory']['selection']['items'],[])
  self.assertNotIn(OLD,StoryCore().render(p))
 def test_information_scope_is_required_on_user_observer(self):
  self.assertIn('information_scope',OBSERVATION_SCHEMA['required'])

class FullPipelineTests(unittest.IsolatedAsyncioTestCase):
 import test_pyramid_telegram as fixtures
 asyncSetUp=fixtures.TelegramPyramidTests.asyncSetUp
 asyncTearDown=fixtures.TelegramPyramidTests.asyncTearDown
 async def test_general_then_recall_preserve_source_and_world_state(self):
  s=state();s['history']=s['history'][:-1]
  self.app.pyramid.save_clock(s)
  # Run the real Telegram/App route with a synthetic transport and deterministic
  # model provider; no send reaches Telegram and no live state is imported.
  llm=FakeLLM([json.dumps(observation(information_scope='general',utterance={'act':'question','purpose':'other','value':'植物與陽光','evidence':'植物為什麼需要陽光？','ref':'','strength':1})),
               '植物利用陽光進行光合作用。',json.dumps(observation()),
               json.dumps(observation(information_scope='past_events',utterance={'act':'question','purpose':'social','value':'天文台往事','evidence':'之前在天文台做什麼？','ref':'','strength':1})),
               '先前在天文台觀測行星，準備記錄讀數。',json.dumps(observation())])
  self.app.llm=llm
  for uid,text in [(101,'植物為什麼需要陽光？'),(102,'之前在天文台做什麼？')]:
   await self.app.handle(uid,{'chat':{'id':42},'text':text})
   saved=self.app.pyramid.load()
   await self.app.pyramid.recover_response(saved,PyramidRunner(llm,self.app.pyramid.core),DecisionTrace())
   self.assertEqual(saved['scene']['character_location'],'植物圖書館');self.assertEqual(saved['scene']['user_location'],'花園')
   self.assertTrue(saved['appearance'])
  self.assertEqual(len(llm.calls),6)
  general,recall=llm.calls[1],llm.calls[4]
  self.assertNotIn(OLD,str(general));self.assertNotIn(OLD,[m['content'] for m in recall[1:]])
  self.assertIn(OLD,recall[0]['content'])
  self.assertEqual([m['content'] for m in recall[1:]][-1],'之前在天文台做什麼？')
  self.assertFalse(any(e.get('kind')=='move' for e in saved['events']))
