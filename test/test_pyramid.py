from unittest.mock import patch as mock_capacity
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from companion.pyramid import (PyramidCore, DecisionTrace, initial_state, date, validate_state, TurnInput)
from companion.pyramid_runner import PyramidRunner
from companion.workbench import SandboxRepository, WorkbenchService, import_production
from companion.config import Config

STAMP = '2026-10-15T00:00:00+00:00'


def event(kind, evidence='證據', **kwargs):
    return dict(kind=kind, actor='user', target='character', stage='completed',
                field='care' if kind == 'invite' else '', value='', evidence=evidence, strength=2, ref='', **{}) | kwargs


def observation(events=None, **kwargs):
    from companion.visual_state import is_removed
    appearance=[{'wearer':e['actor'],'slot':e['field'],'operation':'style' if e['field']=='hair' else 'remove' if is_removed(e['value']) else 'wear','phase':e['stage'],'value':e['value'],'evidence':e['evidence']} for e in (events or []) if e['kind']=='appearance']
    return {'information_scope':'unknown', 'working_updates':[], 'utterance': {'act': 'unknown', 'purpose': 'other', 'value': '', 'evidence': '', 'ref': '', 'strength': 0}, 'appearance_changes':appearance, 'external_request':{'reason':'none','query':'','freshness':'any'}, 'events': [e for e in (events or []) if e['kind']!='appearance'], 'topic': '當前內容', 'missing_detail': '',
            'needs_clarification':bool(kwargs.get('missing_detail')),
            'asks_character': False, 'user_distress': False,
            'user_affect': {'status': 'unknown', 'emotion': '', 'evidence': ''},
            'conversation_signal': {'kind':'unknown','evidence':''},
            'appraisals': [], **kwargs}


