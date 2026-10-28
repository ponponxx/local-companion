import json,sqlite3,unittest
from copy import deepcopy
from datetime import datetime
from companion.pyramid import initial_state
from companion.scene_appearance import restyle_arrival,WARDROBES
from companion.scene_background import load_catalog
from companion.scene_catalog_store import SceneCatalogStore
from companion.visual_state import SLOT_NAMES,current_appearance,reset_morning_appearance

class ExpandedWardrobeTests(unittest.TestCase):
 def state(self,location='青葉莊園臥室'):
  # The same reducer data shape as production, with neutral synthetic persona.
  s=initial_state();s['scene'].update(location=location,character_location=location)
  s['scene_background_world']='original_world';s['availability']='free'
  s['appearance_defaults']={'hair':'自然披髮','makeup':'淡妝','clothing':'白色襯衫','accessories':'無','jewelry':'無'}
  return s
 def test_every_catalog_location_has_complete_outfit(self):
  places=load_catalog()['worlds']['original_world']['places'];self.assertEqual(len(places),37)
  for p in places:
   with self.subTest(location=p['name']):
    s=self.state(p['name']);self.assertTrue(restyle_arrival(s,{})['applied'])
    c=current_appearance(s['appearance'])['clothing'];self.assertEqual(set(c),set(SLOT_NAMES));self.assertNotIn('未確認',c.values())
  for look in WARDROBES['original_world']['looks'].values():self.assertEqual(set(look),set(SLOT_NAMES))
 def test_sleep_replaces_incompatible_daily_prose(self):
  s=self.state();restyle_arrival(s,{'availability':'sleep','clothing':'白色襯衫、黑色百褶裙、黑色皮鞋'})
  c=current_appearance(s['appearance'])['clothing']
  self.assertIn('睡衣',c['top']);self.assertIn('睡褲',c['bottom']);self.assertEqual(c['shoes'],'未穿著');self.assertEqual(c['outerwear'],'未穿著')
 def test_training_is_complete_and_protected_manual_change_wins(self):
  s=self.state('星港學院競技場上空');restyle_arrival(s,{'clothing':'黑色飛行服、黑色百褶裙'})
  self.assertIn('運動長褲',current_appearance(s['appearance'])['clothing']['bottom'])
  s['appearance']['clothing']['shoes']={'description':'使用者指定短靴','worn':True}
  restyle_arrival(s,{},protected=('shoes',));self.assertEqual(s['appearance']['clothing']['shoes']['description'],'使用者指定短靴')
 def test_bathroom_never_implies_undressing(self):
  s=self.state('青葉莊園浴室');restyle_arrival(s,{'activity':'在浴室用冷水喚醒感官','clothing':'白色襯衫、銀綠條紋領帶'})
  c=current_appearance(s['appearance'])['clothing'];self.assertIn('晨袍',c['outerwear']);self.assertEqual(c['accessories'],'未穿著');self.assertNotEqual(c['top'],'未穿著')
 def test_new_location_environment_is_persistent_and_gets_fallback(self):
  db=sqlite3.connect(':memory:');catalog=SceneCatalogStore(db,'original_world','neutral')
  catalog.register_plan([{'location':'合成湖畔碼頭'}],[{'name':'合成湖畔碼頭','environment':'outdoor','background':'平靜湖水旁的木製碼頭與石造步道'}],'now')
  reloaded=SceneCatalogStore(db,'original_world','neutral');s=self.state('合成湖畔碼頭');s['scene_wardrobe_locations']=reloaded.wardrobe_locations()
  self.assertTrue(restyle_arrival(s,{})['applied']);self.assertIn('長外套',s['appearance']['clothing']['outerwear']['description'])
  db.close()
 def test_unknown_location_without_environment_preserves_facts(self):
  s=self.state('尚未定義的地點');before=deepcopy(s['appearance']);self.assertFalse(restyle_arrival(s,{})['applied']);self.assertEqual(before,s['appearance'])
 def test_six_oclock_does_not_redress_sleeping_character(self):
  s=self.state();s['now']='2026-10-21T02:00:00+08:00';s['availability']='sleep';restyle_arrival(s,{'availability':'sleep'})
  before=deepcopy(s['appearance']);self.assertFalse(reset_morning_appearance(s,datetime.fromisoformat('2026-10-21T06:00:00+08:00')));self.assertEqual(before,s['appearance'])
 def test_awake_morning_grooms_without_replacing_scene_clothes(self):
  s=self.state('青葉莊園實驗室');s['now']='2026-10-21T05:59:00+08:00';s['life_current']={'activity':'魔藥研究'}
  restyle_arrival(s,{});before=deepcopy(s['appearance']['clothing']);s['appearance']['hair']='盤髮'
  self.assertTrue(reset_morning_appearance(s,datetime.fromisoformat('2026-10-21T06:00:00+08:00')));self.assertEqual(before,s['appearance']['clothing']);self.assertEqual(s['appearance']['hair'],'自然披髮')
