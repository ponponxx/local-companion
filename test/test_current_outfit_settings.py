"""Current-clothes controls and arrival baseline regression; no model needed."""
import json
import unittest
from copy import deepcopy
from companion.visual_state import current_appearance,clothing_baseline
from companion.scene_appearance import restyle_arrival,migrate_arrival_clothing
from companion.pyramid import initial_state,PyramidCore,DecisionTrace
from test_telegram_settings import SettingsTests as BaseSettings

class WardrobeComposition(unittest.TestCase):
    def state(self):
        s=initial_state('2026-10-19T12:00:00+08:00')
        s['scene'].update(location='青葉莊園書房',character_location='青葉莊園書房',pending=None)
        return s

    def test_schedule_layers_overlay_preset_without_losing_shoes(self):
        s=self.state()
        restyle_arrival(s,{'clothing':'白襯衫、炭灰色V領針織背心、銀綠條紋領帶、黑色百褶裙'})
        clothes=current_appearance(s['appearance'])['clothing']
        self.assertEqual(clothes['top'],'白襯衫、炭灰色V領針織背心')
        self.assertEqual(clothes['bottom'],'黑色百褶裙')
        self.assertEqual(clothes['shoes'],'黑色平底鞋')
        self.assertEqual(clothes['underwear'],'日常內著')  # Authored complete wardrobe sample.
        self.assertEqual(s['appearance']['legacy_outfit'],'')

    def test_preferences_and_alternatives_remain_unconfirmed(self):
        for text in ('白襯衫、黑襯衫','白襯衫或背心','偏好白襯衫、黑鞋'):
            self.assertEqual(clothing_baseline(text,allow_layers=True)[0].get('top'),None)
        self.assertNotIn('top',clothing_baseline('白襯衫、灰色背心')[0])

    def test_dress_does_not_assert_preset_pants(self):
        s=self.state();restyle_arrival(s,{'clothing':'藍色洋裝'})
        c=current_appearance(s['appearance'])['clothing']
        self.assertEqual(c['dress'],'藍色洋裝');self.assertEqual(c['bottom'],'未穿著')  # Full authored dress outfit removes trousers.

    def test_old_arrival_migration_preserves_removed_override_and_pending(self):
        s=self.state();s['scene']['pending']={'destination':'其他地方'}
        s['appearance'].update(legacy_outfit='白襯衫、灰色背心',clothing={'shoes':{'worn':False}})
        s['appearance_transition']={'location':'青葉莊園書房','source':'daily_schedule'}
        before=deepcopy(s['scene'])
        self.assertTrue(migrate_arrival_clothing(s))
        c=current_appearance(s['appearance'])['clothing']
        self.assertEqual(c['top'],'白襯衫、灰色背心');self.assertEqual(c['shoes'],'未穿著')
        self.assertEqual(s['scene'],before);self.assertFalse(migrate_arrival_clothing(s))

    def test_new_manual_setting_blocks_old_response_but_not_new_events(self):
        s=self.state();s['appearance']['clothing']={'top':{'description':'藍襯衫','worn':True}}
        s['_clothing_edits']={'top':'2026-10-19T12:10:00+08:00'}
        e=dict(kind='appearance',actor='character',target='character',stage='completed',field='top',value='白襯衫',evidence='證據',strength=1)
        core=PyramidCore()
        core.apply(s,[e],'assistant','證據','old',DecisionTrace(),at='2026-10-19T12:05:00+08:00')
        self.assertEqual(current_appearance(s['appearance'])['clothing']['top'],'藍襯衫')
        core.apply(s,[e],'assistant','證據','new',DecisionTrace(),at='2026-10-19T12:11:00+08:00')
        self.assertEqual(current_appearance(s['appearance'])['clothing']['top'],'白襯衫')

    def test_old_move_cannot_restyle_over_new_manual_slot(self):
        s=self.state();s['appearance']['clothing']={'top':{'description':'藍襯衫','worn':True}}
        s['_clothing_edits']={'top':'2026-10-19T12:10:00+08:00'}
        e=dict(kind='move',actor='character',target='character',stage='completed',field='location',value='莊園花園',evidence='證據',strength=1)
        PyramidCore().apply(s,[e],'assistant','證據','oldmove',DecisionTrace(),at='2026-10-19T12:05:00+08:00')
        self.assertEqual(current_appearance(s['appearance'])['clothing']['top'],'藍襯衫')

class CurrentClothesSettings(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=BaseSettings.asyncSetUp
    asyncTearDown=BaseSettings.asyncTearDown
    text=BaseSettings.text
    callback=BaseSettings.callback
    field=BaseSettings.field

async def test_current_clothes(self):
    before=deepcopy(self.store.appearance());profile=deepcopy(self.store.profile_facts())
    await self.text('/settings',80);await self.callback('g.appearance');await self.callback('g.current_clothing')
    await self.field('worn.top');await self.text('白色襯衫＋灰色背心',81)
    s=self.app.pyramid.load()
    self.assertEqual(current_appearance(s['appearance'])['clothing']['top'],'白色襯衫＋灰色背心')
    await self.field('worn.shoes');await self.text('未穿著',82)
    self.assertEqual(current_appearance(self.app.pyramid.load()['appearance'])['clothing']['shoes'],'未穿著')
    await self.field('worn.top');await self.text('未確認',83)
    self.assertEqual(current_appearance(self.app.pyramid.load()['appearance'])['clothing']['top'],'未確認')
    self.assertEqual(self.store.appearance(),before);self.assertEqual(self.store.profile_facts(),profile)
    self.assertEqual(self.llm.calls,[]);self.assertEqual(self.store.recent(),[])

async def test_reset(self):
    s=self.app.pyramid.load();s['scene'].update(location='莊園書房',character_location='莊園書房')
    self.app.pyramid.save_clock(s)
    await self.text('/outfit top 未穿著',80)
    await self.text('/settings',81);await self.callback('g.current_clothing');await self.callback('outfit.reset')
    c=current_appearance(self.app.pyramid.load()['appearance'])['clothing']
    self.assertEqual(c['top'],'白色襯衫');self.assertEqual(c['shoes'],'黑色平底鞋')
    self.assertEqual(self.llm.calls,[])

CurrentClothesSettings.test_current_clothes=test_current_clothes
CurrentClothesSettings.test_reset=test_reset
# Do not expose the imported TestCase to unittest discovery.
del BaseSettings
