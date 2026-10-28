"""Scene lifecycle owns movement; proactive topics cannot reopen state notices."""
import json,unittest
from copy import deepcopy
from datetime import timedelta,datetime
from companion.pyramid import initial_state,PyramidCore,DecisionTrace
from companion.story_core import StoryCore
from companion.story_proactive import StoryProactive
from companion.story_events import digest
from companion.pyramid_runner import writer_length,writer_prefix
from test_pyramid import STAMP,observation,event
from test_story_proactive import seed
import test_story_proactive as proactive_fixtures

class SourceTests(unittest.TestCase):
 def test_committed_arrival_does_not_seed_followup_even_if_mislabeled_question(self):
  s=initial_state(STAMP);text='我到書店了';row={'role':'user','content':text,'at':STAMP,'turn_id':'a'};s['history']=[row]
  e=event('move',target='user',field='location',value='書店',evidence=text)
  PyramidCore().apply(s,[e],'user',text,'a',DecisionTrace())
  StoryCore().record(s,row,observation(topic='抵達書店',utterance={'act':'question','purpose':'social'},story_events=[]))
  self.assertFalse(StoryProactive().collect(s,datetime.fromisoformat(STAMP)));self.assertEqual(row['content'],text)
 def test_mixed_update_with_substantive_topic_remains_available(self):
  s=initial_state(STAMP);row=seed(s);row['content']='我到書店了，買的植物圖鑑還沒看完';row['turn_id']='mixed'
  s['events']=[dict(event('move',target='user',field='location',value='書店',evidence='我到書店了'),source='user',turn_id='mixed')]
  row['_story']['digest']=digest(row)
  self.assertTrue(StoryProactive().collect(s,datetime.fromisoformat(STAMP)))
 def test_chinese_normal_and_proactive_share_same_length_rule(self):
  self.assertEqual(writer_length('CH'),writer_length('CH',True));self.assertIn(writer_length('CH'),writer_prefix('CH'))

class OrderingTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=proactive_fixtures.StoryDeliveryTests.asyncSetUp
 asyncTearDown=proactive_fixtures.StoryDeliveryTests.asyncTearDown
 async def test_notice_then_arrival_then_casual_two_heartbeats(self):
  self.store.set('life_enabled','true');self.store.set('story_events_enabled','false')
  s=self.app.pyramid.load();s['scene'].update(location='青葉莊園餐廳',character_location='青葉莊園餐廳',user_location='青葉莊園餐廳');s['availability']='free'
  s['schedule']=[{'at':self.now.isoformat(),'location':'青葉莊園起居室','activity':'閱讀','availability':'free','share_worthy':False}]
  self.app.pyramid.save_clock(s);before=deepcopy(self.app.pyramid.load()['appearance'])
  await self.life.tick();self.tg.send.assert_awaited_once();self.assertFalse(self.llm.calls)
  s=self.app.pyramid.load();self.assertTrue(s['scene']['pending']);self.assertEqual(s['scene']['character_location'],'青葉莊園餐廳');self.assertEqual(s['appearance'],before);self.assertEqual(self.life.count_today(self.now),0)
  self.now+=timedelta(minutes=10)
  self.llm.results=iter(['那本植物圖鑑最近讀到哪裡了？我剛換個地方閱讀，也想聽聽你的心得。'])
  await self.life.tick();self.assertEqual(self.tg.send.await_count,2);self.assertEqual(len(self.llm.calls),1)
  s=self.app.pyramid.load();self.assertIsNone(s['scene']['pending']);self.assertEqual(s['scene']['character_location'],'青葉莊園起居室');self.assertEqual(s['scene']['user_location'],'青葉莊園餐廳');self.assertEqual(self.life.count_today(self.now),1)
  c=json.loads(self.llm.calls[0][0]['content'].split('\n',1)[1]);self.assertEqual(c['current_facts']['scene']['character_location'],'青葉莊園起居室');self.assertIn('30至50',self.llm.calls[0][0]['content'])
 async def test_character_journey_blocks_generation(self):
  s=self.app.pyramid.load();s['scene']['journeys']={'character':{'status':'moving','destination':'書房'}}
  await self.life.casual(s,self.now,DecisionTrace());self.assertFalse(self.llm.calls);self.tg.send.assert_not_awaited()
 async def test_scene_changed_during_generation_discards_old_draft(self):
  async def generated(messages,on_request=None,**kwargs):
   if on_request:on_request(messages)
   fresh=self.app.pyramid.load();fresh['scene']['pending']={'location':'青葉莊園起居室','delivery':'delivered'};self.app.pyramid.save_clock(fresh)
   return '這份舊稿不應送出。'
  self.llm.generate=generated
  await self.life.casual(self.app.pyramid.load(),self.now,DecisionTrace())
  self.tg.send.assert_not_awaited();self.assertEqual(self.life.count_today(self.now),0)
