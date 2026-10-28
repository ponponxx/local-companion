import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from companion.app import App
from companion.config import Config
from companion.router import EmbeddingClient, StrategyRouter, load_catalog, select_plan
from companion.services import Images, LLM, Telegram, Voice, ServiceError
from companion.store import Store, DEFAULT_TRAITS, DEFAULT_RELATIONSHIP


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog()
        self.traits = {**DEFAULT_TRAITS, 'teasing': 90}
        self.relationship = {**DEFAULT_RELATIONSHIP, 'trust': 85, 'comfort': 85, 'affection': 85}
        self.state = {'energy': 80, 'stress': 10}

    def plan(self, scores, relationship=None, state=None, knowledge=(), previous=()):
        return select_plan(self.catalog, scores, self.traits, relationship or self.relationship,
                           state or self.state, knowledge, previous)

    def test_no_match_or_ambiguous_match_has_an_exit(self):
        for scores in ({}, {'support': .2}, {'support': .6, 'playful': .59}):
            self.assertEqual(self.plan(scores)['strategy'], 'open_response')

    def test_conflict_blocks_teasing_but_does_not_replace_a_factual_answer(self):
        rel = {**self.relationship, 'tension': 90}
        repair = self.plan({'playful': .8}, relationship=rel)
        factual = self.plan({'direct_answer': .8}, relationship=rel)
        self.assertEqual(repair['strategy'], 'repair')
        self.assertEqual(repair['motivation']['coping']['strategy'], 'repair_connection')
        self.assertEqual(factual['strategy'], 'direct_answer')
        self.assertEqual(factual['motivation']['coping']['strategy'], 'problem_solve')

    def test_cannot_invent_self_facts_to_fill_a_plan(self):
        self.assertEqual(self.plan({'share_self': .9})['strategy'], 'open_response')
        self.assertEqual(self.plan({'share_self': .9}, knowledge=[{'value': '家中'}])['strategy'], 'share_self')

    def test_same_intent_keeps_goal_and_relationship_changes_expression(self):
        high = self.plan({'support': .8})
        low = self.plan({'support': .8}, relationship=DEFAULT_RELATIONSHIP)
        tired = self.plan({'support': .8}, state={**self.state, 'energy': 10})
        self.assertEqual(high['strategy'], low['strategy'])
        self.assertEqual(high['variant'], 'warm')
        self.assertEqual(low['variant'], 'reserved')
        self.assertEqual(tired['variant'], 'brief')

    def test_repeated_need_is_not_arbitrarily_replaced(self):
        plan = self.plan({'support': .8}, previous=['support', 'support'])
        self.assertEqual(plan['strategy'], 'support')
        self.assertTrue(plan['avoid_repeating_expression'])

    def test_motivation_reduces_many_inputs_to_one_goal_and_coping_intention(self):
        plan = select_plan(
            self.catalog, {'followup': .8}, self.traits, self.relationship, self.state, (),
            conversation={'open_loops': ['確認明天要帶的東西']},
        )
        motivation = plan['motivation']
        self.assertEqual(motivation['model'], 'ema_lite_v1')
        self.assertEqual(set(motivation['needs']), {
            'relatedness', 'autonomy', 'competence', 'curiosity', 'homeostasis', 'erotic_connection'})
        self.assertGreaterEqual(len(motivation['goals']), 5)
        self.assertIn(motivation['dominant_goal'], motivation['goals'])
        self.assertIn(motivation['appraisal']['congruence'], {
            'obstructed', 'threatened', 'unresolved', 'opportunity'})
        self.assertEqual(motivation['coping']['strategy'], 'explore')

    def test_erotic_goal_stays_latent_when_standing_policy_is_off(self):
        plan = select_plan(
            self.catalog, {}, self.traits,
            {**self.relationship, 'erotic_interest': 90, 'sexual_comfort': 90, 'intimate_safety': 90},
            self.state, (), adult_intimacy={
                'sexual_openness': 90, 'excitation_sensitivity': 90,
                'inhibition_sensitivity': 10, 'responsive_desire': 90,
                'erotic_expressiveness': 90,
            },
        )
        erotic = next(goal for goal in plan['motivation']['goals'] if goal['id'] == 'seek_erotic_connection')
        self.assertEqual(erotic['status'], 'latent')
        self.assertFalse(plan['motivation']['erotic_readiness']['actionable'])

    def test_adult_tendencies_change_readiness_and_policy_plus_state_open_route(self):
        relationship = {**self.relationship, 'erotic_interest': 80,
                        'sexual_comfort': 80, 'intimate_safety': 80}
        low = select_plan(
            self.catalog, {'playful': .8}, self.traits, relationship, self.state, (),
            adult_intimacy={
                'flirtatiousness': 10, 'sexual_openness': 10, 'excitation_sensitivity': 10,
                'inhibition_sensitivity': 90, 'responsive_desire': 10, 'erotic_expressiveness': 10,
            },
        )
        high = select_plan(
            self.catalog, {'playful': .8}, self.traits, relationship, self.state, (),
            adult_intimacy={
                'flirtatiousness': 90, 'sexual_openness': 90, 'excitation_sensitivity': 90,
                'inhibition_sensitivity': 10, 'responsive_desire': 90, 'erotic_expressiveness': 90,
            },
            adult_policy={'mode': 'intimate', 'agent_may_initiate': True},
            intimacy_state={'heat': 25, 'desire': 80, 'receptivity': 70, 'inhibition': 10,
                            'momentum': 30, 'mutuality': 20, 'tension': 20,
                            'last_signal': 'receptive', 'last_direction': 'warm_tease'},
        )
        self.assertGreater(high['motivation']['erotic_readiness']['score'],
                           low['motivation']['erotic_readiness']['score'])
        self.assertTrue(high['motivation']['erotic_readiness']['actionable'])

    def test_character_need_weights_change_goal_selection_without_changing_engine(self):
        autonomy = select_plan(
            self.catalog, {}, self.traits, self.relationship, self.state, (),
            need_weights={'autonomy': 2.0, 'relatedness': .25, 'competence': .25,
                          'curiosity': .25, 'homeostasis': .25},
        )
        self.assertEqual(autonomy['motivation']['dominant_goal']['id'], 'preserve_agency')


class RouterIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'memory.sqlite3')
        self.config = replace(Config.load(require_credentials=False), router_mode='off', chat_core='legacy', data_dir=Path(self.tmp.name))
        self.telegram = AsyncMock(spec=Telegram)
        self.telegram.send.return_value = {'message_id': 1}
        self.llm = AsyncMock(spec=LLM)
        self.llm.generate.return_value = '先休息一下吧。'
        self.llm.last_metrics = {'first_text_seconds': .1, 'total_seconds': .2}
        self.app = App(self.config, self.store, self.telegram, self.llm, AsyncMock(spec=Voice), AsyncMock(spec=Images))
        self.app.extract_scene_events = AsyncMock()
        self.plan = select_plan(load_catalog(), {'support': .8}, DEFAULT_TRAITS, DEFAULT_RELATIONSHIP,
                                {'energy': 70, 'stress': 20}, [])
        self.app.router = AsyncMock()
        self.app.router.catalog = load_catalog()
        self.app.router.plan.return_value = self.plan
        self.app.router.visual_kind.return_value = None

    async def asyncTearDown(self):
        self.store.close()
        self.tmp.cleanup()

    async def chat(self):
        await self.app.handle(1, {'chat': {'id': 42}, 'text': '我好累，安慰我一下。'})

    async def test_active_adds_outline_without_second_generation(self):
        self.app.config = replace(self.config, router_mode='active')
        await self.chat()
        self.llm.generate.assert_awaited_once()
        prompt = self.llm.generate.await_args.args[0]
        self.assertIn('<response_plan>', prompt[-1]['content'])
        self.assertNotIn('relationship_stage', json.dumps(prompt, ensure_ascii=False))
        self.assertEqual(json.loads(self.store.get('last_strategy'))['strategy'], 'support')

    async def test_shadow_records_selection_without_changing_prompt(self):
        self.app.config = replace(self.config, router_mode='shadow')
        await self.chat()
        self.assertNotIn('<response_plan>', json.dumps(self.llm.generate.await_args.args[0], ensure_ascii=False))
        self.assertEqual(json.loads(self.store.get('last_strategy'))['strategy'], 'support')

    async def test_embedding_failure_falls_back_without_blocking_chat(self):
        self.app.router.plan.side_effect = RuntimeError('not_ready')
        await self.chat()
        self.llm.generate.assert_awaited_once()
        self.assertEqual(json.loads(self.store.get('last_strategy'))['reasons'], ['embedding_unavailable'])

    async def test_failed_delivery_does_not_advance_strategy_history(self):
        self.telegram.send.side_effect = ServiceError('uncertain')
        with self.assertRaises(ServiceError):
            await self.chat()
        self.assertEqual(self.store.get('recent_strategies'), '')

    async def test_image_generation_suspends_embedding(self):
        self.app.image_busy = True
        await self.chat()
        self.app.router.plan.assert_not_awaited()
        self.llm.generate.assert_awaited_once()

    async def test_reset_removes_strategy_evidence(self):
        await self.chat()
        self.store.reset()
        self.assertEqual(self.store.get('last_strategy'), '')
        self.assertEqual(self.store.get('recent_strategies'), '')

    async def test_query_is_encoded_once_and_catalog_is_cached(self):
        client = AsyncMock()
        client.model_path = Path('fixed-revision')
        async def encode(texts):
            return [[1., 0.] for _ in texts]
        client.encode.side_effect = encode
        router = StrategyRouter(client, cache_dir=Path(self.tmp.name) / 'cache')
        await router.plan('你好', self.store, datetime.now(timezone.utc))
        await router.plan('你好', self.store, datetime.now(timezone.utc))
        self.assertEqual(client.encode.await_count, 3)  # catalog once + each query

    async def test_visual_semantic_fallback_classifies_with_a_strict_margin(self):
        client = AsyncMock()
        client.model_path = Path('fixed-revision')
        router = StrategyRouter(client)
        example_vectors = []
        for kind, _ in router.visual_examples:
            example_vectors.append([1., 0.] if kind == 'selfie' else [0., 1.])
        client.encode.side_effect = [example_vectors, [[1., 0.]]]
        self.assertEqual(await router.visual_kind('讓我看看你此刻的模樣'), 'selfie')
        self.assertEqual(router.last_query[0], '讓我看看你此刻的模樣')

    async def test_missing_runtime_has_no_orphan_process(self):
        client = EmbeddingClient('/not-installed/python', '/not-installed/model')
        with self.assertRaises(RuntimeError):
            await client.encode(['test'])
        self.assertIsNone(client.process)
