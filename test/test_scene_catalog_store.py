import sqlite3
import tempfile
from pathlib import Path
import unittest
from companion.scene_catalog_store import SceneCatalogStore


class SceneCatalogStoreTests(unittest.TestCase):
    def test_persists_across_reopen_but_does_not_cross_characters(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'catalog.sqlite3'
            with sqlite3.connect(path) as db:
                store=SceneCatalogStore(db,'original_world','character A')
                detail={'name':'湖畔碼頭','environment':'outdoor','background':'湖邊的木製碼頭沿著水岸延伸，遠方可見山丘與蘆葦。'}
                store.register_plan([{'location':'湖畔碼頭'}],[detail],'2026-10-18T10:00:00+08:00')
            with sqlite3.connect(path) as db:
                a=SceneCatalogStore(db,'original_world','character A');b=SceneCatalogStore(db,'original_world','character B')
                self.assertTrue(a.resolve('湖畔碼頭')['matched']);self.assertFalse(b.resolve('湖畔碼頭')['matched'])
                self.assertFalse(a.resolve('湖畔碼頭')['semantically_verified'])
                self.assertEqual(a.register_plan([{'location':'湖畔碼頭'}],[],'later'),0)

    def test_incomplete_batch_and_conflicts_leave_no_partial_rows(self):
        with sqlite3.connect(':memory:') as db:
            store=SceneCatalogStore(db,'original_world','A')
            detail={'name':'湖畔碼頭','environment':'outdoor','background':'湖邊的木製碼頭沿著水岸延伸，遠方可見山丘與蘆葦。'}
            with self.assertRaises(ValueError):store.register_plan([{'location':'湖畔碼頭'},{'location':'其他未知地點'}],[detail],'now')
            self.assertEqual(db.execute('SELECT count(*) FROM scene_background_catalog').fetchone()[0],0)
            conflict=dict(detail,environment='indoor')
            with self.assertRaises(ValueError):store.register_plan([{'location':'湖畔碼頭'}],[detail,conflict],'now')
            self.assertEqual(db.execute('SELECT count(*) FROM scene_background_catalog').fetchone()[0],0)

    def test_existing_environment_is_not_overwritten_or_duplicated(self):
        with sqlite3.connect(':memory:') as db:
            store=SceneCatalogStore(db,'original_world','A')
            old=store.resolve('星港學院競技場上空')['background']
            store.register_plan([{'location':'星港學院競技場上空'}],[{
                'name':'星港學院競技場上空','environment':'indoor','background':'這是一個不同背景，內部有石牆與家具和壁爐。'}],'now')
            self.assertEqual(store.resolve('星港學院競技場上空')['background'],old)
            self.assertEqual(db.execute('SELECT count(*) FROM scene_background_catalog').fetchone()[0],0)
