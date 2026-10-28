"""Writer perspective is shared by foreground and proactive messages."""
import json,unittest
from copy import deepcopy
from companion.pyramid import initial_state,DecisionTrace
from companion.pyramid_runner import WRITER_VOICE,writer_prefix,PyramidRunner
from companion.story_proactive import continuation_handoff
from companion.journeys import scene_handoff_note
from test_pyramid import STAMP,FakeLLM,observation

class SpeakerTests(unittest.TestCase):
 def test_self_life_has_own_experience_instruction_not_generic_quote_rule(self):
  s=initial_state(STAMP)
  for kind in ['life','life_event']:
   p,_=continuation_handoff(s,{'subtype':kind,'story':'角色整理植物資料。'})
   self.assertIn('角色自己',p['instruction']);self.assertNotIn('不能轉成角色經歷',p['instruction'])
  p,_=continuation_handoff(s,{'subtype':'story','story':'使用者的往事。'})
  self.assertIn('不能轉成角色經歷',p['instruction'])
 def test_handoff_does_not_feed_exact_clock_or_pause_into_dialogue_outline(self):
  s=initial_state(STAMP);s['scene'].update(location='書房',character_location='書房')
  s['_scene_handoff']={'origin':'花園','destination':'書房','at':'2026-10-26T20:10:00+08:00','idle_minutes':568,'notice_status':'delivered','after_history_index':0}
  before=deepcopy(s);note=scene_handoff_note(s)
  self.assertNotIn('20:10',note);self.assertNotIn('568',note);self.assertIn('已完成',note);self.assertEqual(s,before)
 def test_foreground_starts_with_speaker_not_third_party_writer(self):
  self.assertTrue(writer_prefix('CH').startswith(WRITER_VOICE));self.assertNotIn('你是角色Writer',writer_prefix('CH'))

class AppTests(unittest.IsolatedAsyncioTestCase):
 import test_pyramid_telegram as fixtures
 asyncSetUp=fixtures.TelegramPyramidTests.asyncSetUp
 asyncTearDown=fixtures.TelegramPyramidTests.asyncTearDown
 async def test_two_turn_pipeline_preserves_identity_and_world_facts(self):
  self.store.update_persona('name','林青');self.store.set_profile_fact('name','阿哲')
  s=self.app.pyramid.load();s['story'].update(name='林青',background='28歲成年植物研究員。');s['user_profile']=[{'id':'1','category':'name','value':'阿哲','confidence':1,'source':'manual','updated_at':STAMP}]
  s['scene'].update(location='青葉莊園溫室',character_location='青葉莊園溫室',user_location='拾頁書店');self.app.pyramid.save_clock(s);appearance=deepcopy(self.app.pyramid.load()['appearance'])
  self.llm.results=iter([json.dumps(observation(information_scope='current_state')),'晚上好，阿哲。',json.dumps(observation()),json.dumps(observation(information_scope='current_state')),'我在溫室整理資料。',json.dumps(observation())])
  for uid,text in [(301,'晚上好'),(302,'妳現在在哪裡？')]:
   await self.app.handle(uid,{'chat':{'id':42},'text':text});saved=self.app.pyramid.load();await self.app.pyramid.recover_response(saved,PyramidRunner(self.llm,self.app.pyramid.core),DecisionTrace())
   self.assertEqual(saved['scene']['character_location'],'青葉莊園溫室');self.assertEqual(saved['scene']['user_location'],'拾頁書店');self.assertEqual(saved['appearance'],appearance)
  m=json.loads(self.store.get('last_text_prompt'));c=json.loads(m[0]['content'].split('\n',1)[1]);self.assertEqual(c['identity']['character'],'林青');self.assertEqual(c['identity']['user'],'阿哲');self.assertTrue(m[0]['content'].startswith(WRITER_VOICE));self.assertEqual(len(self.llm.calls),6)
