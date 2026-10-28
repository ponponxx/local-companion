"""Idle character movement must have a factual continuity handoff."""
import unittest
from copy import deepcopy
from companion.pyramid import initial_state,PyramidCore,DecisionTrace
from companion.journeys import scene_handoff_note, departure_notice
from test_pyramid import event

class PrecedenceTests(unittest.TestCase):
 def setUp(self):
  self.s=initial_state('2026-10-21T20:50:00+08:00');self.c=PyramidCore();self.t=DecisionTrace()
  self.s['scene'].update(location='工作室',character_location='工作室',user_location='公園',status='active')
  self.s['last_user_at']='2026-10-21T20:53:00+08:00'
  self.s['schedule']=[self.item('20:00','起居室'),self.item('22:00','臥室')]
  self.s.update(scene_notice_delivery='external',scene_notifications_enabled=False)
 def item(self,time,place):
  return {'at':'2026-10-21T'+time+':00+08:00','location':place,'activity':'休息','availability':'free'}
 def move(self,field='location',stage='completed',actor='user',target='scene',at='20:53'):
  proof='我們一起抵達醫院' if actor=='user' and target=='scene' else '證據'
  return self.c.apply(self.s,[event('move',field=field,value='醫院',actor=actor,target=target,stage=stage,evidence=proof)],
                      'assistant' if actor=='character' else 'user',proof,'move'+at,self.t,at='2026-10-21T'+at+':00+08:00')
 def test_idle_can_leave_hospital_and_records_the_process(self):
  self.move();appearance=deepcopy(self.s['appearance']);self.c.advance(self.s,'2026-10-21T21:04:00+08:00',self.t)
  self.assertEqual(self.s['scene']['character_location'],'起居室');self.assertEqual(self.s['scene']['user_location'],'醫院')
  self.assertIsNone(self.s['scene']['pending'])
  note=scene_handoff_note(self.s);self.assertIn('從醫院移到起居室',note);self.assertIn('沒有送出',note)
 def test_next_newer_slot_still_commits_in_quiet_hours(self):
  self.move();self.c.advance(self.s,'2026-10-21T22:01:00+08:00',self.t)
  self.assertEqual(self.s['scene']['character_location'],'臥室');self.assertEqual(self.s['scene']['user_location'],'醫院')
 def test_daytime_departure_notice_explains_pause(self):
  self.move();self.s['scene_notifications_enabled']=True
  self.c.advance(self.s,'2026-10-21T21:04:00+08:00',self.t)
  p=self.s['scene']['pending'];self.assertEqual(p['location'],'起居室')
  self.assertIn('暫時沒聊',departure_notice(self.s,p));self.assertEqual(self.s['scene']['character_location'],'醫院')
 def test_next_new_slot_requires_receipt_and_ten_minutes_by_day(self):
  self.move();self.s['scene_notifications_enabled']=True
  self.s['schedule']=self.s['schedule'][1:]
  self.c.advance(self.s,'2026-10-21T22:01:00+08:00',self.t)
  p=self.s['scene']['pending'];self.assertEqual(p['location'],'臥室')
  self.assertEqual(self.s['scene']['character_location'],'醫院')
  self.c.acknowledge_scene_notice(self.s,p['notice_id'],'2026-10-21T22:01:00+08:00',self.t)
  self.c.advance(self.s,'2026-10-21T22:12:00+08:00',self.t)
  self.assertEqual(self.s['scene']['character_location'],'臥室')
 def test_user_alone_and_relative_moves_do_not_block_character_routine(self):
  for field in ['location','relative_position']:
   with self.subTest(field=field):
    self.setUp();self.move(field=field,target='user')
    self.c.advance(self.s,'2026-10-21T21:04:00+08:00',self.t)
    self.assertEqual(self.s['scene']['character_location'],'起居室')
 def test_proposed_character_move_does_not_block_routine(self):
  self.assertFalse(self.move(stage='proposed'))
  self.c.advance(self.s,'2026-10-21T21:04:00+08:00',self.t)
  self.assertEqual(self.s['scene']['character_location'],'起居室')
 def test_delivered_pending_completes_and_preserves_handoff(self):
  self.move();self.s['scene']['pending']={**self.item('20:00','起居室'),'delivery':'delivered','deadline':'2026-10-21T21:00:00+08:00'}
  self.c.advance(self.s,'2026-10-21T21:04:00+08:00',self.t)
  self.assertEqual(self.s['scene']['character_location'],'起居室');self.assertIsNone(self.s['scene']['pending'])
  self.assertIn('通知已送達',scene_handoff_note(self.s))
 def test_new_background_move_breaks_shared_journey_and_records_origin(self):
  self.move(field='departure')
  self.c.advance(self.s,'2026-10-21T21:04:00+08:00',self.t)
  self.assertNotIn('character',self.s['scene']['journeys']);self.assertEqual(self.s['scene']['journeys']['user']['destination'],'醫院')
  self.assertIn('從工作室移到起居室',scene_handoff_note(self.s))

 def test_conversation_not_idle_does_not_move(self):
  self.move();self.c.advance(self.s,'2026-10-21T20:59:00+08:00',self.t)
  self.assertEqual(self.s['scene']['character_location'],'醫院');self.assertNotIn('_scene_handoff',self.s)
 def test_bridge_consumed_only_by_foreground_reply(self):
  self.move();self.c.advance(self.s,'2026-10-21T21:04:00+08:00',self.t)
  self.s['history'].append({'role':'assistant','turn_id':'background:news','content':'別則主動訊息'})
  self.assertTrue(scene_handoff_note(self.s))
  self.s['history'].append({'role':'user','content':'恭喜'})
  self.assertTrue(scene_handoff_note(self.s))
  self.s['history'].append({'role':'assistant','turn_id':'human-turn','content':'我先回來了'})
  self.assertFalse(scene_handoff_note(self.s))
 def test_explicit_character_move_invalidates_stale_bridge(self):
  self.move();self.c.advance(self.s,'2026-10-21T21:04:00+08:00',self.t)
  self.move(actor='character',target='character',at='21:05')
  self.assertFalse(scene_handoff_note(self.s))
 def test_initialization_and_same_place_activity_do_not_claim_departure(self):
  self.s['scene'].update(status='none',location='',character_location='')
  self.c._commit_schedule(self.s,self.s['schedule'][0],self.t,initial=True)
  self.assertFalse(scene_handoff_note(self.s))
  self.c._commit_schedule(self.s,self.s['schedule'][0],self.t,silent=True)
  self.assertFalse(scene_handoff_note(self.s))
 def test_writer_outline_has_one_bridge_and_recompile_is_idempotent(self):
  from companion.pyramid_runner import writer_contract
  from companion.writer_context import finish_contract
  from test_pyramid import observation
  self.move();self.c.advance(self.s,'2026-10-21T21:04:00+08:00',self.t)
  decision=self.c.decide(self.s,[],observation(),self.t)
  contract=writer_contract(self.s,decision)
  self.assertEqual(contract['decision']['response_outline'].count('從醫院移到起居室'),1)
  contract['past_memory']={'items':[]}  # finish_contract consumes this raw assembly block.
  contract=finish_contract(contract,self.s)
  self.assertEqual(contract['decision']['response_outline'].count('從醫院移到起居室'),1)

 def test_visible_header_explains_move_without_model_cooperation(self):
  from companion.state_brief import with_state_brief
  self.move();self.c.advance(self.s,'2026-10-21T21:04:00+08:00',self.t)
  text=with_state_brief(self.s,'真的嗎？恭喜！')
  self.assertIn('換場經過：',text);self.assertIn('從醫院移到起居室',text);self.assertIn('當時未通知',text)
 def test_delivered_casual_consumes_visible_header_without_human_reply_claim(self):
  self.move();self.c.advance(self.s,'2026-10-21T21:04:00+08:00',self.t)
  self.s['history'].append({'role':'assistant','turn_id':'background:one','background_kind':'casual','content':'稍後聊'})
  self.assertFalse(scene_handoff_note(self.s))
 def test_proactive_contract_does_not_inherit_foreground_handoff_instruction(self):
  from companion.pyramid_runner import writer_contract
  from companion.writer_context import finish_contract
  from test_pyramid import observation
  self.move();self.c.advance(self.s,'2026-10-21T21:04:00+08:00',self.t)
  d=self.c.decide(self.s,[],observation(),self.t);c=writer_contract(self.s,d)
  c['past_memory']={'items':[]};c['proactive_story']='分享書籍故事'
  c=finish_contract(c,self.s)
  self.assertNotIn('先用一句角色自己的話交代接續',c['decision']['response_outline'])
