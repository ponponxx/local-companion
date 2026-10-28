import unittest
from copy import deepcopy
from companion.scene_background import load_catalog, resolve_background, location_options


class SceneBackgroundTests(unittest.TestCase):
    def test_air_and_ground_are_distinct_not_substring_matches(self):
        air = resolve_background('星港學院競技場上空', 'original_world')
        ground = resolve_background('星港學院競技場', 'original_world')
        self.assertEqual(air['environment'], 'air');self.assertEqual(ground['environment'], 'outdoor')
        self.assertIn('下方',air['background']);self.assertNotIn('壁爐',air['background'])
        self.assertNotIn('銀製燭台',air['background']);self.assertNotIn('石造牆壁',air['background'])

    def test_unknown_and_ambiguous_location_do_not_inherit_manor(self):
        for location in ['浴室', '競技場附近的一間旅館', '莊園臥室窗外']:
            result=resolve_background(location,'original_world')
            self.assertFalse(result['matched']);self.assertEqual(result['background'],location)
        self.assertEqual(resolve_background('', 'original_world')['background'],'地點未確認')

    def test_english_alias_and_stable_id_resolve_same_template(self):
        a=resolve_background('Manor Bedroom','original_world')
        self.assertEqual(a['background'],resolve_background('manor_bedroom','original_world')['background'])
        self.assertEqual(a['background'],resolve_background('莊園臥室','original_world')['background'])
        self.assertNotIn('銀製燭台',resolve_background('莊園花園','original_world')['background'])

    def test_plan_options_have_ids_but_not_mutable_state(self):
        catalog=load_catalog();before=deepcopy(catalog)
        options=location_options('original_world',catalog)
        self.assertEqual(len(options),37)
        self.assertTrue(all(set(o)=={'id','name'} for o in options))
        self.assertEqual(catalog,before)
        self.assertEqual(location_options('unknown_world',catalog),[])
        self.assertFalse(resolve_background('莊園臥室','unknown_world',catalog)['matched'])
