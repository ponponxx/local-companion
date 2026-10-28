"""Neutral scene identity tests; no private generation replay."""
import unittest
from copy import deepcopy
from datetime import datetime,timedelta
from companion.pyramid import initial_state,PyramidCore,DecisionTrace
from companion.scene_background import canonical_location,same_location
from companion.journeys import pending_scene_handoff
from test_pyramid import STAMP,event

class PlaceIdentityTests(unittest.TestCase):
 def setUp(self):
  self.s=initial_state(STAMP);self.s['scene_background_world']='original_world'
  self.s['scene'].update(location='青葉莊園臥室',character_location='青葉莊園臥室',user_location='青葉莊園餐廳',status='active')
  self.s['last_user_at']=(datetime.fromisoformat(STAMP)-timedelta(minutes=20)).isoformat()
  self.c=PyramidCore();self.t=DecisionTrace()
 def move(self,text,value,actor='user',target='scene'):
  return self.c.apply(self.s,[event('move',actor=actor,target=target,field='location',value=value,evidence=text)],'user',text,'single',self.t)
 def test_singular_arrival_cannot_rename_character_and_uses_contextual_room(self):
  r=self.move('我到臥室了','臥室');self.assertEqual(r[0]['target'],'user')
  self.assertEqual(self.s['scene']['user_location'],'青葉莊園臥室');self.assertEqual(self.s['scene']['character_location'],'青葉莊園臥室')
 def test_existing_shared_trip_can_finish_and_unknown_legacy_destination_cannot_move_character(self):
  text='我們一起前往莊園餐廳'
  self.c.apply(self.s,[event('move',actor='user',target='scene',field='departure',value='莊園餐廳',evidence=text)],'user',text,'depart',self.t)
  self.move('我到莊園餐廳了','莊園餐廳')
  self.assertEqual(self.s['scene']['character_location'],'青葉莊園餐廳')
  self.s['scene']['journeys']={p:{'status':'moving','destination':'卧式','group':'legacy'} for p in ['user','character']}
  self.assertFalse(self.move('我到了','@journey',target='user'))
  self.assertEqual(self.s['scene']['character_location'],'青葉莊園餐廳')
 def test_wrong_actor_does_not_move_character(self):
  before=deepcopy(self.s['scene']);self.assertFalse(self.move('我到臥室了','臥室',actor='character'));self.assertEqual(self.s['scene'],before)
 def test_typo_is_not_registered_character_scene(self):
  before=deepcopy(self.s['scene']);self.assertFalse(self.move('我們到卧式了','卧式'));self.assertEqual(self.s['scene'],before)
  self.move('我到卧式了','卧式');self.assertEqual(self.s['scene']['character_location'],'青葉莊園臥室')
 def test_unproven_collective_arrival_is_rejected(self):
  self.assertFalse(self.move('到臥室了','臥室'));self.assertEqual(self.s['scene']['user_location'],'青葉莊園餐廳')
 def test_known_collective_and_custom_registered_places_remain_supported(self):
  self.assertTrue(self.move('我們到莊園臥室了','莊園臥室'))
  self.s['scene_wardrobe_locations']={'新旅館客房':'indoor'}
  self.assertEqual(canonical_location(self.s,'新旅館客房'),'新旅館客房')
 def test_short_name_must_have_unique_context_not_fuzzy_spelling(self):
  self.s['scene_wardrobe_locations']={'北塔臥室':'indoor'};self.s['scene']['user_location']='北塔臥室'
  self.assertIsNone(canonical_location(self.s,'臥室'));self.assertIsNone(canonical_location(self.s,'卧式'))
 def test_same_place_activity_does_not_create_notice_or_handoff(self):
  self.s['scene']['location']=self.s['scene']['character_location']='莊園臥室'
  self.s['schedule']=[{'at':STAMP,'location':'青葉莊園臥室','activity':'睡眠','availability':'sleep'}]
  self.c._scene_clock(self.s,self.t)
  self.assertIsNone(self.s['scene']['pending']);self.assertFalse(self.s['notices']);self.assertNotIn('_scene_handoff',self.s)
  self.assertEqual(self.s['scene']['character_location'],'青葉莊園臥室')
 def test_stale_same_place_pending_is_cancelled_before_delivery_or_deadline(self):
  self.s['scene']['pending']={'location':'莊園臥室','delivery':'queued','notice_id':'old'}
  self.s['scene']['status']='moving';self.s['activity']='前往莊園臥室'
  self.c._scene_clock(self.s,self.t);self.assertIsNone(self.s['scene']['pending']);self.assertFalse(self.s['notices']);self.assertEqual(self.s['activity'],'')
 def test_false_legacy_handoff_not_presented_as_real_move(self):
  self.s['_scene_handoff']={'origin':'臥室','destination':'青葉莊園臥室','after_history_index':0}
  self.assertFalse(pending_scene_handoff(self.s));self.assertTrue(self.s['_scene_handoff'])


class TelegramIdentityTests(unittest.IsolatedAsyncioTestCase):
 import test_pyramid_telegram as fixture
 asyncSetUp=fixture.TelegramPyramidTests.asyncSetUp
 asyncTearDown=fixture.TelegramPyramidTests.asyncTearDown
 async def test_two_turn_chat_and_schedule_keep_canonical_individual_locations(self):
  import json
  from companion.pyramid_runner import PyramidRunner
  from test_pyramid import observation,FakeLLM
  s=self.app.pyramid.load();s['scene'].update(location='青葉莊園臥室',character_location='青葉莊園臥室',user_location='青葉莊園餐廳',status='active')
  self.app.pyramid.save_clock(s)
  first='我到臥室了，來看看妳。'
  one=observation([event('move',actor='user',target='scene',field='location',value='臥室',evidence='我到臥室了')])
  llm=FakeLLM([json.dumps(one,ensure_ascii=False),'你到了呀。',json.dumps(observation()),json.dumps(observation()),'我在臥室。',json.dumps(observation())]);self.app.llm=llm
  for uid,text in [(10,first),(11,'妳現在在哪裡？')]:
   await self.app.handle(uid,{'chat':{'id':42},'text':text})
   s=self.app.pyramid.load();await self.app.pyramid.recover_response(s,PyramidRunner(llm,self.app.pyramid.core),DecisionTrace())
   self.assertEqual(s['scene']['character_location'],'青葉莊園臥室');self.assertEqual(s['scene']['user_location'],'青葉莊園臥室')
  self.assertEqual(len(llm.calls),6)
  prompt=json.loads(self.store.get('last_text_prompt'));contract=json.loads(prompt[0]['content'].split('\n',1)[1])
  self.assertEqual(contract['current_facts']['scene']['character_location'],'青葉莊園臥室')
  self.assertFalse(any(n['phase']=='arrived' for n in s.get('scene_history',[])))
  self.assertEqual([h['content'] for h in s['history'] if h['role']=='user'],[first,'妳現在在哪裡？'])