class CoreTests(unittest.TestCase):
    def interaction_case(self, assertiveness, respect):
        state,core,trace=initial_state(STAMP),PyramidCore(),DecisionTrace()
        state['traits']['assertiveness']=assertiveness
        state['relationship'].update(trust=80,comfort=80,respect=respect)
        events=core.apply(state,[event('invite',field='social',value='跳舞')],'user','證據','one',trace)
        decision=core.decide(state,events,observation(),trace)
        candidates=next(n['inputs']['candidates'] for n in trace.nodes if n['rule']=='decision.single_selector')
        accept=next(c for c in candidates if c['action']=='accept')
        term=next(t for t in accept['cost_terms'] if 'normalized_bands' in t)
        return term,decision,candidates

    def test_assertiveness_respect_interaction_directions_over_four_levels(self):
        levels=[12,37,62,87]
        matrix=[[self.interaction_case(a,r)[0]['value'] for r in levels] for a in levels]
        self.assertEqual(matrix[0],[1,.667,.333,0])
        self.assertEqual(matrix[-1],[1,1,1,1])
        for row in matrix:self.assertEqual(row,sorted(row,reverse=True))
        for col in zip(*matrix):self.assertEqual(list(col),sorted(col))
        effects=[row[0]-row[-1] for row in matrix]
        self.assertEqual(effects,sorted(effects,reverse=True))

    def test_interaction_replaces_both_independent_penalties_and_can_change_choice(self):
        _,low_respect,_=self.interaction_case(12,12)
        _,high_respect,candidates=self.interaction_case(12,87)
        self.assertNotEqual(low_respect['primary'],'accept')
        self.assertEqual(high_respect['primary'],'accept')
        for c in candidates:
            joint=[t for t in c['cost_terms'] if 'normalized_bands' in t]
            self.assertEqual(len(joint),1 if c['action']=='accept' else 0)
            self.assertFalse(any(t['rule'] in {'有主見偏好保留條件','低尊重增加無條件接受成本'} for t in c['cost_terms']))

    def test_interaction_does_not_remove_discomfort_busy_costs_or_adult_policy(self):
        state,core,trace=initial_state(STAMP),PyramidCore(),DecisionTrace()
        state['traits']['assertiveness']=12
        state['relationship'].update(respect=87,comfort=10,trust=80)
        state['availability']='busy'
        events=core.apply(state,[event('invite',field='intimacy',value='跳舞')],'user','證據','one',trace)
        core.decide(state,events,observation(),trace)
        candidates=next(n['inputs']['candidates'] for n in trace.nodes if n['rule']=='decision.single_selector')
        for c in candidates:
            if c['action'] in {'accept','conditional_accept'}:
                self.assertEqual(c['cost'],3)
                self.assertTrue(c['excluded'])

    def test_conditional_accept_waives_discomfort_cost_but_keeps_busy_cost(self):
        for availability in ('free', 'busy'):
            with self.subTest(availability=availability):
                state, core, trace = initial_state(STAMP), PyramidCore(), DecisionTrace()
                state['relationship'].update(trust=80, comfort=10)
                state['availability'] = availability
                events = core.apply(state, [event('invite', value='接受按摩')], 'user', '證據', 'one', trace)
                core.decide(state, events, observation(), trace)
                candidates = {c['action']: c for c in next(n['inputs']['candidates'] for n in trace.nodes if n['rule'] == 'decision.single_selector')}
                conditional = candidates['conditional_accept']
                self.assertEqual(conditional['cost'], 0 if availability == 'free' else 3)
                self.assertFalse(any(t['rule'] == '相處尚不自在' for t in conditional['cost_terms']))
                self.assertEqual(candidates['accept']['cost'], 3)
                # Effective conditions waive this cost, not the relationship benefit rule.
                self.assertEqual(conditional['helps']['relatedness'], 0)
                self.assertEqual(state['relationship']['comfort'], 10)

    def test_joy_bonus_applies_to_all_invitation_purposes_and_only_direct_accept(self):
        for purpose in ('care', 'task', 'social', 'intimacy', 'other'):
            scores = []
            for joy in (12, 37, 62, 87):
                state, core, trace = initial_state(STAMP), PyramidCore(), DecisionTrace()
                state['adult_policy']['mode'] = 'intimate'
                state['emotions']['joy'] = joy
                events = core.apply(state, [event('invite', field=purpose, value='未知活動')], 'user', '證據', 'one', trace)
                core.decide(state, events, observation(), trace)
                candidates = {c['action']: c for c in next(n['inputs']['candidates'] for n in trace.nodes if n['rule'] == 'decision.single_selector')}
                accept = candidates['accept']
                self.assertEqual(accept['joy_bonus'], round((joy // 25) / 3, 3))
                for action, candidate in candidates.items():
                    if action != 'accept': self.assertEqual(candidate['joy_bonus'], 0)
                scores.append(accept['score'])
            self.assertAlmostEqual(scores[-1] - scores[0], 1, places=3)

    def test_high_joy_cannot_override_stop_or_adult_policy_and_no_invite_gets_no_bonus(self):
        for kinds, purpose in [(['invite'], 'intimacy'), (['invite', 'stop'], 'social'), (['question'], 'task')]:
            state, core, trace = initial_state(STAMP), PyramidCore(), DecisionTrace()
            state['emotions']['joy'] = 100
            events = core.apply(state, [event(k, field=purpose) for k in kinds], 'user', '證據', 'one', trace)
            decision = core.decide(state, events, observation(), trace)
            candidates = {c['action']: c for c in next(n['inputs']['candidates'] for n in trace.nodes if n['rule'] == 'decision.single_selector')}
            if 'invite' in kinds:
                self.assertTrue(candidates['accept']['excluded'])
                self.assertNotEqual(decision['primary'], 'accept')
            else:
                self.assertEqual(candidates['accept']['joy_bonus'], 0)

    def activity_decision(self, value, purpose='social', strength=2, appraisals=None, competence_weight=1):
        state, core, trace = initial_state(STAMP), PyramidCore(), DecisionTrace()
        state['relationship'].update(trust=80,comfort=80)
        state['need_weights']['competence'] = competence_weight
        before = deepcopy(state)
        events = core.apply(state,[event('invite',value=value,field=purpose,strength=strength)],'user','證據','one',trace)
        decision = core.decide(state,events,observation(appraisals=appraisals or []),trace)
        candidates = next(n['inputs']['candidates'] for n in trace.nodes if n['rule']=='decision.single_selector')
        return decision,{c['action']:c for c in candidates},state,before,trace

    def test_skill_activity_activates_competence_and_acceptance_payoff(self):
        for value,help_level in [('跳舞',2),('跳舞邀請',2),('畫素描',3)]:
            decision,candidates,state,before,trace=self.activity_decision(value)
            self.assertEqual(decision['needs']['competence'],2)
            for action in ('accept','conditional_accept'):
                self.assertEqual(candidates[action]['helps']['competence'],help_level)
                self.assertGreater(candidates[action]['benefit_terms']['competence']['contribution'],0)
            self.assertEqual(candidates['refuse']['helps'].get('competence',0),0)
            self.assertEqual(candidates['suggest']['helps']['competence'],0)
            self.assertEqual(state['relationship'],before['relationship'])
            self.assertEqual(state['energy'],before['energy'])
            self.assertEqual(state['activities'],{})

    def test_modeling_is_not_drawing_and_generic_task_does_not_imply_skill(self):
        for value,purpose in [('當素描模特兒','social'),('素描','task'),('未知活動','task'),('','task')]:
            decision,candidates,_,_,trace=self.activity_decision(value,purpose,appraisals=[{'need':'competence','strength':3,'evidence':'證據'}])
            self.assertEqual(decision['needs']['competence'],0,value)
            self.assertEqual(candidates['accept']['helps']['competence'],0,value)
        activity=next(n for n in trace.nodes if n['rule']=='activity.affordances')
        self.assertFalse(activity['output']['scoring_activity']['matched'])

    def test_activity_role_prevents_giving_care_from_counting_as_own_rest(self):
        _,receive,_,_,_=self.activity_decision('接受按摩','other')
        _,give,_,_,_=self.activity_decision('提供按摩','care')
        self.assertEqual(receive['accept']['helps']['homeostasis'],3)
        self.assertEqual(receive['accept']['helps']['competence'],0)
        self.assertEqual(give['accept']['helps']['homeostasis'],0)
        self.assertEqual(give['accept']['helps']['competence'],2)
        _,generic,_,_,trace=self.activity_decision('按摩','care')
        self.assertEqual(generic['accept']['helps']['homeostasis'],3)
        self.assertFalse(next(n for n in trace.nodes if n['rule']=='activity.affordances')['output']['scoring_activity']['matched'])

    def test_competence_weight_changes_skill_benefit_not_modeling(self):
        for value in ['跳舞','當素描模特兒']:
            low,lc,_,_,_=self.activity_decision(value,competence_weight=.25)
            high,hc,_,_,_=self.activity_decision(value,competence_weight=2)
            if value=='跳舞':
                self.assertGreater(high['needs']['competence'],low['needs']['competence'])
                self.assertGreater(hc['accept']['benefit_terms']['competence']['contribution'],lc['accept']['benefit_terms']['competence']['contribution'])
            else:
                self.assertEqual(high['needs'],low['needs'])
                for action in hc:
                    for key in ('score','benefit','cost','trait_preference','fit','excluded'):
                        self.assertEqual(hc[action][key],lc[action][key])

    def test_only_selected_invitation_supplies_activity_opportunity(self):
        state,core,trace=initial_state(STAMP),PyramidCore(),DecisionTrace()
        events=core.apply(state,[event('invite',value='跳舞'),event('invite',value='當素描模特兒')],'user','證據','one',trace)
        decision=core.decide(state,events,observation(),trace)
        self.assertEqual(decision['target_event'],events[-1]['id'])
        self.assertEqual(decision['needs']['competence'],0)

    def test_invitation_does_not_activate_autonomy_even_with_unsupported_appraisal(self):
        for appraisals in ([], [{'need':'autonomy','strength':3,'evidence':'證據'}]):
            state, core, trace = initial_state(STAMP), PyramidCore(), DecisionTrace()
            events = core.apply(state, [event('invite')], 'user', '證據', 'one', trace)
            decision = core.decide(state, events, observation(appraisals=appraisals), trace)
            self.assertEqual(decision['needs']['autonomy'], 0)
            self.assertEqual(decision['needs']['relatedness'], 2)
            self.assertIn('refuse', [c['action'] for c in next(n for n in trace.nodes if n['rule']=='decision.single_selector')['inputs']['candidates'] if not c['excluded']])

    def test_boundary_and_stop_events_still_activate_autonomy(self):
        for kind, strength in [('violation',2),('stop',3),('pause',2)]:
            state, core, trace = initial_state(STAMP), PyramidCore(), DecisionTrace()
            events = core.apply(state, [event(kind)], 'user', '證據', 'one', trace)
            decision = core.decide(state, events, observation(), trace)
            self.assertEqual(decision['needs']['autonomy'], strength)

    def test_humor_is_delivery_only_across_answers_invitations_and_flirting(self):
        from companion.pyramid import ACTIONS, TRAIT_PREFS
        self.assertNotIn('play', ACTIONS)
        self.assertFalse(any(key == 'teasing' for prefs in TRAIT_PREFS.values() for key, _ in prefs))
        for kind, policy, comfort in [('question', 'off', 80), ('invite', 'off', 80),
                                       ('invite', 'off', 10), ('flirt', 'flirt', 80), ('pause', 'flirt', 80)]:
            results = []
            for teasing in (12, 87):
                state = initial_state(STAMP)
                state['traits']['teasing'] = teasing
                state['relationship'].update(trust=80, comfort=comfort)
                state['adult_policy']['mode'] = policy
                core, trace = PyramidCore(), DecisionTrace()
                events = core.apply(state, [event(kind)], 'user', '證據', 'one', trace)
                decision = core.decide(state, events, observation(), trace)
                candidates = next(n['inputs']['candidates'] for n in trace.nodes if n['rule']=='decision.single_selector')
                results.append((decision, candidates))
            self.assertEqual(results[0][0]['primary'], results[1][0]['primary'], kind)
            self.assertEqual(results[0][0]['intimacy_intention'], results[1][0]['intimacy_intention'], kind)
            self.assertEqual(results[0][1], results[1][1], kind)
            self.assertLess(results[0][0]['delivery']['teasing'], results[1][0]['delivery']['teasing'])

    def test_distress_suppresses_humor_in_delivery_instead_of_action_candidates(self):
        state, core, trace = initial_state(STAMP), PyramidCore(), DecisionTrace()
        state['traits']['teasing'] = 100
        state['relationship'].update(trust=100, comfort=100)
        events = core.apply(state, [event('disclosure')], 'user', '證據', 'one', trace)
        decision = core.decide(state, events, observation(user_distress=True), trace)
        self.assertEqual(decision['primary'], 'support')
        self.assertEqual(decision['delivery']['teasing'], 0)

    def setUp(self):
        self.state, self.core, self.trace = initial_state(STAMP), PyramidCore(), DecisionTrace()
        self.state['scene'].update(location='浴室', status='active')

    def apply(self, events, text='證據', source='user', turn='a'):
        return self.core.apply(self.state, events, source, text, turn, self.trace)

    def test_relative_move_keeps_bathroom_and_invite_is_not_action(self):
        events = self.apply([event('move', field='relative_position', value='靠近一步'),
                             event('invite', value='按摩')])
        self.assertEqual(self.state['scene']['location'], '浴室')
        self.assertEqual(self.state['scene']['positions']['user']['relation'], '靠近一步')
        self.assertEqual(self.state['activities'], {})
        self.assertEqual(self.state['stress'], 20)
        self.assertEqual(self.state['invitations'][events[1]['id']]['status'], 'proposed')

    def test_user_cannot_decide_character_consent_in_autonomous_mode(self):
        invitation = self.apply([event('invite', value='按摩')])[0]['id']
        self.assertFalse(self.apply([event('accept', actor='character', ref=invitation)], turn='b'))
        self.assertEqual(self.state['invitations'][invitation]['status'], 'proposed')

    def test_director_can_establish_acceptance_but_not_skip_start(self):
        self.state['control_mode'] = 'director'
        ref = self.apply([event('invite', value='按摩')])[0]['id']
        self.apply([event('accept', actor='character', ref=ref)], turn='b')
        self.assertFalse(self.apply([event('complete', actor='character', ref=ref)], turn='c'))
        self.apply([event('start', actor='character', ref=ref)], turn='d')
        self.apply([event('complete', actor='character', ref=ref)], turn='e')
        self.assertEqual(self.state['invitations'][ref]['status'], 'completed')
        self.assertFalse(self.state['activities'])

    def test_assistant_cannot_invent_user_action(self):
        self.assertFalse(self.apply([event('move', field='location', value='窗邊')], source='assistant'))

    def test_hair_typed_and_appearance_persists_on_scene_change(self):
        self.assertFalse(self.apply([event('appearance', actor='character', field='hair', value='按摩')]))
        self.apply([event('appearance', actor='character', field='hair', value='盤發')], turn='b')
        self.apply([event('appearance', actor='character', field='socks', value='已脫下')], turn='c')
        self.apply([event('move', actor='character', target='scene', field='location', value='書房')], turn='d')
        self.assertEqual(self.state['appearance']['hair'], '盤髮')
        self.assertFalse(self.state['appearance']['clothing']['socks']['worn'])

    def test_requests_do_not_change_appearance(self):
        self.assertFalse(self.apply([event('appearance', actor='character', field='hair', value='盤髮', stage='proposed')]))

    def test_bad_evidence_rejected_and_identical_proposal_only_once(self):
        p = event('misunderstanding')
        accepted = self.apply([p, p])
        self.assertEqual(len(accepted), 1)
        self.assertEqual(self.state['relationship']['tension'], .5)
        self.assertFalse(self.apply([p], text='其他原文', turn='b'))
        self.assertFalse(self.apply([p], turn='a'))

    def test_time_decay_keeps_issue_relationship_and_not_forced_calm(self):
        self.state['emotions']['anger'] = 100
        self.apply([event('misunderstanding')])
        relation = deepcopy(self.state['relationship'])
        self.core.advance(self.state, '2026-10-15T04:00:00+00:00', self.trace)
        self.assertGreater(self.state['emotions']['anger'], 0)
        self.assertTrue(self.state['issues'])
        self.assertEqual(self.state['relationship'], relation)
        self.assertAlmostEqual(self.state['emotions']['anger'], 13.534, places=3)
        old = deepcopy(self.state)
        self.core.advance(self.state, self.state['now'], self.trace)
        self.assertEqual(self.state['emotions'], old['emotions'])

    def test_persistent_load_does_not_disappear_when_acute_stress_decays(self):
        self.state['control_mode'] = 'director'
        ref = self.apply([event('stressor', actor='character', value='期限', strength=3)])[0]['id']
        self.core.advance(self.state, '2026-10-16T00:00:00+00:00', self.trace)
        self.assertEqual(self.state['stress'], 30)
        self.apply([event('resolve', actor='character', ref=ref)], turn='b')
        self.assertLess(self.state['stress'], 1)

    def test_notice_waits_another_ten_minutes_and_keeps_appearance(self):
        self.state['appearance']['hair'] = '盤髮'
        self.state['schedule'] = [{'at': STAMP, 'location': '書房', 'activity': '研究', 'availability': 'busy'}]
        self.core.advance(self.state, '2026-10-15T00:10:00+00:00', self.trace)
        self.assertEqual(self.state['scene']['location'], '浴室')
        self.assertIsNotNone(self.state['scene']['pending'])
        self.core.advance(self.state, '2026-10-15T00:19:00+00:00', self.trace)
        self.assertEqual(self.state['scene']['location'], '浴室')
        self.core.advance(self.state, '2026-10-15T00:20:00+00:00', self.trace)
        self.assertEqual(self.state['scene']['location'], '書房')
        self.assertEqual(self.state['appearance']['hair'], '盤髮')

    def test_response_keeps_departed_trip_and_backward_time_fails(self):
        self.state['schedule'] = [{'at': STAMP, 'location': '書房'}]
        self.core.advance(self.state, '2026-10-15T00:10:00+00:00', self.trace)
        self.core.user_arrived(self.state, self.trace)
        self.assertIsNotNone(self.state['scene']['pending'])
        self.core.advance(self.state, '2026-10-15T00:19:00+00:00', self.trace)
        self.assertIsNotNone(self.state['scene']['pending'])
        with self.assertRaises(ValueError): self.core.advance(self.state, STAMP, self.trace)

    def test_tension_accumulates_upgrade_halves_and_plan_does_not_change_heat(self):
        self.state['adult_policy']['mode'] = 'intimate'
        self.apply([event('flirt', strength=1)], turn='a')
        self.assertEqual(self.state['intimacy']['heat'], 0)
        self.assertEqual(self.state['intimacy']['tension'], 2)
        current = self.state['intimacy']['heat']
        self.core.decide(self.state, [], observation(), self.trace)
        self.assertEqual(self.state['intimacy']['heat'], current)
        self.apply([event('flirt', actor='character', target='user', strength=2)], source='assistant', turn='a')
        self.assertEqual(self.state['intimacy']['heat'], 40)
        self.assertEqual(self.state['intimacy']['tension'], 1)
        self.assertEqual(self.state['intimacy']['mutuality'], 100)

    def test_stop_clears_tension_does_not_penalize_relationship_or_reuse_old_partner(self):
        self.state['adult_policy']['mode'] = 'intimate'
        self.apply([event('flirt')], turn='a')
        rel = deepcopy(self.state['relationship'])
        self.apply([event('stop')], turn='b')
        self.assertEqual(self.state['intimacy']['tension'], 0)
        self.assertEqual(self.state['relationship'], rel)
        self.apply([event('flirt', actor='character')], source='assistant', turn='c')
        self.assertEqual(self.state['intimacy']['heat'], 0)
        self.assertEqual(self.state['intimacy']['momentum'], 0)

    def test_intimacy_window_expires_without_penalizing_relationship(self):
        self.state['adult_policy']['mode'] = 'intimate'
        self.apply([event('flirt')], turn='a')
        self.apply([event('flirt', actor='character')], source='assistant', turn='a')
        self.core.advance(self.state, '2026-10-15T01:01:00+00:00', self.trace)
        self.assertEqual(self.state['intimacy']['momentum'], 0)
        self.assertEqual(self.state['intimacy']['mutuality'], 0)

    def test_low_comfort_allows_conditions_but_reduces_connection_benefit(self):
        events = self.apply([event('invite', value='按摩')])
        self.state['energy'] = 25
        low = self.core.decide(self.state, events, observation(), self.trace)
        self.state['relationship'].update(trust=90, comfort=90)
        high = self.core.decide(self.state, events, observation(), self.trace)
        self.assertEqual(low['primary'], 'conditional_accept')
        self.assertIn(high['primary'], {'accept', 'conditional_accept'})
        comparisons = [n['inputs']['candidates'] for n in self.trace.nodes if n['rule'] == 'decision.single_selector']
        low_conditional = next(c for c in comparisons[-2] if c['action'] == 'conditional_accept')
        high_conditional = next(c for c in comparisons[-1] if c['action'] == 'conditional_accept')
        self.assertEqual(low_conditional['cost'], high_conditional['cost'])
        self.assertGreater(high_conditional['benefit'], low_conditional['benefit'])

    def test_old_conflict_and_high_desire_do_not_override_question(self):
        self.state['relationship']['tension'] = 93
        self.state['intimacy']['desire'] = 100
        events = self.apply([event('question', value='17*3')])
        result = self.core.decide(self.state, events, observation(), self.trace)
        self.assertEqual(result['primary'], 'answer')

    def test_successful_turn_does_not_add_trust(self):
        before = deepcopy(self.state['relationship'])
        self.core.decide(self.state, self.apply([event('question')]), observation(), self.trace)
        self.assertEqual(self.state['relationship'], before)

    def test_promise_can_only_be_fulfilled_once_by_promisor(self):
        ref = self.apply([event('promise', value='帶書')])[0]['id']
        trust = self.state['relationship']['trust']
        self.apply([event('fulfilled', ref=ref)], turn='b')
        self.apply([event('fulfilled', ref=ref)], turn='c')
        self.assertEqual(self.state['relationship']['trust'], trust + .5)
        self.assertFalse(self.apply([event('fulfilled', actor='character', ref=ref)], source='assistant', turn='d'))

    def test_repair_requires_specific_issue_and_resolves_only_once(self):
        self.assertFalse(self.apply([event('repair', ref='invented')]))
        ref = self.apply([event('misunderstanding')], turn='b')[0]['id']
        self.apply([event('repair', ref=ref)], turn='c')
        rel = deepcopy(self.state['relationship'])
        self.apply([event('repair', ref=ref)], turn='d')
        self.assertEqual(self.state['relationship'], rel)
        self.assertTrue(self.state['issues'][ref]['resolved'])

    def test_care_requires_started_activity_and_not_just_accepted_invite(self):
        self.state['control_mode'] = 'director'
        ref = self.apply([event('invite', value='按摩')])[0]['id']
        self.apply([event('accept', actor='character', ref=ref)], turn='b')
        self.assertFalse(self.apply([event('care_benefit', actor='character', ref=ref)], turn='c'))
        self.apply([event('start', ref=ref)], turn='d')
        self.apply([event('care_benefit', actor='character', ref=ref)], turn='e')
        self.assertLess(self.state['stress'], 20)
        self.assertGreater(self.state['emotions']['joy'], 0)

    def test_user_sadness_is_not_character_emotion(self):
        events = self.apply([event('disclosure', actor='user', target='character')])
        self.core.assess(self.state, events, observation(appraisals=[{'evidence': '證據', 'certainty': 'occurred', 'valence': 'obstructive', 'control': 'other', 'strength': 2}]), self.trace)
        self.assertEqual(self.state['emotions']['sadness'], 0)
        self.assertEqual(self.state['emotions']['anger'], 0)

    def test_invalid_numeric_state_is_rejected(self):
        for value in (float('nan'), True, '70'):
            bad = deepcopy(self.state); bad['energy'] = value
            with self.assertRaises(ValueError): validate_state(bad)


class FakeLLM:
    def __init__(self, results):
        self.results = iter(results)
        self.last_metrics = {'finish_reason': 'stop'}
        self.calls = []

    async def generate(self, messages, on_request=None, **kwargs):
        self.calls.append(deepcopy(messages))
        if on_request: on_request(deepcopy(messages))
        raw=next(self.results)
        # Shared semantic fixtures support both role contracts. Simulate the
        # response JSON grammar, without filling missing keys or fixing events.
        schema=kwargs.get('response_schema')
        if schema and set(schema.get('required',[]))-{'working_updates','life_event_disclosure'}=={'utterance','events','topic','appraisals','conversation_signal','appearance_changes'}:
            try:body=json.loads(raw)
            except (ValueError,TypeError):return raw
            if isinstance(body,dict):
                raw=json.dumps({k:v for k,v in body.items() if k not in
                    {'needs_clarification','missing_detail','asks_character','user_distress','user_affect','information_scope','story_control','external_request'}},ensure_ascii=False)
        return raw


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_observation_cache_freezes_interpretation_but_recalculates_parameters(self):
        start = initial_state(STAMP)
        cache = {}
        llm = FakeLLM([json.dumps(observation([event('question', evidence='多少')]))])
        runner = PyramidRunner(llm)
        one = await runner.run(deepcopy(start), TurnInput('多少', 'one'), observation_cache=cache)
        edited = deepcopy(start)
        edited['energy'] = 10
        edited['relationship']['trust'] = 5
        edited['emotions']['anger'] = 80
        two = await runner.run(edited, TurnInput('多少', 'two'), observation_cache=cache)
        self.assertEqual(len(llm.calls), 1)
        self.assertNotEqual(one.decision['delivery'], two.decision['delivery'])
        self.assertEqual(two.after['events'][0]['turn_id'], 'two')
        self.assertEqual(cache['source_run'], 'one')
        self.assertTrue(next(n for n in two.trace if n['rule'] == 'observation.cache')['output']['reused'])

    async def test_observation_cache_invalidates_changed_text_or_context(self):
        base = initial_state(STAMP)
        key = PyramidRunner.observation_key(base, '多少')
        for path, value in [('scene', {'location': '書房'}), ('history', [{'role': 'user', 'content': '先前對話'}]),
                            ('invitations', {'new': {'status': 'proposed'}}), ('now', '2026-10-15T01:00:00+00:00'),
                            ('adult_policy', {'mode': 'flirt', 'agent_may_initiate': True})]:
            changed = deepcopy(base)
            if path == 'scene': changed[path].update(value)
            elif path == 'invitations':
                changed[path] = {'new': {'status': 'proposed', 'actor': 'user', 'target': 'character'}}
            else: changed[path] = value
            self.assertNotEqual(key, PyramidRunner.observation_key(changed, '多少'), path)
        cache = {}
        llm = FakeLLM([json.dumps(observation([event('question', evidence='多少')]))] * 3)
        runner = PyramidRunner(llm)
        await runner.run(deepcopy(base), TurnInput('多少', 'one'), observation_cache=cache)
        await runner.run(deepcopy(base), TurnInput('多少呢', 'two'), observation_cache=cache)
        changed = deepcopy(base); changed['scene']['location'] = '書房'
        await runner.run(changed, TurnInput('多少呢', 'three'), observation_cache=cache)
        self.assertEqual(len(llm.calls), 3)

    async def test_cached_user_understanding_does_not_cache_writer_or_response_observer(self):
        base, cache = initial_state(STAMP), {}
        llm = FakeLLM([json.dumps(observation([event('question', evidence='多少')])),
                       '答案是三。', json.dumps(observation([event('answer', evidence='答案是三。', actor='character', target='user')]))])
        runner = PyramidRunner(llm)
        await runner.run(deepcopy(base), TurnInput('多少', 'one'), observation_cache=cache)
        result = await runner.run(deepcopy(base), TurnInput('多少', 'two', True), observation_cache=cache)
        self.assertEqual(len(llm.calls), 3)
        self.assertEqual(result.reply, '答案是三。')
        self.assertEqual(result.after['events'][-1]['source'], 'assistant')

    async def test_decision_only_and_trace_has_real_request(self):
        state = initial_state(STAMP)
        llm = FakeLLM([json.dumps(observation([event('question', evidence='多少', value='17*3')]))])
        result = await PyramidRunner(llm).run(state, TurnInput('17*3多少', 'one'))
        self.assertEqual(result.decision['primary'], 'answer')
        self.assertEqual(result.reply, '')
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(len(state['history']), 1)
        requests = [n for n in result.trace if n['rule'] == 'model.request']
        self.assertEqual(requests[0]['inputs']['messages'], llm.calls[0])

    async def test_writer_is_followed_by_actual_reply_extraction(self):
        state = initial_state(STAMP)
        llm = FakeLLM([json.dumps(observation([event('question', evidence='多少')])),
                       '答案是51。', json.dumps(observation())])
        result = await PyramidRunner(llm).run(state, TurnInput('17乘3多少', 'one', True))
        self.assertEqual(len(llm.calls), 3)
        self.assertEqual(result.reply, '答案是51。')
        self.assertEqual(state['history'][-1]['role'], 'assistant')
        self.assertFalse(state['activities'])

    async def test_truncation_does_not_commit_response(self):
        state = initial_state(STAMP)
        llm = FakeLLM([json.dumps(observation())])
        llm.last_metrics['finish_reason'] = 'length'
        with self.assertRaises(Exception): await PyramidRunner(llm).run(state, TurnInput('你好', 'one', True))
        self.assertFalse(state['history'])

    async def test_contiguous_summary_coverage_retains_tail(self):
        state = initial_state(STAMP)
        state['history'] = [{'role': 'user' if i % 2 == 0 else 'assistant', 'content': str(i)} for i in range(16)]
        llm = FakeLLM([json.dumps({'notes':[]}), json.dumps({'notes':[]})])
        trace = DecisionTrace()
        await PyramidRunner(llm).compact(state, trace)
        self.assertEqual(state['summary_through'], 4)
        self.assertEqual(state['history'][state['summary_through']]['content'], '4')
        batch = json.loads(llm.calls[1][-1]['content'])
        self.assertEqual([v['content'] for v in batch['messages']], ['2', '3'])
        self.assertEqual(len(state['history']), 16)


class RepositoryTests(unittest.TestCase):
    def test_sandboxes_are_isolated_and_resettable(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = SandboxRepository(temporary)
            a, b = repo.create(initial_state(STAMP)), repo.create(initial_state(STAMP))
            state = repo.read(a); state['energy'] = 5
            repo.write(a, state)
            self.assertEqual(repo.read(b)['energy'], 70)
            self.assertEqual(repo.read(a, 'baseline')['energy'], 70)
            with self.assertRaises(ValueError): repo.read('../memory')

    def test_production_import_is_read_only_and_queues_not_copied(self):
        from companion.store import Store
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'source.sqlite3'
            store = Store(path)
            store.update_persona('name', '來源角色')
            store.add_message('user', '原文')
            store.close()
            original = path.read_bytes()
            imported = import_production(path)
            self.assertEqual(imported['story']['name'], '來源角色')
            self.assertEqual(imported['history'][0]['content'], '原文')
            self.assertEqual(path.read_bytes(), original)
            self.assertNotIn('token', imported)
            self.assertNotIn('jobs', imported)

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.llm = FakeLLM([json.dumps(observation([event('question', evidence='多少')]))] * 8)
        self.service = WorkbenchService(Path(self.temp.name), llm=self.llm)

    def tearDown(self):
        self.service.submit(self.service.close())
        self.service.loop.call_soon_threadsafe(self.service.loop.stop)
        self.service.thread.join(timeout=2)
        self.service.loop.close()
        self.temp.cleanup()

    def api(self, method, path, body=None):
        return self.service.submit(self.service.api(method, path, body or {}))

    def create(self):
        return self.api('POST', '/api/sandboxes', {'source': 'clean'})['id']

    def finished(self, identifier):
        import time
        for _ in range(100):
            result = self.api('GET', '/api/runs/' + identifier)
            if result['status'] in {'done', 'failed', 'cancelled'}: return result
            time.sleep(.01)
        self.fail('job did not finish')

    def test_memory_pin_is_persistent_isolated_and_validated(self):
        from companion.pyramid_memory import add_batch
        sid, other = self.create(), self.create()
        path = '/api/sandboxes/' + sid
        state = self.service.repo.read(sid)
        state['history'] = [{'role':'user','content':'我喜歡茶'}]
        add_batch(state, 0, state['history'], [])
        self.service.repo.write(sid, state)
        note_id = state['memory']['notes'][0]['id']
        result = self.api('POST', path + '/memory', {'id':note_id, 'important':True})
        self.assertTrue(result['state']['memory']['notes'][0]['important'])
        self.assertTrue(self.api('GET',path)['state']['memory']['notes'][0]['important'])
        self.assertFalse(self.api('GET','/api/sandboxes/'+other)['state']['memory']['notes'])
        with self.assertRaises(ValueError): self.api('POST',path+'/memory',{'id':note_id,'important':'true'})

    def test_run_rerun_preserves_edited_parameters_and_same_history_start(self):
        sid = self.create()
        path = '/api/sandboxes/' + sid
        one = self.api('POST', path + '/runs', {'text': '多少'})
        first = self.finished(one['run_id'])
        self.assertEqual(first['status'], 'done')
        self.api('PATCH', path, {'patch': {'energy': 15}})
        two = self.api('POST', path + '/runs', {'text': '多少', 'rerun': True})
        second = self.finished(two['run_id'])
        self.assertEqual(second['before']['energy'], 15)
        self.assertEqual(second['before']['history'], first['before']['history'])
        self.assertEqual(len(second['after']['history']), 1)
        self.assertEqual(len(self.llm.calls), 1)
        self.assertTrue(next(n for n in second['trace'] if n['rule'] == 'observation.cache')['output']['reused'])
        third = self.finished(self.api('POST', path + '/runs',
                              {'text': '多少', 'rerun': True, 'reuse_observation': False})['run_id'])
        self.assertEqual(len(self.llm.calls), 2)
        self.assertFalse(next(n for n in third['trace'] if n['rule'] == 'observation.cache')['output']['reused'])
        exported = self.api('GET', path + '/export')
        self.assertEqual(len(exported['runs']), 3)
        self.assertNotIn('token', exported)

    def test_failed_model_does_not_modify_sandbox_and_trace_is_retained(self):
        self.llm.results = iter(['invalid JSON'])
        sid = self.create(); path = '/api/sandboxes/' + sid
        before = self.api('GET', path)['state']
        result = self.finished(self.api('POST', path + '/runs', {'text': '多少'})['run_id'])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.api('GET', path)['state'], before)
        self.assertTrue(result['trace'])

    def test_cancel_releases_busy_and_does_not_commit(self):
        import asyncio
        async def slow(*args, **kwargs): await asyncio.sleep(3600)
        self.llm.generate = slow
        sid = self.create(); path = '/api/sandboxes/' + sid
        before = self.api('GET', path)['state']
        job = self.api('POST', path + '/runs', {'text': '多少'})['run_id']
        self.api('POST', '/api/runs/' + job + '/cancel')
        result = self.finished(job)
        self.assertEqual(result['status'], 'cancelled')
        self.assertIsNone(self.service.active)
        self.assertEqual(self.api('GET', path)['state'], before)

    def test_numeric_and_uneditable_patch_rejected(self):
        path = '/api/sandboxes/' + self.create()
        for patch in ({'token': 'x'}, {'energy': float('inf')}, {'control_mode': 'wrong'}):
            with self.assertRaises(ValueError): self.api('PATCH', path, {'patch': patch})

    def test_http_origin_and_token_checks_and_static_page(self):
        from http.server import ThreadingHTTPServer
        from companion.workbench import Handler
        import threading, urllib.request, urllib.error
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.token, server.service = 'test-token', self.service
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with urllib.request.urlopen(base) as response:
                self.assertIn('角色實驗室', response.read().decode())
            with self.assertRaises(urllib.error.HTTPError): urllib.request.urlopen(base + '/api/meta')
            req = urllib.request.Request(base + '/api/meta', headers={'X-Workbench-Token': 'test-token'})
            with urllib.request.urlopen(req) as response:
                self.assertTrue(json.load(response)['local_only'])
            req = urllib.request.Request(base + '/api/meta', headers={'X-Workbench-Token': 'test-token', 'Origin': 'https://evil.example'})
            with self.assertRaises(urllib.error.HTTPError) as caught: urllib.request.urlopen(req)
            self.assertEqual(caught.exception.code, 403)
        finally:
            server.shutdown(); server.server_close(); thread.join()

class AdditionalContractTests(unittest.TestCase):
    setUp = CoreTests.setUp
    apply = CoreTests.apply
    def test_large_time_jump_integrates_activity_at_commit_boundary(self):
        self.state['energy'] = 50
        self.state['schedule'] = [{'at': STAMP, 'location': '書房', 'availability': 'busy'}]
        self.core.advance(self.state, '2026-10-15T01:00:00+00:00', self.trace)
        self.assertEqual(self.state['scene']['location'], '書房')
        self.assertAlmostEqual(self.state['energy'], 50 + 2 / 3 - 2, places=2)

    def test_profile_learning_is_grounded_and_does_not_increase_trust(self):
        trust = self.state['relationship']['trust']
        self.apply([event('profile_add', evidence='叫我Alex', actor='user', target='user', field='name', value='Alex')], text='叫我Alex')
        self.assertEqual(self.state['user_profile'][0]['value'], 'Alex')
        self.assertEqual(self.state['relationship']['trust'], trust)
        self.apply([event('profile_add', evidence='我的密碼是abc', actor='user', target='user', field='preference', value='abc')], text='我的密碼是abc', turn='b')
        self.assertEqual(len(self.state['user_profile']), 1)

    def test_tension_contributes_to_desire_once(self):
        self.state['adult_policy']['mode'] = 'intimate'
        self.core.derive_intimacy(self.state, self.trace)
        before = self.state['intimacy']['desire']
        self.state['intimacy']['tension'] = 40
        self.core.derive_intimacy(self.state, self.trace)
        self.assertAlmostEqual(self.state['intimacy']['desire'], before + 10, places=2)
        self.core.decide(self.state, [], observation(), self.trace)
        self.assertAlmostEqual(self.state['intimacy']['desire'], before + 10, places=2)

    def test_source_specific_appearance_does_not_change_character(self):
        self.apply([event('appearance', actor='user', field='hair', value='馬尾')])
        self.assertEqual(self.state['user_appearance']['hair'], '馬尾')
        self.assertEqual(self.state['appearance']['hair'], '')

class ExtendedRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_writer_prompt_has_qualitative_contract_not_scores(self):
        from companion.pyramid_runner import writer_contract
        s = initial_state(STAMP); trace = DecisionTrace()
        d = PyramidCore().decide(s, [], observation(), trace)
        contract = writer_contract(s, d)
        self.assertNotIn('relationship', contract)
        self.assertNotIn('traits', contract)
        self.assertNotIn('candidate', contract['decision'])
        self.assertIsInstance(contract['delivery']['warmth'], str)
        self.assertNotIn('confidence', json.dumps(contract))

    async def test_missing_mandatory_speech_act_fails_contract(self):
        bad = observation(); bad.pop('utterance')
        llm = FakeLLM([json.dumps(bad)])
        with self.assertRaises(ValueError):
            await PyramidRunner(llm).run(initial_state(STAMP), TurnInput('你好', 'one'))

    @mock_capacity("companion.context_plan.CAPACITY",4096)
    @mock_capacity("companion.pyramid_runner.CAPACITY",4096)
    @mock_capacity("companion.services.CONTEXT_CAPACITY",4096)
    async def test_preflight_capacity_failure_preserves_history_without_generation(self):
        from companion.services import ServiceError
        state=initial_state(STAMP)
        state['history']=[{'role':'user','content':'上輪問題'},{'role':'assistant','content':'上輪回答'}]
        class TooLarge(FakeLLM):
            async def count_context(self,messages):return 4096
        llm=TooLarge([]);before=deepcopy(state['history'])
        with self.assertRaises(ServiceError):await PyramidRunner(llm).run(state,TurnInput('多少','one'))
        self.assertEqual(state['history'],before)
        self.assertEqual(state['summary_through'],0)
        self.assertFalse(llm.calls)

class ConsistencyTests(unittest.TestCase):
    def test_refusal_after_planned_acceptance_is_reported_not_rewritten(self):
        from companion.pyramid_runner import response_consistency
        result = response_consistency({'primary': 'conditional_accept'}, {'utterance': {'act': 'refuse'}})
        self.assertEqual(result['status'], 'mismatch')
        self.assertEqual(result['observed_speech_act'], 'refuse')
    def test_unknown_observation_is_not_claimed_consistent(self):
        from companion.pyramid_runner import response_consistency
        self.assertEqual(response_consistency({'primary':'accept'}, {'utterance':{'act':'unknown'}})['status'],'unknown')

class CompoundResponseTests(unittest.TestCase):
    def test_primary_invite_with_valid_secondary_accept_is_not_false_mismatch(self):
        from companion.pyramid_runner import response_consistency
        decision = {'primary': 'conditional_accept', 'target_event': 'original'}
        accepted = [{'kind': 'invite', 'actor': 'character', 'ref': 'original'},
                    {'kind': 'accept', 'actor': 'character', 'ref': 'original'}]
        self.assertEqual(response_consistency(decision, {'utterance':{'act':'invite'}}, accepted)['status'], 'consistent')
        self.assertEqual(response_consistency(decision, {'utterance':{'act':'accept'}}, [])['status'], 'unknown')

class LinkedInvitationTests(unittest.TestCase):
    def test_compound_response_updates_original_invitation_without_duplicate_or_start(self):
        state, core, trace = initial_state(STAMP), PyramidCore(), DecisionTrace()
        original = core.apply(state, [event('invite', value='按摩')], 'user', '證據', 'one', trace)[0]['id']
        core.apply(state, [event('invite', actor='character', target='user', value='開始按摩', ref=original),
                           event('accept', actor='character', target='user', ref=original)],
                   'assistant', '證據', 'one', trace)
        self.assertEqual(len(state['invitations']), 1)
        self.assertEqual(state['invitations'][original]['status'], 'accepted')
        self.assertEqual(state['activities'], {})
