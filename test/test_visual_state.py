import unittest
from datetime import datetime
from companion.visual_state import reset_morning_appearance, clothing_description

class VisualStateTests(unittest.TestCase):
    def test_reset_after_six_once_preserves_other_state(self):
        s={'appearance_defaults':{'hair':'披髮','makeup':'淡妝','clothing':'白襯衫','accessories':'耳環'},
           'appearance':{'body':'固定身份','hair':'盤髮','clothing':{'socks':{'worn':False}},'accessories':'無'},
           'presentation':{'pose':{'value':'坐著'}},'scene':{'location':'臥室'}}
        def at(v):return datetime.fromisoformat(v+'+08:00')
        self.assertFalse(reset_morning_appearance(s,at('2026-10-15T23:00:00')))
        self.assertFalse(reset_morning_appearance(s,at('2026-10-16T05:59:00')))
        self.assertTrue(reset_morning_appearance(s,at('2026-10-16T06:00:00')))
        self.assertEqual(s['appearance']['hair'],'披髮')
        self.assertEqual(s['appearance']['body'],'固定身份')
        self.assertEqual(s['scene']['location'],'臥室')
        s['appearance']['hair']='盤髮'
        self.assertFalse(reset_morning_appearance(s,at('2026-10-16T12:00:00')))
        self.assertEqual(s['appearance']['hair'],'盤髮')

    def test_slot_projection_contains_no_python_dict(self):
        result=clothing_description({'legacy_outfit':'白襯衫；黑襪', 'clothing':{'socks':{'description':'off','worn':False,'source':'id'}}})
        self.assertIn('白襯衫',result)
        self.assertIn('襪子：未穿著',result)
        self.assertNotIn('{',result)

    def test_chinese_appearance_fields_and_separate_jewelry(self):
        import tempfile
        from pathlib import Path
        from companion.store import Store
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'test.sqlite3')
            try:
                store.update_appearance('固定外觀','成年白金髮女性')
                store.update_appearance('妝髮','盤髮 | 淡妝')
                store.update_appearance('飾品','銀耳環')
                store.update_appearance('配件','黑色皮包')
                a=store.appearance()
                self.assertEqual((a['hair'],a['makeup']),('盤髮','淡妝'))
                self.assertEqual(a['jewelry'],'銀耳環')
                self.assertEqual(a['accessories'],'黑色皮包')
            finally: store.close()
