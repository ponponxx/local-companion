from copy import deepcopy
from datetime import timedelta
import json
import unittest
from companion import life_events as le
from companion.memory_metadata import record_scene,scene_snapshot
from companion.pyramid import PyramidCore,DecisionTrace,TurnInput
from companion.pyramid_runner import PyramidRunner,writer_prefix
from companion.telegram_debug import describe
from test_life_events import prepared,NOW
from test_pyramid import FakeLLM,observation
import test_pyramid_telegram as fixtures


def ready():
 s=prepared();s.pop('life_events',None);s['story_events_enabled']=True
 return s


def move(s,place,at=NOW,actor='character',phase='moved'):
 before=scene_snapshot(s);s['scene']['character_location']=place;s['scene']['location']=place
 return record_scene(s,phase,before,'synthetic',at.isoformat(),actor)


class SceneEventTests(unittest.TestCase):
 def test_each_confirmed_visit_plans_one_or_two_without_committing(self):
  s=ready();move(s,'圖書館');book=le.ledger(s)
  self.assertIn(len(book['drafts']),(1,2));self.assertEqual(book['stories'],[])
  self.assertEqual(le.current_reading(s),'')
  self.assertFalse(le.candidates(s,NOW))
  self.assertNotIn(le.TEMPLATES['reading_note']['opening'],str(s['history']))

 def test_pending_announcements_and_user_only_moves_do_not_plan(self):
  s=ready();before=scene_snapshot(s);s['scene']['pending']={'location':'花園'}
  record_scene(s,'announced',before,'synthetic',NOW.isoformat())
  self.assertNotIn('life_events',s)
  s['scene']['pending']=None;before=scene_snapshot(s);s['scene']['user_location']='花園'
  record_scene(s,'moved',before,'synthetic',NOW.isoformat(),'user')
  self.assertNotIn('life_events',s)

 def test_initial_confirmed_place_bootstraps_once_across_restart(self):
  s=ready();le.sync_visit(s,NOW);visit=deepcopy(le.ledger(s)['visit'])
  for i in range(4):le.sync_visit(s,NOW+timedelta(minutes=i))
  s=json.loads(json.dumps(s));le.sync_visit(s,NOW+timedelta(hours=1))
  self.assertEqual(le.ledger(s)['visit'],visit);self.assertEqual(len(le.ledger(s)['visits']),1)

 def test_return_visit_gets_new_id_and_old_stories_do_not_resume(self):
  s=ready();move(s,'圖書館');le.advance(s,NOW,'normal',DecisionTrace());old=le.active(s)
  first=le.ledger(s)['visit']['id'];move(s,'花園',NOW+timedelta(minutes=1))
  self.assertEqual(old['status'],'expired')
  le.advance(s,NOW+timedelta(minutes=1),'normal',DecisionTrace())
  self.assertIsNotNone(le.active(s))  # A new visit is not held by the old visit's 90-minute cooldown.
  move(s,'圖書館',NOW+timedelta(minutes=2))
  self.assertNotEqual(first,le.ledger(s)['visit']['id']);self.assertEqual(len(le.ledger(s)['visits']),3)
  self.assertLessEqual(len(le.ledger(s)['drafts']),2)

 def test_category_matches_paraphrase_not_exact_activity(self):
  s=ready();le.sync_visit(s,NOW)
  draft=next(d for d in le.ledger(s)['drafts'] if d['template']!='scene_observation')
  s['activity']='閱讀另一份文獻'
  self.assertNotEqual(s['activity'],draft['activity']);self.assertTrue(le.eligible(s,draft))
  s['activity']='晚餐';self.assertFalse(le.eligible(s,draft))
  generic=next(d for d in le.ledger(s)['drafts'] if d['template']=='scene_observation')
  self.assertTrue(le.eligible(s,generic))

 def test_unknown_activity_has_one_generic_and_sleep_waits(self):
  s=ready();s['activity']='';s['availability']='sleep';le.sync_visit(s,NOW)
  self.assertEqual(len(le.ledger(s)['drafts']),1)
  le.advance(s,NOW,'normal',DecisionTrace());self.assertIsNone(le.active(s))
  s['availability']='free';le.advance(s,NOW,'normal',DecisionTrace());self.assertIsNotNone(le.active(s))

 def test_turned_off_does_not_create_visit(self):
  s=ready();s['story_events_enabled']=False;move(s,'花園')
  self.assertNotIn('life_events',s)
  self.assertFalse(le.plan_visit(s,'disabled',NOW))

 def test_reenable_after_return_uses_latest_arrival_not_old_visit(self):
  s=ready();move(s,'圖書館');first=le.ledger(s)['visit']['id']
  s['story_events_enabled']=False;move(s,'花園',NOW+timedelta(minutes=1));move(s,'圖書館',NOW+timedelta(minutes=2))
  self.assertEqual(le.ledger(s)['visit']['id'],first)
  s['story_events_enabled']=True;le.sync_visit(s,NOW+timedelta(minutes=3))
  self.assertNotEqual(le.ledger(s)['visit']['id'],first)
  self.assertEqual(len(le.ledger(s)['visits']),2)

 def test_original_core_selects_chat_content_without_reselecting_action(self):
  s=ready();le.sync_visit(s,NOW);le.advance(s,NOW,'normal',DecisionTrace())
  obs=observation();obs.update(asks_character=True)
  obs['utterance'].update(act='question',purpose='social',evidence='最近怎麼樣')
  events=[{'kind':'question','strength':1,'id':'q','evidence':'最近怎麼樣'}]
  d=PyramidCore().decide(s,events,obs,DecisionTrace())
  self.assertIn('life_share',d)
  social=deepcopy(obs);social['asks_character']=False
  self.assertEqual(le.chat_share(s,social,events,d)['mode'],'optional')
  for purpose in ['task','intimacy']:
   blocked=deepcopy(obs);blocked['utterance']['purpose']=purpose
   self.assertIsNone(le.chat_share(s,blocked,events,d))
  self.assertIsNone(le.chat_share(s,obs,[{'kind':'pause'}],d))
  le.active(s)['beats'][-1]['disclosure']='delivered'
  self.assertIsNone(le.chat_share(s,obs,events,d))

 def test_status_debug_distinguishes_draft_and_unshared(self):
  s=ready();le.sync_visit(s,NOW)
  report=le.status(s,NOW);self.assertGreater(report['drafts'],0);self.assertEqual(report['unshared'],0)
  s['availability']='sleep';self.assertIn('睡眠',le.status(s,NOW)['blocked']);s['availability']='free'
  le.advance(s,NOW,'normal',DecisionTrace());report=le.status(s,NOW)
  self.assertEqual(report['unshared'],1)
  text=describe({'rule':'life_event.status','output':report})[1]
  self.assertIn('待發生',text);self.assertIn('未分享',text)


class SceneEventIntegrationTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=fixtures.TelegramPyramidTests.asyncSetUp
 asyncTearDown=fixtures.TelegramPyramidTests.asyncTearDown

 async def test_ordinary_chat_shares_once_in_brief_and_still_has_three_calls(self):
  s=ready();le.sync_visit(s,NOW);le.advance(s,NOW,'normal',DecisionTrace());story=le.active(s)
  text=story['beats'][-1]['text'];user=observation();user.update(asks_character=True,information_scope='current_state')
  user['utterance'].update(act='question',purpose='social',evidence='妳最近在做什麼')
  user['life_event_input']={'ref':story['id'],'action':'none','evidence':'妳最近在做什麼'}
  post=observation();post['life_event_disclosure']={'ref':story['id'],'version':1,'evidence':text}
  llm=FakeLLM([json.dumps(user),text,json.dumps(post)])
  result=await PyramidRunner(llm,clock=lambda:NOW).run(s,TurnInput('妳最近在做什麼','new',True))
  self.assertEqual(len(llm.calls),3)
  writer=llm.calls[1][0]['content']
  self.assertEqual(writer.count(text),1);self.assertNotIn(story['id'],writer)
  self.assertEqual(le.active(result.after)['beats'][-1]['disclosure'],'delivered')

 async def test_idle_reports_disabled_or_pending_and_does_not_generate(self):
  s=ready();self.store.set('life_enabled','true');self.store.set('story_events_enabled','false')
  trace=DecisionTrace();await self.app.pyramid.life.events(s,NOW,trace)
  self.assertIn('已關閉',trace.nodes[-1]['output']['blocked'])
  self.store.set('story_events_enabled','true');s['_pending_responses']=[{}]
  trace=DecisionTrace();await self.app.pyramid.life.events(s,NOW,trace)
  self.assertIn('等待',trace.nodes[-1]['output']['blocked']);self.assertFalse(self.llm.calls)

 async def test_telegram_deferred_observer_retains_selected_story_and_quota(self):
  s=ready();le.sync_visit(s,NOW);le.advance(s,NOW,'normal',DecisionTrace());story=le.active(s)
  self.app.now=lambda:NOW;self.app.pyramid.save_clock(s)
  before={k:self.store.get(k) for k in ['proactive_daily_limit','proactive_cooldown_minutes','last_proactive_at','proactive_unanswered']}
  user=observation();user['utterance'].update(act='question',purpose='social',evidence='妳最近怎樣')
  user['life_event_input']={'ref':story['id'],'action':'none','evidence':'妳最近怎樣'}
  post=observation();text=story['beats'][-1]['text']
  post['life_event_disclosure']={'ref':story['id'],'version':1,'evidence':text}
  self.llm.results=iter([json.dumps(user),text,json.dumps(post)])
  await fixtures.TelegramPyramidTests.text(self,'妳最近怎樣',101)
  state=json.loads(self.store.get('pyramid_state'))
  self.assertEqual(le.active(state)['beats'][-1]['disclosure'],'delivered')
  self.assertEqual(before,{k:self.store.get(k) for k in before})
  self.assertFalse(state.get('_pending_responses'))
