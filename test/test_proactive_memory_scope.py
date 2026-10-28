from copy import deepcopy
from datetime import datetime
import unittest
from companion import working_memory as wm
from companion.pyramid import initial_state,DecisionTrace,PyramidCore
from companion.pyramid_runner import PyramidRunner,writer_prefix
from test_pyramid import FakeLLM,STAMP
from scripts.working_memory_cases import append,update

class ProactiveMemoryScopeTests(unittest.IsolatedAsyncioTestCase):
    def state(self):
        s=initial_state(STAMP)
        append(s,'我準備好了，是否現在去醫院？',updates=[update('準備好了',kind='interaction',canonical='使用者曾詢問是否現在去醫院')])
        ref=wm.current_items(s)[0]['id']
        append(s,'你剛才詢問是否現在去醫院。','assistant',[update('詢問是否現在去醫院',op='confirm',ref=ref,kind='interaction',canonical='使用者曾詢問是否現在去醫院')])
        return s
    def test_own_leaf_story_does_not_import_unrelated_hospital_note(self):
        s=self.state();before=deepcopy(s)
        text,audit=wm.contextual_reading(s,{'subtype':'life_event','story':'角色在溫室觀察葉片。'})
        self.assertNotIn('醫院',text);self.assertEqual(audit['omitted'],1);self.assertEqual(s,before)

    def test_related_followup_keeps_source_linked_note(self):
        s=self.state();text,audit=wm.contextual_reading(s,{'subtype':'story','source_index':0})
        self.assertIn('醫院',text);self.assertEqual(audit['included'],1)
        self.assertIn('不據此推定',text);self.assertNotIn('尚未解除',text)

    def test_unrelated_persistent_fiction_is_not_erased(self):
        s=self.state();append(s,'我目前是一隻藍色紙鶴。',updates=[update('藍色紙鶴',kind='fiction',canonical='使用者目前是藍色紙鶴')])
        text,_=wm.contextual_reading(s,{'subtype':'life_event'})
        self.assertIn('藍色紙鶴',text);self.assertNotIn('醫院',text)

    def test_foreground_preserves_existing_notes_without_future_obligation_label(self):
        s=self.state();text,audit=wm.contextual_reading(s)
        self.assertIn('醫院',text);self.assertEqual(audit['scope'],'foreground')
        self.assertNotIn('尚未解除的共同情節',text)

    async def test_proactive_actual_prompt_and_debug_use_the_same_filtered_notes(self):
        s=self.state();trace=DecisionTrace()
        d=PyramidCore().decide(s,[],{'utterance':{'act':'question','purpose':'other'},'appraisals':[]},trace)
        d.update(primary='share_self',information_scope='current_state',content='葉片',
            story_candidate={'subtype':'life_event','story':'角色在溫室看到葉片。','outline':'分享葉片觀察。'})
        messages=await PyramidRunner(FakeLLM([]),clock=lambda:datetime.fromisoformat(s['now'])).prepare_writer(
            s,d,writer_prefix('CH'),trace,output_tokens=256,control={'role':'user','content':'請撰寫主動訊息。'})
        self.assertNotIn('醫院',str(messages))
        note=next(n for n in trace.nodes if n['rule']=='memory.packet')
        self.assertNotIn('醫院',note['output']['text'])
        self.assertTrue(note['inputs']['omitted'])
