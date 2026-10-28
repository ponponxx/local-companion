"""Actor scope validation for movement embedded in clarifications."""
import unittest,json
from copy import deepcopy
from companion.pyramid import initial_state,PyramidCore,DecisionTrace
from companion.journeys import singular_self_report
from test_pyramid import STAMP,event,observation,FakeLLM
from companion.pyramid_runner import PyramidRunner

class ClarificationTests(unittest.TestCase):
 def test_embedded_self_report_normalizes_only_user(self):
  for text in ['更正一下，我現在到溫室了','我的意思是我到溫室了','抱歉我剛剛到溫室了','To clarify, I arrived at the greenhouse.']:
   s=initial_state(STAMP);s['scene_background_world']='original_world';s['scene'].update(location='青葉莊園溫室',character_location='青葉莊園溫室',user_location='拾頁書店')
   before=deepcopy(s['appearance']);c=PyramidCore()
   accepted=c.apply(s,[event('move',target='scene',field='location',value='溫室',evidence=text)],'user',text,'clarify',DecisionTrace())
   self.assertEqual(accepted[0]['target'],'user');self.assertEqual(s['scene']['user_location'],'青葉莊園溫室');self.assertEqual(s['scene']['character_location'],'青葉莊園溫室');self.assertEqual(s['appearance'],before)
 def test_conditional_quoted_negated_and_plural_are_not_singular_authority(self):
  for text in ['如果我到溫室了','假設我到溫室了','不是我到溫室了','他說我到溫室了','「我到溫室了」','我們到溫室了','If I arrived at the greenhouse.']:
   self.assertFalse(singular_self_report(text),text)
 def test_wrong_actor_still_rejected(self):
  s=initial_state(STAMP);text='我的意思是我到溫室了';before=deepcopy(s['scene'])
  self.assertFalse(PyramidCore().apply(s,[event('move',actor='character',target='scene',field='location',value='溫室',evidence=text)],'user',text,'x',DecisionTrace()));self.assertEqual(s['scene'],before)

class AppTests(unittest.IsolatedAsyncioTestCase):
 import test_pyramid_telegram as fixtures
 asyncSetUp=fixtures.TelegramPyramidTests.asyncSetUp
 asyncTearDown=fixtures.TelegramPyramidTests.asyncTearDown
 async def test_two_turn_clarification_commits_before_writer_and_survives_reload(self):
  s=self.app.pyramid.load();s['scene'].update(location='青葉莊園溫室',character_location='青葉莊園溫室',user_location='拾頁書店');self.app.pyramid.save_clock(s);appearance=deepcopy(self.app.pyramid.load()['appearance'])
  text='我的意思是我到溫室了'
  llm=FakeLLM([json.dumps(observation([event('move',target='scene',field='location',value='溫室',evidence=text)],information_scope='current_state')),'你到溫室了。',json.dumps(observation()),json.dumps(observation(information_scope='current_state')),'我們都在溫室。',json.dumps(observation())]);self.app.llm=llm
  for uid,word in [(201,text),(202,'我現在在哪裡？')]:
   await self.app.handle(uid,{'chat':{'id':42},'text':word});s=self.app.pyramid.load();await self.app.pyramid.recover_response(s,PyramidRunner(llm,self.app.pyramid.core),DecisionTrace())
   self.assertEqual(s['scene']['user_location'],'青葉莊園溫室');self.assertEqual(s['scene']['character_location'],'青葉莊園溫室');self.assertEqual(s['appearance'],appearance)
  moves=[e for e in s['events'] if e.get('kind')=='move'];self.assertTrue(moves);self.assertTrue(all(e['actor']=='user' and e['stage']=='completed' for e in moves))
  prompt=json.loads(self.store.get('last_text_prompt'));contract=json.loads(prompt[0]['content'].split('\n',1)[1]);self.assertEqual(contract['current_facts']['scene']['user_location'],'青葉莊園溫室');self.assertEqual(len(llm.calls),6)
