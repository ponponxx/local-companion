import unittest

from companion.adult_intimacy import (
    advance_intimacy_state, choose_intimacy_direction, finalize_intimacy_state,
    normalized_policy,
)


TRAITS = {
    'flirtatiousness': 80, 'sexual_openness': 80, 'excitation_sensitivity': 75,
    'inhibition_sensitivity': 20, 'responsive_desire': 80, 'erotic_expressiveness': 70,
}
RELATIONSHIP = {'erotic_interest': 75, 'sexual_comfort': 70, 'intimate_safety': 80, 'tension': 0}
PHYSICAL = {'energy': 80, 'stress': 10}


class AdultIntimacyPacingTests(unittest.TestCase):
    def test_off_is_a_hard_envelope(self):
        state = advance_intimacy_state({}, {'mode': 'off'}, TRAITS, RELATIONSHIP, PHYSICAL, '撩我啊')
        self.assertEqual(state['heat'], 0)
        self.assertEqual(choose_intimacy_direction({'mode': 'off'}, state, RELATIONSHIP)['direction'], 'none')

    def test_flirt_mode_caps_heat_and_never_chooses_deepen(self):
        state = advance_intimacy_state(
            {'heat': 38, 'receptivity': 70, 'momentum': 50}, {'mode': 'flirt'},
            TRAITS, RELATIONSHIP, PHYSICAL, '我想跟你親密互動',
        )
        self.assertEqual(state['heat'], 40)
        self.assertEqual(choose_intimacy_direction({'mode': 'flirt'}, state, RELATIONSHIP)['direction'], 'playful_probe')

    def test_intimate_mode_advances_one_step_from_user_signal(self):
        state = advance_intimacy_state({}, {'mode': 'intimate'}, TRAITS, RELATIONSHIP, PHYSICAL, '我想跟你親密互動')
        plan = choose_intimacy_direction({'mode': 'intimate'}, state, RELATIONSHIP)
        self.assertEqual(plan['direction'], 'deepen_intimacy')
        delivered = finalize_intimacy_state(state, {'mode': 'intimate'}, plan['direction'])
        self.assertGreater(delivered['heat'], state['heat'])

    def test_agent_can_initiate_when_motivation_is_ready(self):
        state = {'heat': 0, 'desire': 70, 'receptivity': 40, 'tension': 0, 'inhibition': 20,
                 'momentum': 0, 'mutuality': 0, 'last_signal': 'neutral', 'last_direction': 'none'}
        plan = choose_intimacy_direction({'mode': 'flirt', 'agent_may_initiate': True}, state, RELATIONSHIP)
        self.assertEqual(plan['direction'], 'warm_tease')

    def test_stop_immediately_clears_escalation(self):
        prior = {'heat': 75, 'desire': 80, 'receptivity': 80, 'tension': 60,
                 'inhibition': 10, 'momentum': 70, 'mutuality': 65,
                 'last_signal': 'receptive', 'last_direction': 'deepen_intimacy'}
        state = advance_intimacy_state(prior, {'mode': 'intimate'}, TRAITS, RELATIONSHIP, PHYSICAL, '停下，我不想繼續')
        self.assertEqual((state['heat'], state['momentum'], state['mutuality']), (0, 0, 0))
        self.assertEqual(choose_intimacy_direction({'mode': 'intimate'}, state, RELATIONSHIP)['direction'], 'cool_down')

    def test_dont_stop_is_receptive_inside_an_active_exchange(self):
        prior = {'heat': 25, 'desire': 60, 'receptivity': 55, 'tension': 20,
                 'inhibition': 20, 'momentum': 30, 'mutuality': 20,
                 'last_signal': 'flirt', 'last_direction': 'playful_probe'}
        state = advance_intimacy_state(prior, {'mode': 'intimate'}, TRAITS, RELATIONSHIP, PHYSICAL, '不要停，繼續吧')
        self.assertEqual(state['last_signal'], 'receptive')
        self.assertGreater(state['heat'], prior['heat'])

    def test_model_cannot_raise_policy_range(self):
        self.assertEqual(normalized_policy({'mode': 'anything', 'maximum_heat': 100})['mode'], 'off')


if __name__ == '__main__':
    unittest.main()
