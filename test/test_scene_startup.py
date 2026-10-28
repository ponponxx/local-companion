"""Startup must establish state from plans, never from Writer inventions."""
from copy import deepcopy
import unittest
from companion.pyramid import initial_state, PyramidCore, DecisionTrace
from companion.visual_state import current_appearance
from companion.pyramid_runner import writer_contract


class StartupTests(unittest.TestCase):
    def state(self):
        s=initial_state('2026-10-20T08:00:00+08:00')
        s['appearance']['legacy_outfit']='白襯衫、炭灰色V領針織背心、黑色百褶裙'
        s['last_user_at']=s['now']
        s.update(scene_notice_delivery='external',scene_notifications_enabled=False)
        s['schedule']=[{'at':'2026-10-20T07:30:00+08:00','location':'青葉莊園書房','activity':'閱讀',
                        'availability':'free','clothing':'白襯衫、炭灰色V領針織背心','accessories':'無'}]
        return s

    def test_concrete_layers_are_not_ambiguous_alternatives(self):
        s=self.state()
        self.assertEqual(current_appearance(s['appearance'])['clothing']['top'],'白襯衫、炭灰色V領針織背心')
        s['appearance']['clothing']['top']={'worn':False,'description':'已脫下'}
        self.assertEqual(current_appearance(s['appearance'])['clothing']['top'],'未穿著')
        self.assertEqual(current_appearance({'legacy_outfit':'白襯衫、黑襯衫'})['clothing']['top'],'未確認')

    def test_initial_scene_ignores_idle_and_notification_gate(self):
        s=self.state();core=PyramidCore();trace=DecisionTrace()
        core.advance(s,s['now'],trace)
        self.assertEqual(s['scene']['character_location'],'青葉莊園書房')
        self.assertEqual(s['scene']['user_location'],'')
        self.assertEqual(s['appearance']['clothing']['top']['description'],'白襯衫、炭灰色V領針織背心')
        self.assertEqual(s['scene_history'][-1]['phase'],'initialized')
        self.assertFalse(s['notices']);self.assertIsNone(s['scene']['pending'])
        before=deepcopy(s['scene_history']);core.advance(s,s['now'],trace)
        self.assertEqual(s['scene_history'],before)

    def test_does_not_teleport_confirmed_scene_or_finish_departure(self):
        s=self.state();s['scene'].update(location='青葉莊園花園',character_location='青葉莊園花園',status='active')
        PyramidCore().advance(s,s['now'],DecisionTrace())
        self.assertEqual(s['scene']['character_location'],'青葉莊園花園')
        self.assertIsNone(s['scene']['pending'])

    def test_yesterday_schedule_does_not_initialize_today(self):
        s=self.state();s['schedule'][0]['at']='2026-10-19T23:00:00+08:00'
        PyramidCore().advance(s,s['now'],DecisionTrace())
        self.assertEqual(s['scene']['status'],'none');self.assertFalse(s['notices'])

    def test_writer_gets_actual_upcoming_plans_without_ids(self):
        s=self.state();s['schedule'].append({'at':'2026-10-20T12:00:00+08:00','location':'青葉莊園餐廳','activity':'午餐','id':'hidden'})
        core=PyramidCore();d=core.decide(s,[],{'missing_detail':'','appraisals':[]},DecisionTrace())
        c=writer_contract(s,d)
        self.assertEqual(c['daily_plan']['upcoming'],[{'time':'12:00','location':'青葉莊園餐廳','activity':'午餐'}])
        self.assertNotIn('character_location',c['current_facts']['scene'])
        s['schedule']=[]
        self.assertEqual(writer_contract(s,d)['daily_plan']['status'],'unavailable')

class SampleTests(unittest.TestCase):
    def test_sample_has_all_slots_without_claiming_past_or_user_location(self):
        from companion.character_baseline import initialize_character
        s=initial_state('2026-10-20T09:00:00+08:00')
        s['scene_background_world']='original_world'
        s['appearance_defaults']={'clothing':'白襯衫、灰色背心、黑色百褶裙','hair':'馬尾'}
        self.assertTrue(initialize_character(s))
        self.assertEqual(s['scene']['character_location'],'青葉莊園臥室')
        self.assertEqual(s['scene']['user_location'],'')
        self.assertNotIn('未確認',current_appearance(s['appearance'])['clothing'].values())
        self.assertEqual(s['scene_history'][0]['phase'],'initialized')
        self.assertFalse(s['character_baseline']['past_events_inferred'])
        self.assertFalse(initialize_character(s))

    def test_known_scene_and_pending_trip_are_not_replaced_by_sample(self):
        from companion.character_baseline import initialize_character
        s=initial_state('2026-10-20T09:00:00+08:00')
        s['scene'].update(location='花園',character_location='花園',status='active')
        before=deepcopy(s)
        self.assertFalse(initialize_character(s));self.assertEqual(s,before)
        s['scene'].update(location='',character_location='',status='none',pending={'location':'書房'})
        self.assertFalse(initialize_character(s))

    def test_morning_and_arrival_maintain_complete_authored_outfit(self):
        from companion.character_baseline import initialize_character
        from companion.visual_state import reset_morning_appearance
        from companion.scene_appearance import restyle_arrival
        from datetime import datetime
        s=initial_state('2026-10-20T09:00:00+08:00');s['scene_background_world']='original_world'
        s['appearance_defaults']={'clothing':'白襯衫、灰色背心','hair':'馬尾'}
        initialize_character(s);s['appearance_day']='2026-10-19'
        reset_morning_appearance(s,datetime.fromisoformat(s['now']))
        self.assertNotIn('未確認',current_appearance(s['appearance'])['clothing'].values())
        s['scene'].update(location='青葉莊園書房',character_location='青葉莊園書房')
        restyle_arrival(s,{'clothing':'藍色洋裝'})
        c=current_appearance(s['appearance'])['clothing']
        self.assertNotIn('未確認',c.values());self.assertEqual(c['bottom'],'未穿著')
