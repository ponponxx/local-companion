import unittest
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from companion.visual_state import current_appearance,is_removed
from companion.visual_lighting import snapshot_lighting,timeless_background
from companion.scene_appearance import restyle_arrival
from companion.pyramid import initial_state,PyramidCore,DecisionTrace
from companion.app import App

STAMP='2026-10-18T21:04:00+08:00'

def event(kind,field,value,actor='character'):
    return dict(kind=kind,actor=actor,target='character',stage='completed',field=field,value=value,evidence='證據',strength=1)

class ArrivalVisualTests(unittest.TestCase):
    def state(self):
        s=initial_state(STAMP)
        s['scene'].update(location='莊園書房',character_location='莊園書房',pending=None)
        s['appearance'].update(body='28歲成年角色',hair='盤髮',clothing={'shoes':{'worn':False,'description':'off'}})
        s['appearance_defaults']={'hair':'整齊披髮','makeup':'淡妝','accessories':'皮包','jewelry':'銀耳環'}
        return s

    def test_removed_aliases_stay_distinct_from_unknown(self):
        for v in ['off','未穿','沒穿','沒有穿','已脫下','not worn']:
            self.assertTrue(is_removed(v))
            a=current_appearance({'clothing':{'shoes':v}})['clothing']
            self.assertEqual(a['shoes'],'未穿著');self.assertEqual(a['socks'],'未確認')
        self.assertFalse(is_removed('還沒脫下'))

    def test_reducer_removal_is_explicit(self):
        s=self.state();PyramidCore().apply(s,[event('appearance','shoes','沒穿')],'user','證據','1',DecisionTrace())
        self.assertFalse(s['appearance']['clothing']['shoes']['worn'])

    def test_confirmed_move_restores_destination_outfit_only_character(self):
        s=self.state();other=deepcopy(s['user_appearance'])
        PyramidCore().apply(s,[event('move','location','莊園花園')],'user','證據','1',DecisionTrace())
        self.assertEqual(current_appearance(s['appearance'])['clothing']['shoes'],'黑色短靴')
        self.assertEqual(s['appearance']['hair'],'整齊披髮');self.assertEqual(s['appearance']['body'],'28歲成年角色')
        self.assertEqual(s['user_appearance'],other)

    def test_same_turn_explicit_clothes_win_in_either_event_order(self):
        for reverse in [False,True]:
            s=self.state();events=[event('appearance','shoes','off'),event('move','location','莊園花園')]
            if reverse:events.reverse()
            PyramidCore().apply(s,events,'user','證據','1',DecisionTrace())
            self.assertEqual(current_appearance(s['appearance'])['clothing']['shoes'],'未穿著')

    def test_same_place_relative_and_user_moves_do_not_restyle(self):
        for e in [event('move','location','莊園書房'),event('move','relative_position','靠近'),event('move','location','莊園花園','user')]:
            s=self.state();old=deepcopy(s['appearance'])
            PyramidCore().apply(s,[e],'user','證據','1',DecisionTrace())
            self.assertEqual(s['appearance'],old)

    def test_unknown_location_and_other_world_preserve(self):
        for location,world in [('未知地點','original_world'),('莊園書房','modern')]:
            s=self.state();s['scene']['character_location']=location;s['scene_background_world']=world
            old=deepcopy(s['appearance']);self.assertFalse(restyle_arrival(s)['applied']);self.assertEqual(s['appearance'],old)

    def test_schedule_outfit_wins_and_bathroom_is_not_undressing(self):
        s=self.state();s['scene']['character_location']='莊園浴室';restyle_arrival(s)
        self.assertEqual(current_appearance(s['appearance'])['clothing']['top'],'素色棉質上衣')
        restyle_arrival(s,{'clothing':'藍色襯衫；白色長褲','hair':'束髮'})
        self.assertEqual(current_appearance(s['appearance'])['clothing']['top'],'藍色襯衫')
        self.assertEqual(s['appearance']['hair'],'束髮')

    def test_lighting_boundaries_and_host_timezone(self):
        for hour,period in [(4,'夜晚'),(5,'清晨'),(7,'白天'),(17,'黃昏'),(19,'夜晚')]:
            at=datetime.fromisoformat(f'2026-10-18T{hour:02}:04:00+08:00')
            self.assertEqual(snapshot_lighting(at,{'environment':'air'})['period'],period)
        self.assertEqual(snapshot_lighting('2026-10-18T13:04:00+00:00',{})['period'],'夜晚')

    def test_fixed_daylight_old_background_not_rewritten_or_combined(self):
        old={'location':'庭院','background':'陽光灑落庭院石桌','environment':'outdoor'}
        new=timeless_background(old)
        self.assertEqual(new['background'],'庭院');self.assertTrue(new['temporal_fallback'])
        self.assertEqual(old['background'],'陽光灑落庭院石桌')
        self.assertEqual(timeless_background({'background':'石桌與花圃'}),{'background':'石桌與花圃'})

    def test_image_prompt_authority_and_frozen_night(self):
        background={'matched':True,'location':'花園','background':'花園石徑','environment':'outdoor'}
        snap={'appearance_authoritative':True,'identity_anchors':'28歲成年角色','current_clothing':'鞋子：未穿著；上衣：白襯衫',
            'current_accessories':'無','current_jewelry':'無','current_scene':'花園','background_context':background,
            'lighting':snapshot_lighting(STAMP,background),'scene_facts':{'facts':{'character.hair':'盤髮'}}}
        prompt=App.visual_prompt(SimpleNamespace(),'user_photo','拍一張',{'clothing':'黑色靴子','accessories':'魔杖','hair_state':'披髮'},snapshot=snap)
        self.assertIn('鞋子：未穿著',prompt);self.assertNotIn('黑色靴子',prompt);self.assertNotIn('魔杖',prompt)
        self.assertIn('21:04',prompt);self.assertIn('夜晚',prompt);self.assertNotIn('窗',prompt)
        self.assertIn('盤髮',prompt)

class VisualPlannerOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_formal_schema_excludes_state_owned_appearance(self):
        from unittest.mock import AsyncMock
        import json
        llm=SimpleNamespace(generate=AsyncMock(return_value=json.dumps({'include_character':True,'pose':'站立','expression':'微笑','camera':'','identity_change':'','identity_change_evidence':'','view_direction':'front','camera_height':'eye_level'})))
        owner=SimpleNamespace(llm=llm,store=SimpleNamespace(recent=lambda *a,**kw:[]))
        snap={'appearance_authoritative':True,'current_clothing':'黑色外套','scene_facts':{'facts':{}}}
        await App.plan_visual(owner,'user_photo','拍一張','',snap)
        call=llm.generate.await_args
        schema=call.kwargs['response_schema']
        for field in ['clothing','hair_state','jewelry','accessories']:
            self.assertNotIn(field,schema['properties']);self.assertNotIn(field,schema['required'])
        self.assertNotIn('may be adapted',call.args[0][0]['content'])
