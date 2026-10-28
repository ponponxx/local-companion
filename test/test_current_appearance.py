"""Current-clothing projection uses neutral, completed wardrobe changes."""
from copy import deepcopy
import json
import unittest
from datetime import datetime
from companion.visual_state import current_appearance, clothing_description, clothing_baseline, reset_morning_appearance
from companion.pyramid import initial_state, PyramidCore, DecisionTrace
from companion.pyramid_runner import writer_contract, model_view


class CurrentAppearanceTests(unittest.TestCase):
    def state(self):
        s=initial_state('2026-10-16T09:00:00+08:00')
        s['appearance'].update(legacy_outfit='灰色外套、白襯衫、黑襪、棕色靴子',hair='馬尾')
        return s

    def decision(self,s):return PyramidCore().decide(s,[],{'missing_detail':'','appraisals':[]},DecisionTrace())

    def test_slot_changes_replace_baseline_without_losing_other_clothes(self):
        s=self.state();s['appearance']['clothing']['socks']={'worn':False,'description':'已脫下','source':'example'}
        s['appearance']['clothing']['shoes']={'worn':True,'description':'藍色運動鞋','source':'example2'}
        before=deepcopy(s)
        result=current_appearance(s['appearance'])
        self.assertEqual(result['clothing']['socks'],'未穿著')
        self.assertEqual(result['clothing']['shoes'],'藍色運動鞋')
        self.assertEqual(result['clothing']['top'],'白襯衫')
        self.assertEqual(result['clothing']['outerwear'],'灰色外套')
        self.assertNotIn('黑襪',json.dumps(result,ensure_ascii=False))
        self.assertNotIn('棕色靴子',json.dumps(result,ensure_ascii=False))
        self.assertNotIn('legacy_outfit',result)
        self.assertEqual(s,before)

    def test_observer_writer_and_image_use_same_projection(self):
        s=self.state();s['appearance']['clothing']['outerwear']={'worn':False,'description':'已脫下','source':'test'}
        current=current_appearance(s['appearance'])
        self.assertEqual(model_view(s)['appearance'],current)
        self.assertEqual(writer_contract(s,self.decision(s))['current_facts']['appearance'],current)
        image=clothing_description(s['appearance'])
        self.assertIn('外衣：未穿著',image)
        self.assertNotIn('灰色外套',image)
        self.assertIn('上衣：白襯衫',image)

    def test_ambiguous_or_preferred_baseline_does_not_claim_current_wearing(self):
        for text in ['白襯衫或黑上衣','偏好黑色外套','偏好白襯衫、黑襪','白襯衫搭配黑色長褲','白襯衫、黑襯衫','依場合換裝']:
            with self.subTest(text=text):
                appearance={'legacy_outfit':text,'clothing':{}}
                result=current_appearance(appearance)
                self.assertTrue(all(v=='未確認' for v in result['clothing'].values()))
                self.assertTrue(clothing_baseline(text)[1])
                self.assertEqual(clothing_description(appearance),'衣著尚未確認')

    def test_unknown_is_not_removed_and_no_cross_actor_transfer(self):
        s=self.state();s['user_appearance']['clothing']['shoes']={'worn':False,'description':'已脫下'}
        result=writer_contract(s,self.decision(s))['current_facts']
        self.assertEqual(result['user_appearance']['clothing']['shoes'],'未穿著')
        self.assertEqual(result['appearance']['clothing']['shoes'],'棕色靴子')
        self.assertEqual(result['appearance']['clothing']['underwear'],'未確認')

    def test_completed_event_persists_across_serialization_proposal_does_not(self):
        s=self.state();core=PyramidCore();trace=DecisionTrace()
        event={'kind':'appearance','actor':'character','target':'character','stage':'completed','field':'outerwear',
               'value':'已脫下','evidence':'角色已脫下外套','ref':'','strength':1}
        self.assertTrue(core.apply(s,[event],'user','角色已脫下外套','change',trace))
        s=json.loads(json.dumps(s))
        self.assertEqual(current_appearance(s['appearance'])['clothing']['outerwear'],'未穿著')
        event.update(stage='proposed',value='灰色外套',evidence='讓我幫你穿上外套')
        self.assertFalse(core.apply(s,[event],'user',event['evidence'],'proposal',trace))
        self.assertEqual(current_appearance(s['appearance'])['clothing']['outerwear'],'未穿著')

    def test_morning_reset_establishes_new_baseline(self):
        s=self.state();s['appearance_day']='2026-10-15'
        s['appearance_defaults']={'clothing':'白襯衫、黑鞋','hair':'披髮'}
        s['appearance']['clothing']['shoes']={'worn':False,'description':'已脫下'}
        reset_morning_appearance(s,datetime.fromisoformat('2026-10-16T08:00:00+08:00'))
        self.assertEqual(current_appearance(s['appearance'])['clothing']['shoes'],'黑鞋')

    def test_current_fact_has_no_provenance_or_duplicate_outfit(self):
        s=self.state();s['appearance']['clothing']['shoes']={'worn':False,'description':'已脫下','source':'private-event-id'}
        contract=writer_contract(s,self.decision(s));raw=json.dumps(contract['current_facts']['appearance'],ensure_ascii=False)
        self.assertNotIn('private-event-id',raw)
        self.assertNotIn('legacy_outfit',raw)
        self.assertIn('未穿著',contract['appearance_instruction'])
        self.assertIn('未確認',contract['appearance_instruction'])

    def test_english_baseline_and_removed_baseline(self):
        self.assertEqual(clothing_baseline('white shirt; black boots')[0],{'top':'white shirt','shoes':'black boots'})
        self.assertEqual(current_appearance({'legacy_outfit':'外套已脫下'})['clothing']['outerwear'],'未穿著')
