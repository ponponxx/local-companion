import unittest,json
from copy import deepcopy
from datetime import datetime,timedelta
from companion.annual_schedule import load_calendar,lookup_day,install_day,PATH
from companion.scene_background import resolve_background
from companion.scene_appearance import restyle_arrival
from companion.visual_state import current_appearance
from companion.pyramid import initial_state
class AnnualScheduleTests(unittest.TestCase):
 def test_all_365_days_locations_outfits_and_boundaries(self):
  document=json.loads(PATH.read_text());self.assertEqual(len(load_calendar()),365)
  for day in document['days']:
   s=initial_state();s['scene_background_world']='original_world';now=datetime.fromisoformat(day['date']+'T01:00:00+08:00');install_day(s,now)
   self.assertEqual(len(s['schedule']),9);self.assertEqual(s['schedule'][0]['availability'],'sleep');self.assertEqual(s['schedule'][-1]['availability'],'sleep')
   for item in s['schedule']:
    self.assertTrue(resolve_background(item['location'],'original_world')['matched'])
    s['scene'].update(location=item['location'],character_location=item['location']);self.assertTrue(restyle_arrival(s,item)['applied'])
    self.assertNotIn('未確認',current_appearance(s['appearance'])['clothing'].values())
   self.assertEqual(s['history'],[]);self.assertNotIn('life_events',s)
 def test_future_rebased_and_leap_day_not_missing(self):
  for date in ['2028-02-29','2030-12-25']:
   now=datetime.fromisoformat(date+'T10:00:00+08:00');s=initial_state();r=install_day(s,now)
   self.assertEqual(r['date'],date);self.assertTrue(all(i['at'].startswith(date) for i in s['schedule']))
   self.assertEqual(r['leap_day_extension'],date.endswith('02-29'))
 def test_caller_cannot_mutate_cached_calendar(self):
  now=datetime.fromisoformat('2026-10-21T10:00:00+08:00');a=lookup_day(now);a['activities'].clear();self.assertEqual(len(lookup_day(now)['activities']),9)
 def test_other_world_is_not_assigned_this_characters_life(self):
  with self.assertRaises(ValueError):lookup_day(datetime.now(),'science_fiction')
 def test_three_trips_and_no_mid_trip_home_meals(self):
  rows=list(load_calendar().values());trips={}
  for row in rows:
   if 'trip' in row:trips.setdefault(row['trip']['id'],[]).append(row)
  self.assertEqual(sorted(len(v) for v in trips.values()),[1,2,3])
  for days in trips.values():
   for row in days:
    trip=row['trip']
    self.assertTrue(trip['planning_only']);self.assertFalse(trip['user_accompanies'])
    self.assertTrue(all(e['status']=='draft' for e in row['event_candidates']))
    if trip['day']<trip['total_days']:
     for item in row['activities'][4:]:self.assertNotIn('青葉莊園',item['location'])
 def test_every_midnight_matches_previous_nights_planned_sleep(self):
  rows=json.loads(PATH.read_text())['days']
  for previous,current in zip(rows,rows[1:]):
   self.assertEqual(previous['activities'][-1]['location'],current['activities'][0]['location'],current['date'])
 def test_birthday_is_explicit_about_who_and_is_not_a_completed_event(self):
  row=lookup_day(datetime.fromisoformat('2027-06-05T10:00:00+08:00'))
  self.assertEqual(row['birthdays'][0]['actor'],'character');self.assertIn('角色生日',row['activities'][7]['activity']);self.assertTrue(row['planning_only'])
  state=initial_state();install_day(state,datetime.fromisoformat('2027-06-05T10:00:00+08:00'))
  self.assertEqual(state['history'],[]);self.assertEqual(state['user_profile'],[]);self.assertIn('birthdays',state['life_calendar_day'])
 def test_birthday_overlay_on_trip_stays_in_the_trip_location(self):
  from unittest.mock import patch
  from scripts.calendar_occasions import apply_occasions
  config={'trips':[],'birthdays':[{'actor':'character','label':'角色','month_day':'12-12'}]}
  row=lookup_day(datetime.fromisoformat('2026-12-12T12:00:00+08:00'));old=row['activities'][7]['location']
  def fake_activity(start,location,text,*args,**kwargs):return dict(start=start,location=location,activity=text)
  with patch('scripts.calendar_occasions.json.loads',return_value=config):result=apply_occasions(row,fake_activity)
  self.assertEqual(result['activities'][7]['location'],old);self.assertNotIn('青葉莊園',old)
 def test_weekend_outing_keeps_lunch_near_diagon_alley(self):
  row=lookup_day(datetime.fromisoformat('2026-10-24T09:00:00+08:00'))
  self.assertIn('weekend',row);self.assertEqual(row['activities'][3]['location'],'石板街')
  self.assertEqual(row['activities'][4]['location'],'星港魔法茶館');self.assertEqual(row['activities'][5]['location'],'拾頁書店')
  self.assertTrue(all(a['availability']=='free' for a in row['activities'][3:8]))
 def test_future_year_uses_actual_weekend_and_restores_weekdays(self):
  # 10/24 is Saturday in 2026 and Sunday in 2027; 10/25 changes Sunday -> Monday.
  monday=lookup_day(datetime.fromisoformat('2027-10-25T09:00:00+08:00'))
  self.assertEqual(monday['weekday'],'一');self.assertNotIn('weekend',monday);self.assertEqual(monday['activities'][3]['availability'],'busy')
  saturday=lookup_day(datetime.fromisoformat('2027-10-23T09:00:00+08:00'))
  self.assertEqual(saturday['weekday'],'六');self.assertIn('weekend',saturday);self.assertEqual(saturday['activities'][3]['availability'],'free')
 def test_weekend_never_overwrites_trip_or_character_birthday(self):
  trip=lookup_day(datetime.fromisoformat('2026-12-12T09:00:00+08:00'));self.assertIn('trip',trip);self.assertEqual(trip['activities'][3]['location'],'松風村')
  birthday=lookup_day(datetime.fromisoformat('2027-06-05T09:00:00+08:00'));self.assertIn('角色生日',birthday['activities'][7]['activity']);self.assertIn('birthdays',birthday)
 def test_weekend_alternatives_all_have_valid_locations_and_outfits(self):
  from companion.annual_schedule import weekend_routes
  self.assertEqual(len(weekend_routes()),3)
  for route in weekend_routes():
   s=initial_state();s['scene_background_world']='original_world'
   for item in route['activities']:
    self.assertTrue(resolve_background(item['location'],'original_world')['matched']);s['scene'].update(location=item['location'],character_location=item['location'])
    self.assertTrue(restyle_arrival(s,item)['applied']);self.assertNotIn('未確認',current_appearance(s['appearance'])['clothing'].values())
