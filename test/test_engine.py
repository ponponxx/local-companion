import json
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

from companion.engine import behavior_policy, utcnow
from companion.store import Store, DEFAULT_TRAITS, DEFAULT_RELATIONSHIP


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name) / 'memory.sqlite3')
        self.engine = self.store.engine

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def pair(self, user='你現在有點累，先休息一下', answer='我在家休息。'):
        u = self.store.add_message('user', user)
        a = self.store.add_message('assistant', answer)
        return u, a

    def seed(self):
        with self.store.db:
            self.engine.emit('observed', 'test:location', {'facts': [{'key': 'location', 'value': '家中'}]})
        return self.engine.knowledge()[0]['id']

    def test_delivery_is_not_understanding_and_ack_needs_prior_delivery(self):
        fact = self.seed()
        u, a = self.pair()
        update = {'knowledge_ops': [{'action': 'disclosed', 'fact_id': fact, 'evidence': '我在家休息'}]}
        with self.store.db:
            self.engine.analyze(1, u, a, update)
        self.assertFalse(self.engine.knowledge()[0]['disclosed'])
        self.store.mark_delivered(a)
        with self.store.db:
            self.engine.analyze(1, u, a, update)
        self.assertTrue(self.engine.knowledge()[0]['disclosed'])
        self.assertFalse(self.engine.knowledge()[0]['acknowledged'])
        u, a = self.pair('知道你在家了，休息吧。', '好。')
        with self.store.db:
            self.engine.analyze(2, u, a, {'knowledge_ops': [{'action': 'acknowledged', 'fact_id': fact,
                                                          'evidence': '知道你在家了'}]})
        self.assertTrue(self.engine.knowledge()[0]['acknowledged'])

    def test_wrong_evidence_cannot_mark_knowledge(self):
        fact = self.seed()
        u, a = self.pair('嗯', '我在家。')
        self.store.mark_delivered(a)
        with self.store.db:
            self.engine.analyze(1, u, a, {'knowledge_ops': [{'action': 'disclosed', 'fact_id': fact,
                                                          'evidence': '我在公司'}]})
        self.assertFalse(self.engine.knowledge()[0]['disclosed'])

    def test_temporal_fact_replacement_does_not_inherit_disclosure(self):
        fact = self.seed()
        u, a = self.pair(answer='我在家。')
        self.store.mark_delivered(a)
        with self.store.db:
            self.engine.analyze(1, u, a, {'knowledge_ops': [{'action': 'disclosed', 'fact_id': fact, 'evidence': '我在家'}]})
            self.engine.emit('observed', 'test:new-location', {'facts': [{'key': 'location', 'value': '公園'}]})
        current = self.engine.knowledge()[0]
        self.assertEqual(current['value'], '公園')
        self.assertFalse(current['disclosed'])
        self.assertIsNotNone(self.store.db.execute('SELECT valid_to FROM character_knowledge WHERE id=?', (fact,)).fetchone()[0])

    def test_replay_is_deterministic_and_events_are_idempotent(self):
        self.seed()
        before = self.engine.knowledge()
        with self.store.db:
            self.engine.emit('observed', 'test:location', {'facts': [{'key': 'location', 'value': '錯誤值'}]})
        self.assertEqual(before, self.engine.knowledge())
        self.engine.rebuild()
        self.assertEqual(before, self.engine.knowledge())

    def test_interaction_override_survives_calendar_tick_then_expires(self):
        u, a = self.pair('你在公園休息吧', '好')
        with self.store.db:
            self.engine.analyze(1, u, a, {'evidence': '你在公園休息吧', 'state': {'location': '公園', 'activity': '休息'}})
            self.engine.tick(utcnow(), {'location': '公司', 'activity': '工作', 'availability': 'busy'})
        self.assertEqual(self.engine.state()['location'], '公園')
        with self.store.db:
            self.engine.tick(utcnow() + timedelta(hours=3), {'location': '公司', 'activity': '工作', 'availability': 'busy'})
        self.assertEqual(self.engine.state()['location'], '公司')

    def test_time_progression_and_state_delta_are_bounded(self):
        u, a = self.pair()
        now = utcnow()
        with self.store.db:
            self.engine.analyze(1, u, a, {'evidence': '先休息一下', 'state': {'stress_delta': 9999, 'energy_delta': float('nan')}})
        state = self.engine.state(now)
        self.assertLessEqual(state['stress'], 30.01)
        later = self.engine.state(now + timedelta(hours=4))
        self.assertLess(later['stress'], state['stress'])
        self.assertGreater(later['energy'], state['energy'])

    def test_old_review_does_not_overwrite_current_state(self):
        u, a = self.pair('你在公園', '好')
        review = self.store.queue_relationship_review('你在公園', '好', u, a)
        self.pair('新話題', '新的回答')
        self.store.apply_relationship_analysis(review, {}, '', conversation_state={'topic': '舊話題'},
            engine_update={'evidence': '你在公園', 'state': {'location': '公園'}})
        self.assertNotEqual(self.engine.state()['location'], '公園')
        self.assertNotEqual(self.store.conversation_state()['topic'], '舊話題')

    def test_reset_removes_derived_evidence_and_knowledge(self):
        self.seed()
        self.pair('秘密', '知道')
        self.store.reset()
        self.assertEqual(self.engine.knowledge(), [])
        self.engine.rebuild()
        self.assertNotIn('秘密', json.dumps([dict(r) for r in self.store.db.execute('SELECT * FROM character_events')]))

    def test_unconfirmed_delivery_cannot_become_shared_context(self):
        self.store.add_message('user', '你在哪裡？')
        a = self.store.add_message('assistant', '尚未確認送達的回答', pending_delivery=True)
        self.assertNotIn('尚未確認送達的回答', json.dumps(self.store.context(), ensure_ascii=False))
        self.store.mark_delivered(a)
        self.assertIn('尚未確認送達的回答', json.dumps(self.store.context(), ensure_ascii=False))

    def test_conflict_and_stress_suppress_teasing_despite_high_trait(self):
        traits = {**DEFAULT_TRAITS, 'teasing': 100}
        rel = {**DEFAULT_RELATIONSHIP, 'comfort': 90, 'trust': 90, 'affection': 90}
        state = {'energy': 80, 'stress': 10}
        normal = behavior_policy(traits, rel, state)
        conflict = behavior_policy(traits, {**rel, 'tension': 90}, state)
        stressed = behavior_policy(traits, rel, {**state, 'stress': 90})
        self.assertEqual(conflict['priority'], 'repair')
        self.assertLess(conflict['teasing'], normal['teasing'])
        self.assertLess(stressed['teasing'], normal['teasing'])
        self.assertGreater(normal['warmth'], conflict['warmth'])

    def test_low_energy_reduces_initiative_and_expression(self):
        high = behavior_policy(DEFAULT_TRAITS, DEFAULT_RELATIONSHIP, {'energy': 90, 'stress': 20})
        low = behavior_policy(DEFAULT_TRAITS, DEFAULT_RELATIONSHIP, {'energy': 10, 'stress': 20})
        self.assertLess(low['initiative'], high['initiative'])
        self.assertLess(low['expression'], high['expression'])

    def test_every_trait_has_an_observable_policy_axis(self):
        state = {'energy': 80, 'stress': 20}
        relationship = {key: 70 for key in DEFAULT_RELATIONSHIP}
        relationship['tension'] = 0
        for trait in DEFAULT_TRAITS:
            with self.subTest(trait=trait):
                low = behavior_policy({**DEFAULT_TRAITS, trait: 10}, relationship, state)
                high = behavior_policy({**DEFAULT_TRAITS, trait: 90}, relationship, state)
                self.assertNotEqual(low, high)

    def test_new_observation_cannot_be_overwritten_by_an_old_review(self):
        u, a = self.pair('你喜歡紅茶', '好')
        self.pair('現在改成咖啡', '好')
        with self.store.db:
            self.engine.emit('observed', 'new-preference', {'facts': [{'key': 'character.drink', 'value': '咖啡'}]})
            self.engine.analyze(1, u, a, {'facts': [{'key': 'drink', 'value': '紅茶', 'evidence': '你喜歡紅茶'}]})
        self.assertEqual(self.engine.knowledge()[0]['value'], '咖啡')

    def test_every_relationship_axis_affects_policy(self):
        state = {'energy': 80, 'stress': 20}
        base = {key: 70 for key in DEFAULT_RELATIONSHIP}
        for key in DEFAULT_RELATIONSHIP:
            with self.subTest(relationship=key):
                self.assertNotEqual(
                    behavior_policy(DEFAULT_TRAITS, {**base, key: 10}, state),
                    behavior_policy(DEFAULT_TRAITS, {**base, key: 90}, state),
                )

    def test_completed_analysis_cannot_apply_a_second_time(self):
        u, a = self.pair()
        review = self.store.queue_relationship_review('休息吧', '好', u, a)
        self.store.apply_relationship_analysis(review, {'trust': 1}, 'test')
        before = self.store.relationship()
        self.store.apply_relationship_analysis(review, {'trust': 1}, 'test')
        self.assertEqual(before, self.store.relationship())
