from copy import deepcopy
import json
import unittest
from companion.delivery_rules import band, compile_delivery, character_identity
from companion.pyramid import initial_state, PyramidCore, DecisionTrace
from companion.pyramid_runner import writer_contract, writer_prefix
from companion.reply_brief import compile_reply_brief

STAMP='2026-10-21T04:00:00+00:00'

class DeliveryLookupTests(unittest.TestCase):
    def fixture(self):
        s=initial_state(STAMP)
        d=PyramidCore().decide(s,[],{'utterance':{'act':'question','purpose':'other'},
            'appraisals':[],'user_distress':False},DecisionTrace())
        d.update(primary='answer',content='你好嗎？',intimacy_intention='none')
        return s,d

    def test_quartiles_match_controls_and_boundary(self):
        self.assertEqual([band(n) for n in (12,37,62,87)],[0,1,2,3])
        self.assertEqual([band(n) for n in (0,24.99,25,49.99,50,74.99,75,100)],[0,0,1,1,2,2,3,3])
        for invalid in (True,float('nan'),float('inf'),'87'):
            with self.assertRaises(ValueError):band(invalid)

    def test_same_action_distinct_trait_context_wording_without_mutation(self):
        s,d=self.fixture();before=deepcopy((s,d));outputs=[]
        for level in (12,37,62,87):
            case=deepcopy(d);case['delivery'].update(warmth=level,agency=level,teasing=level)
            value,audit=compile_delivery(s,case,compile_reply_brief(s,case))
            outputs.append(value)
            self.assertEqual(case['primary'],'answer')
            self.assertEqual(audit['levels']['teasing'],band(level)+1)
        self.assertEqual(len({json.dumps(v,ensure_ascii=False) for v in outputs}),4)
        self.assertEqual((s,d),before)

    def test_relationship_and_stress_change_teasing_via_existing_formula(self):
        s,_=self.fixture();s['traits']['teasing']=100;s['stress']=0
        values=[]
        for comfort in (12,87):
            s['relationship'].update(trust=100,comfort=comfort)
            d=PyramidCore().decide(s,[],{'utterance':{'act':'question','purpose':'other'},'appraisals':[]},DecisionTrace())
            values.append(compile_delivery(s,d,compile_reply_brief(s,d))[1]['levels']['teasing'])
        self.assertGreater(values[1],values[0])
        s['stress']=87
        d=PyramidCore().decide(s,[],{'utterance':{'act':'question','purpose':'other'},'appraisals':[]},DecisionTrace())
        self.assertEqual(compile_delivery(s,d,compile_reply_brief(s,d))[1]['levels']['teasing'],1)

    def test_pause_support_repair_override_humor_only(self):
        for primary,engagement in [('repair','continue'),('support','continue'),('refuse','respect_pause'),('answer','rest')]:
            s,d=self.fixture();d['primary']=primary;d['delivery'].update(teasing=100,surprise_expression=100)
            brief=compile_reply_brief(s,d);brief['chat_engagement']=engagement
            output,audit=compile_delivery(s,d,brief)
            self.assertEqual(audit['levels']['teasing'],1)
            self.assertEqual(audit['levels']['surprise_expression'],1)
            self.assertEqual(d['primary'],primary)
            self.assertNotIn('adult_expression',output)

    def test_no_freeform_personality_or_catchphrase_leak(self):
        s,d=self.fixture()
        s['story'].update(background='成年圖書管理員',personality='PERSONALITY_SECRET',tone='TONE_SECRET',other='EXTRA_SECRET')
        output=writer_contract(s,d)
        encoded=json.dumps(output,ensure_ascii=False)
        for forbidden in ('PERSONALITY_SECRET','TONE_SECRET','EXTRA_SECRET'):
            self.assertNotIn(forbidden,encoded)
        self.assertEqual(output['character_definition'],{'background':'成年圖書管理員'})
        self.assertIn('PERSONALITY_SECRET',s['story']['personality'])
        self.assertNotIn('moderate',json.dumps(output['delivery']))
        self.assertEqual(output['delivery']['length'],'normal')
        self.assertIn('口吻規則定案',writer_prefix('CH'))

    def test_each_language_preserves_language_control(self):
        for code,target in [('CH','繁體中文'),('EN','English'),('JP','日本語')]:
            self.assertIn('只用'+target,writer_prefix(code))

    def test_legacy_description_cannot_change_lookup(self):
        s,d=self.fixture();first=writer_contract(s,d)['delivery']
        s['story'].update(personality='拒絕所有事',tone='永遠先嘲諷再說我父親')
        self.assertEqual(writer_contract(s,d)['delivery'],first)

    def test_settings_offer_parameters_not_deprecated_prose(self):
        from companion.telegram_settings import FIELDS
        self.assertNotIn('persona.personality',FIELDS)
        self.assertNotIn('persona.tone',FIELDS)
        self.assertIn('trait.teasing',FIELDS)
        self.assertIn('persona.background',FIELDS)

# Settings commands must not silently accept now-inactive prose.
import test_pyramid_telegram as fixtures
class DeliveryCommandTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.TelegramPyramidTests.asyncSetUp
    asyncTearDown = fixtures.TelegramPyramidTests.asyncTearDown
    text = fixtures.TelegramPyramidTests.text

    async def test_deprecated_persona_fields_redirect_without_write_or_model(self):
        before=deepcopy(self.store.persona())
        for i,field in enumerate(('personality','tone'),100):
            await self.text('/persona '+field+' 所有事情一律拒絕',i)
        self.assertEqual(self.store.persona(),before)
        self.assertEqual(self.llm.calls,[])
        self.assertEqual(self.store.recent(),[])

    async def test_persona_view_only_shows_identity_background(self):
        await self.text('/persona',100)
        body=self.tg.send.call_args.args[1]
        self.assertIn('background',body)
        self.assertNotIn('personality：',body)
        self.assertNotIn('tone：',body)
