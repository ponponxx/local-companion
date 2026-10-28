import unittest
from companion.state_brief import state_brief


class StateBriefTests(unittest.TestCase):
    def test_uses_current_projection_without_guessing_unknown_or_pending_arrival(self):
        state={'scene':{'location':'書房','character_location':'書房','user_location':'花園',
                        'pending':{'location':'餐廳','delivery':'delivered'}},
               'appearance':{'hair':'盤髮','legacy_outfit':'白色襯衫、黑色靴子',
                             'clothing':{'shoes':{'description':'黑色靴子','worn':False}}}}
        brief=state_brief(state)
        self.assertIn('角色在書房',brief);self.assertIn('你的位置：花園',brief)
        self.assertIn('移動中，尚未抵達：餐廳',brief)
        self.assertIn('髮型：盤髮',brief);self.assertIn('上衣：白色襯衫',brief)
        self.assertIn('鞋子：未穿著',brief);self.assertNotIn('鞋子：黑色靴子',brief)
        self.assertIn('未確認：',brief);self.assertNotIn('未穿著內衣',brief)

    def test_preference_prose_and_body_are_not_current_clothing(self):
        brief=state_brief({'appearance':{'body':'private body description','legacy_outfit':'偏好襯衫或長袍'}})
        self.assertIn('衣著：尚未確認',brief);self.assertNotIn('偏好',brief)
        self.assertNotIn('private body',brief);self.assertIn('你的位置：未確認',brief)
