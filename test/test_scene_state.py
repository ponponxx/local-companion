import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from companion.store import Store
from companion.app import App
from companion.config import Config


def event(op, subject, field, value, evidence):
    return dict(operation=op, subject=subject, field=field, value=value, evidence=evidence)


class SceneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'test.sqlite3')

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def apply(self, text, events, role='user'):
        mid = self.store.add_message(role, text)
        with self.store.db:
            self.store.scene.apply(mid, events, self.store.scene.snapshot()['revision'])
        return mid

    def open(self):
        return self.apply('我們在花園。我把妳的頭髮盤好了。', [
            event('open', 'scene', 'location', '花園', '我們在花園'),
            event('set', 'character', 'hair', '盤髮', '我把妳的頭髮盤好了'),
        ])

    def test_actor_separation_persistence_and_provenance(self):
        mid = self.open()
        facts = self.store.scene.snapshot()['facts']
        self.assertEqual(facts['character.hair']['source_message_id'], mid)
        self.assertNotIn('user.hair', facts)
        self.store.close()
        self.store = Store(Path(self.tmp.name) / 'test.sqlite3')
        self.assertEqual(self.store.scene.writer_view()['facts']['character.hair'], '盤髮')

    def test_unknown_evidence_and_assistant_prose_cannot_establish_facts(self):
        self.open()
        self.apply('早安', [event('set', 'character', 'hair', '短髮', '剪好了')])
        self.apply('我已經剪短頭髮', [event('set', 'character', 'hair', '短髮', '剪短頭髮')], 'assistant')
        self.assertEqual(self.store.scene.writer_view()['facts']['character.hair'], '盤髮')

    def test_new_scene_and_close_clear_previous_mutable_facts(self):
        self.open()
        self.apply('換個場景，我們在書房', [event('open', 'scene', 'location', '書房', '我們在書房')])
        self.assertNotIn('character.hair', self.store.scene.writer_view()['facts'])
        self.apply('結束角色扮演', [event('close', 'scene', 'interaction', '', '結束角色扮演')])
        self.assertEqual(self.store.scene.writer_view()['mode'], 'remote_chat')
        self.assertEqual(self.store.scene.writer_view()['facts'], {})

    def test_revision_rejects_stale_analysis_and_duplicate_is_idempotent(self):
        mid = self.open()
        before = self.store.scene.snapshot()
        self.assertFalse(self.store.scene.apply(mid, [], 0))
        self.store.scene.apply(mid, [event('open', 'scene', 'location', '花園', '我們在花園')], before['revision'])
        self.assertEqual(self.store.scene.snapshot(), before)

    def test_world_projection_wins_over_life_and_legacy_conversation(self):
        self.open()
        self.store.apply_conversation_state({'scene_status': 'active', 'scene': '錯誤舊場景'})
        self.store.add_message('user', '繼續聊')
        system = self.store.context()[0]['content']
        self.assertIn('<scene_facts>', system)
        self.assertIn('盤髮', system)
        self.assertNotIn('錯誤舊場景', self.store.conversation_state()['scene'])
        self.assertIn('花園', system)

    def test_reset_clears_world_without_changing_identity(self):
        self.open()
        self.store.update_persona('name', '角色甲')
        self.store.reset()
        self.assertEqual(self.store.scene.snapshot()['status'], 'none')
        self.assertEqual(self.store.persona()['name'], '角色甲')

    def test_no_silent_scene_creation_from_set(self):
        self.apply('妳盤髮', [event('set', 'character', 'hair', '盤髮', '妳盤髮')])
        self.assertEqual(self.store.scene.snapshot()['facts'], {})

    def test_camera_uses_scene_owned_entity_id(self):
        self.open()
        self.apply('相機在妳手上', [event('set', 'character', 'camera_holder', '妳', '相機在妳手上')])
        self.assertNotIn('character.camera_holder', self.store.scene.writer_view()['facts'])
        self.apply('相機在妳手上', [event('set', 'scene', 'camera_holder', 'character', '相機在妳手上')])
        self.assertEqual(self.store.scene.writer_view()['facts']['scene.camera_holder'], 'character')


class ExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_extraction_and_visual_share_authoritative_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'test.sqlite3')
            llm = AsyncMock()
            config = Config.load(require_credentials=False)
            app = App(config, store, AsyncMock(), llm, AsyncMock(), AsyncMock())
            text = '我們在花園，妳頭髮盤好了'
            mid = store.add_message('user', text)
            llm.generate.return_value = json.dumps({'transition': 'open',
                'transition_evidence': '我們在花園', 'events': [
                event('set', 'scene', 'location', '花園', '我們在花園'),
                event('set', 'character', 'hair', '盤髮', '妳頭髮盤好了'),
            ]})
            await app.extract_scene_events(mid, text)
            prompt = app.visual_prompt('selfie', '拍一張', {'hair_state': '披髮', 'scene': '教室'})
            self.assertIn('妝髮／髮型：盤髮', prompt)
            self.assertNotIn('教室', prompt)
            self.assertNotIn('妝髮／髮型：披髮', prompt)
            previous = store.scene.snapshot()
            llm.generate.return_value = 'broken JSON'
            await app.extract_scene_events(mid, text)
            self.assertEqual(store.scene.snapshot(), previous)
            store.close()
