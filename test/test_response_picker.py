import asyncio
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from companion.emotions import normalize_emotions, observe_user_emotions
from companion.engine import utcnow
from companion.persona_policy import apply_persona_policy, policy_options
from companion.router import apply_expression_plan, load_catalog, select_plan, expression_contract, response_token_budget
from companion.store import Store, DEFAULT_RELATIONSHIP, DEFAULT_TRAITS


class EmotionStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name) / 'test.sqlite3')

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def update(self, proposal, user='你現在很生氣'):
        u = self.store.add_message('user', user)
        a = self.store.add_message('assistant', '我會說清楚。')
        with self.store.db:
            self.store.engine.analyze(a, u, a, proposal)

    def test_legacy_state_migrates_without_guessing_from_mood(self):
        state = self.store.engine._projection('state')
        state.pop('emotions')
        state['mood'] = '非常憤怒'
        with self.store.db:
            self.store.engine.emit('legacy', 'legacy', {'state': state})
        self.assertEqual(self.store.engine.state()['emotions'], normalize_emotions({}))
        self.assertEqual(self.store.engine.state()['mood'], '平靜')

    def test_new_database_persists_v2_emotion_projection(self):
        projected = self.store.engine._projection('state')
        self.assertEqual(projected['emotions'], normalize_emotions({}))

    def test_evidence_bounds_decay_replay_and_mood(self):
        self.update({'evidence': '你現在很生氣', 'state': {'emotion_deltas': {
            'anger': 999, 'joy': float('nan'), 'fear': True, 'invalid': 80}}})
        now = utcnow()
        own = self.store.engine.state(now)['emotions']
        self.assertAlmostEqual(own['anger'], 10, places=2)
        self.assertEqual(own['joy'], 0)
        self.assertEqual(own['fear'], 0)
        self.assertLess(self.store.engine.state(now + timedelta(hours=2))['emotions']['anger'], 4)
        before = self.store.engine.state(now)
        self.store.engine.rebuild()
        self.assertEqual(before, self.store.engine.state(now))
        self.update({'evidence': '你現在很生氣', 'state': {'emotion_deltas': {'anger': 10}}})
        self.assertIn('憤怒', self.store.engine.state()['mood'])

    def test_no_evidence_or_old_review_cannot_update_emotions(self):
        self.update({'evidence': '我真的生氣了', 'state': {'emotion_deltas': {'anger': 10}}})
        self.assertEqual(self.store.engine.state()['emotions']['anger'], 0)
        u = self.store.add_message('user', '你現在很生氣')
        a = self.store.add_message('assistant', '好')
        self.store.add_message('user', '新訊息')
        with self.store.db:
            self.store.engine.analyze(55, u, a, {'evidence': '你現在很生氣', 'state': {'emotion_deltas': {'anger': 10}}})
        self.assertEqual(self.store.engine.state()['emotions']['anger'], 0)

    def test_reset_removes_emotion_events(self):
        self.update({'evidence': '你現在很生氣', 'state': {'emotion_deltas': {'anger': 10}}})
        self.store.reset()
        self.store.engine.rebuild()
        self.assertEqual(self.store.engine.state()['emotions'], normalize_emotions({}))

    def test_user_self_report_is_separate_and_unknown_is_not_neutral(self):
        for text in ('我很難過', "I'm really sad."):
            observation = observe_user_emotions(text)
            self.assertEqual(observation['status'], 'explicit')
            self.assertEqual(observation['emotions']['sadness'], 60)
        for text in ('什麼是悲傷？', '我不難過', '你很生氣', '如果我很難過怎麼辦', '他說我很難過', '小說裡說「我很難過」', "I am not sad"):
            self.assertEqual(observe_user_emotions(text)['status'], 'unknown', text)
        self.assertEqual(self.store.engine.state()['emotions']['sadness'], 0)


class ResponsePickerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.catalog = load_catalog()
        self.traits = {**DEFAULT_TRAITS, 'teasing': 90}
        self.rel = {**DEFAULT_RELATIONSHIP, 'trust': 85, 'comfort': 85, 'affection': 85}
        self.state = {'energy': 80, 'stress': 10, 'emotions': normalize_emotions({})}

    def plan(self, strategy='support', **kwargs):
        return select_plan(self.catalog, {strategy: .85}, kwargs.get('traits', self.traits),
                           kwargs.get('relationship', self.rel), kwargs.get('state', self.state),
                           [{'value': '家中'}], user_affect=kwargs.get('user_affect'))

    def test_same_message_changed_emotion_changes_attitude_not_fact_answer(self):
        baseline = self.plan('direct_answer')
        for key in self.state['emotions']:
            state = {**self.state, 'emotions': {key: 90}}
            changed = self.plan('direct_answer', state=state)
            self.assertEqual(changed['response']['primary_action'], 'answer')
            self.assertNotEqual(changed['response'], baseline['response'], key)

    def test_own_anger_does_not_turn_factual_answer_into_user_comfort(self):
        state = {**self.state, 'emotions': {'anger': 90}}
        plan = self.plan('direct_answer', state=state)
        self.assertEqual(plan['strategy'], 'direct_answer')
        self.assertEqual(plan['user_affect']['status'], 'unknown')

    def test_explicit_user_distress_blocks_playful_and_affects_support_attitude(self):
        affect = observe_user_emotions('我很難過')
        self.assertEqual(self.plan('playful', user_affect=affect)['strategy'], 'support')
        baseline = self.plan()
        changed = self.plan(user_affect=affect)
        self.assertGreater(changed['response']['warmth'], baseline['response']['warmth'])
        self.assertLess(changed['response']['agency'], baseline['response']['agency'])

    def test_tired_reply_has_no_compulsory_secondary_action(self):
        tired = self.plan(state={**self.state, 'energy': 10})
        self.assertEqual(tired['response']['secondary_action'], 'none')
        self.assertIn('tentatively reflect the specific feeling', expression_contract(tired))
        self.assertIn('Do not ask a question', expression_contract(tired))

    def test_trusted_plan_is_adjacent_without_mutating_saved_context(self):
        plan = self.plan(state={**self.state, 'energy': 10})
        messages = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': '我很難過'}]
        result = apply_expression_plan(messages, plan)
        self.assertEqual(messages[-1]['content'], '我很難過')
        self.assertIn('<response_plan>', result[-1]['content'])
        self.assertIn('Purpose:', result[-1]['content'])
        self.assertIn('Coping intention:', result[-1]['content'])
        self.assertIn('Do not ask a question', result[-1]['content'])
        self.assertNotRegex(result[-1]['content'], r'warmth=\d|agency=\d|"emotions"')
        self.assertNotIn('intensity', result[-1]['content'])
        self.assertNotIn('relatedness', result[-1]['content'])

    def test_social_reply_budget_does_not_shorten_factual_answer(self):
        support = self.plan(state={**self.state, 'energy': 10})
        normal_support = self.plan()
        normal_support['response']['secondary_action'] = 'none'
        direct = self.plan('direct_answer')
        self.assertEqual(response_token_budget(support, 384), 192)
        self.assertEqual(response_token_budget(normal_support, 384), 128)
        self.assertEqual(response_token_budget(direct, 384), 384)

    async def test_model_can_pick_action_but_not_cross_strategy_or_override_bounds(self):
        plan = self.plan()
        snapshot = {'baseline': plan, 'previous_strategies': []}
        options = policy_options(plan, self.catalog, {'support': .85}, self.rel, self.state)
        provider = AsyncMock()
        valid = {'strategy': 'support', 'variant': plan['variant'], **plan['response']}
        provider.decide.return_value = {**valid, 'primary_action': 'affirm', 'secondary_action': 'none'}
        result = await apply_persona_policy(provider, snapshot, options, self.catalog)
        self.assertEqual(result['response']['primary_action'], 'affirm')
        for patch in ({'primary_action': 'share_self'}, {'warmth': 999}, {'agency': True},
                      {'primary_action': 'affirm', 'secondary_action': 'affirm'}):
            provider.decide.return_value = {**valid, **patch}
            result = await apply_persona_policy(provider, snapshot, options, self.catalog)
            self.assertEqual(result['persona_provider'], 'rules_fallback')
            self.assertEqual(result['response'], plan['response'])
