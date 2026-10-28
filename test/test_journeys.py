"""Per-person journey state, no private conversation replay."""
import unittest
from copy import deepcopy
from companion.pyramid import initial_state,PyramidCore,DecisionTrace
from companion.journeys import migrate_legacy
from companion.writer_context import scene_facts
from companion.memory_metadata import scene_snapshot
from companion.state_brief import state_brief
from test_pyramid import STAMP,event

class JourneyTests(unittest.TestCase):
 def setUp(self):
  self.s=initial_state(STAMP);self.s['scene'].update(location='書店',character_location='書店',user_location='公園',status='active')
  self.core=PyramidCore();self.trace=DecisionTrace();self.sequence=0
 def apply(self,field,value,target='character',actor='user',source='user',stage='completed'):
  self.sequence+=1
  proof='我們一起移動到目的地' if target=='scene' and source=='user' else '有效來源'
  return self.core.apply(self.s,[event('move',field=field,value=value,target=target,actor=actor,stage=stage,evidence=proof)],source,proof,str(self.sequence),self.trace)
 def test_departure_is_not_arrival_and_persons_keep_their_origin(self):
  self.apply('departure','圖書館')
  self.assertEqual(self.s['scene']['user_location'],'公園');self.assertEqual(self.s['scene']['character_location'],'書店')
  self.assertEqual(self.s['scene']['journeys']['user']['destination'],'圖書館')
  self.assertEqual(self.s['scene_history'][-1]['phase'],'departed')
  self.assertIn('你正在前往：圖書館',state_brief(self.s))
 def test_user_alone_arrives_without_teleporting_character(self):
  self.apply('departure','圖書館');self.apply('location','@journey')
  self.assertEqual(self.s['scene']['user_location'],'圖書館');self.assertEqual(self.s['scene']['character_location'],'書店')
  self.assertNotIn('journeys',self.s['scene'])
 def test_confirmed_shared_departure_allows_user_to_complete_both_legs(self):
  self.apply('departure','圖書館',target='scene')
  model=scene_facts(self.s['scene']);self.assertEqual(model['journeys']['user']['companions'],['character'])
  self.assertNotIn('group',str(model));self.assertIn('journeys',scene_snapshot(self.s))
  self.apply('location','@journey')
  self.assertEqual(self.s['scene']['character_location'],'圖書館');self.assertEqual(self.s['scene']['user_location'],'圖書館')
  self.assertEqual(self.s['scene_history'][-1]['actor'],'character')
 def test_independent_same_destination_is_not_a_shared_trip(self):
  self.apply('departure','圖書館');self.apply('departure','圖書館',actor='character')
  self.apply('location','@journey')
  self.assertEqual(self.s['scene']['character_location'],'書店');self.assertIn('character',self.s['scene']['journeys'])
 def test_character_narration_cannot_complete_user_leg(self):
  self.apply('departure','圖書館',target='scene')
  self.apply('location','圖書館',actor='character',target='scene',source='assistant')
  self.assertEqual(self.s['scene']['user_location'],'公園');self.assertEqual(self.s['scene']['character_location'],'圖書館')
  self.assertIn('user',self.s['scene']['journeys'])
 def test_calendar_breaks_character_trip_and_never_drags_user(self):
  self.apply('departure','圖書館',target='scene')
  self.core._commit_schedule(self.s,{'location':'餐廳','activity':'用餐','availability':'free','at':STAMP},self.trace)
  self.assertNotIn('character',self.s['scene']['journeys']);self.apply('location','@journey')
  self.assertEqual(self.s['scene']['character_location'],'餐廳');self.assertEqual(self.s['scene']['user_location'],'圖書館')
 def test_direction_text_and_plans_cannot_be_confirmed_locations(self):
  before=deepcopy(self.s['scene'])
  self.assertFalse(self.apply('location','前往圖書館'));self.assertFalse(self.apply('departure','圖書館',stage='proposed'))
  self.assertEqual(self.s['scene'],before)
 def test_join_character_location_only_with_unambiguous_context(self):
  self.apply('location','@character');self.assertEqual(self.s['scene']['user_location'],'書店')
  self.apply('departure','圖書館');self.assertFalse(self.apply('location','@character'))
  self.assertFalse(self.apply('location','@unknown'))
 def test_legacy_direction_migrates_only_user_and_does_not_fabricate_origin_or_time(self):
  self.s['scene']['user_location']='前往醫院'
  self.assertTrue(migrate_legacy(self.s));self.assertFalse(migrate_legacy(self.s))
  trip=self.s['scene']['journeys']['user'];self.assertEqual(trip['destination'],'醫院');self.assertEqual(trip['origin'],'');self.assertEqual(trip['started_at'],'')
  self.assertEqual(self.s['scene']['character_location'],'書店');self.assertEqual(self.s['scene']['user_location'],'')
 def test_relative_movement_neither_changes_location_nor_creates_visit(self):
  before=deepcopy(self.s['scene']);self.apply('relative_position','靠近一步')
  self.assertEqual(self.s['scene']['user_location'],before['user_location'])
  self.assertEqual(self.s['scene_history'][-1]['phase'],'position_changed')

 def test_old_arrival_cannot_overwrite_new_departure(self):
  self.apply('departure','圖書館')
  old='2026-01-01T00:00:00+08:00'
  self.assertFalse(self.core.apply(self.s,[event('move',field='location',value='餐廳',evidence='有效來源')],'user','有效來源','old',self.trace,at=old))
  self.assertEqual(self.s['scene']['journeys']['user']['destination'],'圖書館')
