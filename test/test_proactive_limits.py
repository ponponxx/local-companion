import unittest
from companion.proactive_limits import parse_limit, limit_label, limit_reached

class LimitTests(unittest.TestCase):
    def test_parse_and_validate(self):
        for value in ['unlimited','不限','不限制','無上限']:
            self.assertEqual(parse_limit(value),'unlimited')
            self.assertEqual(limit_label(value),'不限制')
            self.assertFalse(limit_reached(value,999))
        self.assertEqual(parse_limit('20'),'20')
        self.assertTrue(limit_reached('0',0))
        for value in ['-1','2.5','NaN','']:
            with self.assertRaises(ValueError):parse_limit(value)
