import json
import unittest
from copy import deepcopy
from datetime import datetime,timedelta
from companion.appearance_observation import events,SCHEMA
from companion.pyramid import initial_state,PyramidCore,DecisionTrace,TurnInput
from companion.pyramid_runner import PyramidRunner
from companion.visual_state import current_appearance
from companion.photo_objects import options,render
from companion.app import App
from test_pyramid import observation,FakeLLM
import test_pyramid_telegram as fixtures

AT='2026-10-19T10:00:00+08:00'
def change(wearer='character',slot='outerwear',operation='wear',value='長袍',phase='completed',evidence='妳的長袍已經披上囉'):
    return dict(wearer=wearer,slot=slot,operation=operation,value=value,phase=phase,evidence=evidence)

class AppearanceBoundaryTests(unittest.TestCase):
    def test_wearer_drives_state_and_unknown_slots_not_invented(self):
        s=initial_state(AT);trace=DecisionTrace();text='妳的長袍已經披上囉'
        proposals=events([change()],text,trace)
        PyramidCore().apply(s,proposals,'user',text,'one',trace)
        self.assertTrue(s['appearance']['clothing']['outerwear']['worn'])
        self.assertEqual(s['user_appearance']['clothing'],{})
        self.assertEqual(current_appearance(s['appearance'])['clothing']['shoes'],'未確認')
    def test_fabricated_evidence_and_names_rejected(self):
        trace=DecisionTrace()
        self.assertEqual(events([change(evidence='以前的長袍已穿好')],'妳的長袍已經披上囉',trace),[])
        self.assertEqual(events([change(value='紅色長袍')],'妳的長袍已經披上囉',trace),[])
    def test_negation_proposal_and_unknown_do_not_change_state(self):
        for phase in ['proposed','negated','unknown']:
            self.assertEqual(events([change(phase=phase)],'妳的長袍已經披上囉',DecisionTrace()),[])
    def test_user_clothes_and_explicit_removal(self):
        for wearer in ['user','character']:
            text='已脫下黑色靴子';s=initial_state(AT);trace=DecisionTrace()
            rows=events([change(wearer=wearer,slot='shoes',operation='remove',value='',evidence=text)],text,trace)
            PyramidCore().apply(s,rows,'user',text,wearer,trace)
            a=s['appearance'] if wearer=='character' else s['user_appearance']
            self.assertEqual(current_appearance(a)['clothing']['shoes'],'未穿著')
    def test_ornament_projection_deduplicates_without_destroying_gear(self):
        self.assertEqual(current_appearance({'jewelry':'銀耳環','accessories':'銀耳環'})['accessories'],'無')
        self.assertEqual(current_appearance({'jewelry':'銀耳環','accessories':'皮包'})['accessories'],'皮包')

class PhotoObjectTests(unittest.TestCase):
    def test_tea_staging_requires_supported_source_and_no_state_mutation(self):
        dialogue=[{'role':'user','content':'我來泡茶，茶點麻煩妳準備'}]
        plan={'photo_objects':[{'object':'teacup','placement':'held','source':1}]}
        old=deepcopy(plan);result=render(plan,'拍一張',dialogue)
        self.assertIn('茶杯',result);self.assertIn('杯中內容不指定',result);self.assertEqual(plan,old)
        self.assertEqual(render(plan,'拍一張',[]),'')
    def test_unrelated_sources_and_forged_objects_are_ignored(self):
        for row in [{'object':'teacup','placement':'held','source':0}, {'object':'broom','placement':'held','source':0}]:
            self.assertEqual(render({'photo_objects':[row]},'你好',[]),'')
    def test_explicit_request_and_duplicate_object_once(self):
        row={'object':'teacup','placement':'held','source':0}
        self.assertEqual(render({'photo_objects':[row,row]},'拿茶杯拍一張',[]).count('一只茶杯'),1)
        self.assertNotIn('text',options('拿茶杯拍一張',[])[0])

class ObserverBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_raw_current_text_last_and_source_data_once(self):
        s=initial_state(AT);s['history']=[{'role':'user','content':'昨天沒有長袍','at':AT}]
        obs=observation(appearance_changes=[change()]);llm=FakeLLM([json.dumps(obs)])
        result=await PyramidRunner(llm).observe(s,'妳的長袍已經披上囉','user',DecisionTrace())
        messages=llm.calls[0]
        self.assertEqual(messages[-1]['content'],'妳的長袍已經披上囉')
        self.assertNotIn('current_text',json.loads(messages[1]['content']))
        self.assertEqual(result['events'][0]['actor'],'character')
        self.assertEqual(messages[1]['content'].count('妳的長袍已經披上囉'),0)

class PhotoContextTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.TelegramPyramidTests.asyncSetUp
    asyncTearDown=fixtures.TelegramPyramidTests.asyncTearDown
    async def test_photo_uses_fresh_same_scene_sources_only(self):
        at=datetime.fromisoformat(AT);self.app.now=lambda:at
        s=self.app.pyramid.load();s['scene'].update(location='花園',character_location='花園');self.app.pyramid.save_clock(s)
        for content,location,minutes in [('我來泡茶','花園',5),('昨天的書','花園',30),('另一間房裡的花束','書房',2)]:
            self.store.add_message('user',content,at=(at-timedelta(minutes=minutes)).isoformat(),context={'scene':{'character_location':location}})
        snap=self.app.visual_snapshot('user_photo')
        self.assertEqual([r['content'] for r in snap['photo_dialogue']],['我來泡茶'])
        plan={'photo_objects':[{'object':'teacup','placement':'held','source':1}]}
        text=self.app.visual_prompt('user_photo','拍一張',plan,snapshot=snap)
        self.assertIn('角色一隻手持一只茶杯',text)
        self.assertEqual(self.app.pyramid.load()['presentation'],{})

class PhotoConstraintTests(unittest.TestCase):
    def test_explicit_ban_and_user_ownership_remove_options_even_with_tea_context(self):
        dialogue=[{'role':'user','content':'我來泡茶'}]
        for request in ['不要拿茶杯','唯一的茶杯在我手上，妳不要拿它','雙手空著拍一張']:
            self.assertEqual(render({'photo_objects':[{'object':'teacup','placement':'held','source':1}]},request,dialogue),'')
    def test_negative_book_does_not_block_requested_cup(self):
        request='不要拿書，拿茶杯拍一張'
        choices=options(request,[])
        self.assertIn('teacup',choices[0]['objects']);self.assertNotIn('book',choices[0]['objects'])
    def test_staging_default_when_model_omits_prop_and_explicit_object_overrides_old_activity(self):
        dialogue=[{'role':'user','content':'我來泡茶'}]
        self.assertIn('茶杯',render({},'拍一張',dialogue))
        self.assertNotIn('茶杯',render({},'拿書拍一張',dialogue))

class TypedPoseTests(unittest.TestCase):
    def test_free_pose_cannot_reintroduce_forbidden_prop(self):
        from types import SimpleNamespace
        snap={'appearance_authoritative':True,'identity_anchors':'28歲成年角色','current_clothing':'白色襯衫',
            'current_accessories':'無','current_jewelry':'無','current_scene':'花園','scene_facts':{'facts':{}},
            'background_context':{'matched':True,'location':'花園','background':'石徑與長椅'},
            'photo_dialogue':[{'role':'user','content':'我來泡茶'}]}
        plan={'pose':'雙手捧著茶杯','posture':'standing','hand_pose':'sides','body_lean':'none',
              'photo_objects':[{'object':'teacup','placement':'held','source':1}]}
        prompt=App.visual_prompt(SimpleNamespace(),'user_photo','不要拿茶杯',plan,snapshot=snap)
        self.assertNotIn('茶杯',prompt);self.assertIn('雙手自然放在身側',prompt)
    def test_prop_owns_hand_action_and_posture_is_independent(self):
        from companion.photo_objects import pose
        text=pose({'posture':'seated','body_lean':'back','hand_pose':'hips'},'角色手持茶杯')
        self.assertEqual(text,'坐著，上半身微微後仰')
    def test_typed_pose_validation_rejects_arbitrary_prose(self):
        from companion.app import parse_visual_plan
        with self.assertRaises(ValueError):parse_visual_plan(json.dumps({'posture':'雙手捧著杯子'}))
