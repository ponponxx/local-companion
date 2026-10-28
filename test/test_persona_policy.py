import asyncio
import json
from copy import deepcopy
import unittest
from unittest.mock import AsyncMock

from companion.persona_policy import LocalModelPersonaPolicy, RulePersonaPolicy, apply_persona_policy, policy_options
from companion.router import load_catalog, select_plan
from companion.store import DEFAULT_TRAITS, DEFAULT_RELATIONSHIP


class PersonaPolicyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.catalog = load_catalog()
        self.relationship = {**DEFAULT_RELATIONSHIP, 'trust': 90, 'comfort': 80, 'affection': 90}
        self.state = {'energy': 80, 'stress': 10}
        self.baseline = select_plan(self.catalog, {'support': .85}, DEFAULT_TRAITS,
                                    self.relationship, self.state, [])
        self.snapshot = {'baseline': self.baseline, 'previous_strategies': [], 'state': self.state}
        self.options = policy_options(self.baseline, self.catalog, {'support': .85}, self.relationship, self.state)

    async def test_rules_preserve_current_behavior_without_inference(self):
        result = await apply_persona_policy(RulePersonaPolicy(), self.snapshot, self.options, self.catalog)
        self.assertEqual(result['strategy'], self.baseline['strategy'])
        self.assertEqual(result['variant'], self.baseline['variant'])
        self.assertEqual(result['persona_provider'], 'rules')

    async def test_model_adapter_requests_a_bounded_json_decision(self):
        llm = AsyncMock()
        llm.generate.return_value = json.dumps({'strategy': 'support', 'variant': 'reserved', **self.baseline['response']})
        provider = LocalModelPersonaPolicy(llm)
        result = await apply_persona_policy(provider, self.snapshot, self.options, self.catalog)
        self.assertEqual(result['variant'], 'reserved')
        schema = llm.generate.await_args.kwargs['response_schema']
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(schema['properties']['strategy']['enum'], ['support'])

    async def test_invented_actions_or_changed_outline_are_rejected(self):
        for decision in ({'strategy': 'support', 'variant': 'warm', 'facts': ['已泡茶']},
                         {'strategy': 'image_generation', 'variant': 'warm'},
                         {'strategy': 'support', 'variant': 'hostile'}):
            provider = AsyncMock()
            provider.decide.return_value = decision
            result = await apply_persona_policy(provider, self.snapshot, self.options, self.catalog)
            self.assertEqual(result['persona_provider'], 'rules_fallback')
            self.assertEqual(result['outline'], self.baseline['outline'])

    async def test_timeout_falls_back_but_shutdown_is_not_swallowed(self):
        provider = AsyncMock()
        provider.decide.side_effect = TimeoutError()
        self.assertEqual((await apply_persona_policy(provider, self.snapshot, self.options, self.catalog))['persona_provider'], 'rules_fallback')
        provider.decide.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await apply_persona_policy(provider, self.snapshot, self.options, self.catalog)

    async def test_provider_cannot_mutate_state_or_permitted_choices(self):
        before = deepcopy(self.snapshot)
        class MutatingProvider:
            async def decide(self, snapshot, options):
                snapshot['state']['energy'] = 0
                options['strategies'].append('repair')
                return {'strategy': 'repair', 'variant': 'warm'}
        result = await apply_persona_policy(MutatingProvider(), self.snapshot, self.options, self.catalog)
        self.assertEqual(self.snapshot, before)
        self.assertEqual(result['persona_provider'], 'rules_fallback')

    async def test_exhaustion_and_uncertainty_remain_core_constraints(self):
        tired = {'energy': 10, 'stress': 20}
        baseline = select_plan(self.catalog, {'support': .8}, DEFAULT_TRAITS, self.relationship, tired, [])
        options = policy_options(baseline, self.catalog, {'support': .8}, self.relationship, tired)
        self.assertEqual(options['variants'], ['brief'])
        uncertain = select_plan(self.catalog, {'support': .8, 'repair': .79}, DEFAULT_TRAITS, self.relationship, tired, [])
        self.assertEqual(policy_options(uncertain, self.catalog, {'support': .8, 'repair': .79}, self.relationship, tired)['strategies'], ['open_response'])
