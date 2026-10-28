from unittest.mock import patch as mock_capacity
"""Neutral continuity, provenance, lifecycle, and actual Writer projection tests."""
from copy import deepcopy
import json
import unittest
from companion import working_memory as wm
from companion.pyramid import initial_state,PyramidCore,DecisionTrace,TurnInput
from companion.pyramid_runner import PyramidRunner,observer_snapshot
from companion.services import ServiceError
from companion.story_core import StoryCore
from companion.story_events import annotate
from test_pyramid import FakeLLM,observation,STAMP


def patch(text,op='add',ref='',kind='fiction',subject='user',phase='completed'):
    canonical={'user':'使用者','character':'角色','shared':'雙方'}[subject]+'：'+text.replace('我','使用者').replace('你','使用者').replace('妳','角色')
    return dict(op=op,ref=ref,kind=kind,subject=subject,text=canonical,phase=phase,evidence=text)


def append(s,text,role='user',updates=()):
    m={'role':role,'content':text,'turn_id':str(len(s['history'])),'at':STAMP}
    s['history'].append(m)
    StoryCore().record(s,m,observation(working_updates=list(updates)))
    return m


class WorkingTests(unittest.TestCase):
    def setUp(self):self.s=initial_state(STAMP)
    def form(self):
        append(self.s,'我把你變成藍色紙鶴了。','assistant',[patch('變成藍色紙鶴')])
        return wm.current_items(self.s)[0]['id']

    def test_writer_reading_retains_subject_distinct_from_speaker(self):
        ref=self.form()
        text=wm.reading(self.s)
        self.assertIn('角色的說法',text)
        self.assertIn('主體：使用者',text)
        append(self.s,'我是一隻紙鶴',updates=[patch('我是一隻紙鶴',op='confirm',ref=ref)])
        self.assertIn('主體：使用者',wm.reading(self.s))
        self.assertIn('依據來自角色',wm.reading(self.s))
        self.assertNotIn('主體：角色',wm.reading(self.s))

    def test_two_sources_promote_and_survive_long_gap(self):
        ref=self.form();append(self.s,'我現在是一隻紙鶴。',updates=[patch('是一隻紙鶴',op='confirm',ref=ref)])
        self.assertEqual(wm.current_items(self.s)[0]['status'],'shared')
        for i in range(20):append(self.s,'換個話題'+str(i))
        self.s['now']='2026-10-17T12:00:00+00:00'
        self.assertIn('雙方已承接',wm.reading(self.s));self.assertIn('藍色紙鶴',wm.reading(self.s))

    def test_same_speaker_and_proposal_do_not_confirm(self):
        ref=self.form()
        append(self.s,'是一隻紙鶴','assistant',[patch('是一隻紙鶴',op='confirm',ref=ref)])
        append(self.s,'也許是一隻紙鶴',updates=[patch('是一隻紙鶴',op='confirm',ref=ref,phase='hypothetical')])
        self.assertEqual(wm.current_items(self.s)[0]['status'],'reported')

    def test_invalid_quote_unknown_ref_and_subject_rejected(self):
        ref=self.form();before=len(wm.current_items(self.s))
        append(self.s,'只是聊天',updates=[patch('從未說過'),patch('只是聊天',op='confirm',ref='bad'),patch('只是聊天',op='confirm',ref=ref,subject='character')])
        self.assertEqual(len(wm.current_items(self.s)),before)
        self.assertEqual(wm.current_items(self.s)[0]['status'],'reported')

    def test_character_denial_does_not_erase_shared_setting(self):
        ref=self.form();append(self.s,'我是一隻紙鶴',updates=[patch('我是一隻紙鶴',op='confirm',ref=ref)])
        append(self.s,'你不是紙鶴','assistant',[patch('你不是紙鶴',op='replace',ref=ref,phase='negated')])
        self.assertEqual(wm.current_items(self.s)[0]['status'],'shared')
        self.assertIn('不同說法',wm.reading(self.s))

    def test_explicit_ending_closes_and_new_fact_stays(self):
        ref=self.form();append(self.s,'我是一隻紙鶴',updates=[patch('我是一隻紙鶴',op='confirm',ref=ref)])
        append(self.s,'我恢復人形了',updates=[patch('恢復人形',op='close',ref=ref),patch('恢復人形')])
        self.assertNotIn('藍色紙鶴',wm.reading(self.s));self.assertIn('恢復人形',wm.reading(self.s))

    def test_user_correction_supersedes_without_deleting_old(self):
        ref=self.form();append(self.s,'更正，是綠色紙鶴',updates=[patch('綠色紙鶴',op='replace',ref=ref)])
        self.assertNotIn('藍色紙鶴',wm.reading(self.s));self.assertIn('綠色紙鶴',wm.reading(self.s))
        self.assertEqual(len(self.s['story_memory']['working']['items']),2)

    def test_shared_replacement_requires_other_participant(self):
        append(self.s,'書籤交給角色',updates=[patch('書籤交給角色',kind='situation',subject='shared')])
        ref=wm.current_items(self.s)[0]['id']
        append(self.s,'角色保管書籤','assistant',[patch('角色保管書籤',op='confirm',ref=ref,kind='situation',subject='shared')])
        append(self.s,'書籤已歸還使用者',updates=[patch('書籤已歸還使用者',op='replace',ref=ref,kind='situation',subject='shared')])
        items=wm.current_items(self.s);self.assertEqual(len(items),2)
        pending=next(i for i in items if i.get('pending_replaces')==ref)
        self.assertEqual(items[0]['status'],'shared');self.assertIn('舊設定仍有效',wm.reading(self.s))
        append(self.s,'角色已歸還書籤','assistant',[patch('角色已歸還書籤',op='confirm',ref=pending['id'],kind='situation',subject='shared')])
        self.assertEqual(len(wm.current_items(self.s)),1)
        self.assertIn('書籤已歸還使用者',wm.reading(self.s));self.assertNotIn('舊設定仍有效',wm.reading(self.s))

    def test_cross_owner_close_stays_pending_until_confirmed(self):
        ref=self.form();append(self.s,'我是一隻紙鶴',updates=[patch('我是一隻紙鶴',op='confirm',ref=ref)])
        append(self.s,'使用者已恢復人形','assistant',[patch('使用者已恢復人形',op='close',ref=ref)])
        self.assertEqual(len(wm.current_items(self.s)),2)
        pending=next(i for i in wm.current_items(self.s) if i.get('pending_close'))
        self.assertIn('藍色紙鶴',wm.reading(self.s))
        append(self.s,'我確實恢復人形',updates=[patch('恢復人形',op='confirm',ref=pending['id'])])
        self.assertFalse(wm.current_items(self.s))

    def test_character_can_correct_own_state(self):
        append(self.s,'角色穿越魔法變成白鴿','assistant',[patch('角色穿越魔法變成白鴿',subject='character')])
        ref=wm.current_items(self.s)[0]['id']
        append(self.s,'角色已經變回人類','assistant',[patch('角色已經變回人類',op='replace',ref=ref,subject='character')])
        self.assertEqual(len(wm.current_items(self.s)),1)
        self.assertEqual(wm.current_items(self.s)[0]['status'],'corrected')

    def test_ending_invalidates_pending_replacement_without_deleting_sources(self):
        ref=self.form();append(self.s,'我是一隻紙鶴',updates=[patch('我是一隻紙鶴',op='confirm',ref=ref)])
        append(self.s,'使用者變成白鴿','assistant',[patch('使用者變成白鴿',op='replace',ref=ref)])
        self.assertEqual(len(wm.current_items(self.s)),2)
        append(self.s,'我恢復人形',updates=[patch('恢復人形',op='close',ref=ref),patch('恢復人形')])
        self.assertEqual(len(wm.current_items(self.s)),1)
        self.assertNotIn('舊設定仍有效',wm.reading(self.s));self.assertNotIn('白鴿',wm.reading(self.s))
        self.assertEqual(len(self.s['story_memory']['working']['items']),3)

    def test_pending_replacement_does_not_alias_original_same_text(self):
        ref=self.form();append(self.s,'我是一隻紙鶴',updates=[patch('我是一隻紙鶴',op='confirm',ref=ref)])
        append(self.s,'角色重述：變成藍色紙鶴','assistant',[patch('變成藍色紙鶴',op='replace',ref=ref)])
        original=next(i for i in wm.current_items(self.s) if i['id']==ref)
        pending=next(i for i in wm.current_items(self.s) if i.get('pending_replaces')==ref)
        self.assertNotEqual(original['id'],pending['id'])
        self.assertEqual(original['status'],'shared')

    def test_open_loop_agreement_is_not_completion(self):
        append(self.s,'午餐後再一起泡茶','assistant',[patch('午餐後再一起泡茶',kind='open_loop',subject='shared',phase='proposed')])
        ref=wm.current_items(self.s)[0]['id']
        append(self.s,'好，午餐後泡茶',updates=[patch('午餐後泡茶',op='confirm',ref=ref,kind='open_loop',subject='shared')])
        self.assertEqual(wm.current_items(self.s)[0]['phase'],'proposed')
        self.assertIn('不代表已完成',wm.reading(self.s))

    def test_replay_no_duplicates_and_source_edit_invalidates(self):
        m=append(self.s,'我是紙鶴',updates=[patch('我是紙鶴')]);revision=self.s['story_memory']['working']['revision']
        wm.update(self.s,m,[patch('我是紙鶴')]);self.assertEqual(self.s['story_memory']['working']['revision'],revision)
        m['content']='已編輯';self.assertFalse(wm.current_items(self.s))

    def test_fresh_evidence_can_reintroduce_edited_away_claim(self):
        old=append(self.s,'我是紙鶴',updates=[patch('我是紙鶴')]);old['content']='編輯後不再有該說法'
        self.assertFalse(wm.current_items(self.s))
        append(self.s,'我是紙鶴',updates=[patch('我是紙鶴')])
        self.assertEqual(len(wm.current_items(self.s)),1)
        self.assertIn('紙鶴',wm.reading(self.s))

    def test_restart_and_scene_change_preserve_but_reset_forgets(self):
        self.form();self.s=json.loads(json.dumps(self.s));self.s['scene']['character_location']='花園'
        self.assertIn('藍色紙鶴',wm.reading(self.s));self.assertEqual(wm.reading(initial_state(STAMP)),'')

    def test_repeated_same_claim_does_not_add_duplicate_or_confirm(self):
        append(self.s,'我是紙鶴',updates=[patch('我是紙鶴')])
        append(self.s,'我是紙鶴',updates=[patch('我是紙鶴')])
        self.assertEqual(len(wm.current_items(self.s)),1)
        self.assertEqual(wm.current_items(self.s)[0]['status'],'reported')

    def test_formal_invitation_owns_progress_and_invalidates_pending_note(self):
        self.s['invitations']['inv']={'status':'proposed'}
        self.s['events']=[{'turn_id':'0','source':'user','kind':'invite','id':'inv','ref':'','evidence':'一起泡茶'}]
        append(self.s,'一起泡茶',updates=[patch('一起泡茶',kind='open_loop',subject='shared',phase='proposed')])
        self.assertEqual(wm.current_items(self.s)[0]['formal_ref'],'inv')
        self.s['invitations']['inv']['status']='accepted'
        self.assertIn('已接受未開始',wm.reading(self.s))
        self.s['invitations']['inv']['status']='completed'
        self.assertEqual(wm.reading(self.s),'')
        self.assertEqual(len(self.s['story_memory']['working']['items']),1)

    def test_negated_ending_cannot_close_shared_state(self):
        ref=self.form();append(self.s,'我是一隻紙鶴',updates=[patch('我是一隻紙鶴',op='confirm',ref=ref)])
        append(self.s,'我沒有恢復人形',updates=[patch('沒有恢復人形',op='close',ref=ref,phase='negated')])
        self.assertIn('藍色紙鶴',wm.reading(self.s))

    def test_canonical_statement_is_not_a_fabricated_literal_quote(self):
        u=patch('我已恢復人形');u['text']='使用者目前已恢復人形'
        append(self.s,'我已恢復人形',updates=[u])
        self.assertIn('使用者目前已恢復人形',wm.reading(self.s))
        self.assertEqual(wm.current_items(self.s)[0]['sources'][0]['quote'],'我已恢復人形')

    def test_unresolved_person_pronouns_are_rejected_not_rewritten(self):
        u=patch('我已恢復人形');u['text']='我已恢復人形'
        append(self.s,'我已恢復人形',updates=[u])
        self.assertFalse(wm.current_items(self.s))

    def test_literal_pronouns_inside_book_title_are_not_rewritten(self):
        u=patch('我喜歡《你的名字》');u['text']='使用者喜歡《你的名字》'
        append(self.s,'我喜歡《你的名字》',updates=[u])
        self.assertIn('使用者喜歡《你的名字》',wm.reading(self.s))

    def test_correction_can_correct_subject_but_confirmation_cannot(self):
        append(self.s,'角色是紙鶴',updates=[patch('角色是紙鶴',subject='character')])
        ref=wm.current_items(self.s)[0]['id']
        append(self.s,'更正，使用者才是紙鶴',updates=[patch('使用者才是紙鶴',op='replace',ref=ref,subject='user')])
        self.assertEqual(wm.current_items(self.s)[0]['subject'],'user')
        self.assertEqual(wm.current_items(self.s)[0]['status'],'corrected')

    def test_old_quote_version_is_retired_not_silently_reinterpreted(self):
        self.form();old=deepcopy(self.s['story_memory']['working']['items'])
        self.s['story_memory']['working']['version']='working-v1'
        wm.ensure(self.s)
        self.assertFalse(wm.current_items(self.s))
        self.assertEqual(self.s['story_memory']['working']['retired_items'],old)

    def test_no_world_or_policy_writes(self):
        before={k:deepcopy(self.s[k]) for k in ['scene','appearance','user_appearance','adult_policy','relationship']}
        self.form()
        self.assertEqual(before,{k:self.s[k] for k in before})

    def test_migration_uses_valid_annotations_only_never_confirms(self):
        self.s['history']=[{'role':'user','content':'我喝茶','turn_id':'old','at':STAMP}]
        annotate(self.s['history'][0],observation(story_events=[dict(actor='user',target='none',action='喝茶',stage='completed',evidence='我喝茶')]))
        wm.bootstrap(self.s);self.assertFalse(wm.current_items(self.s))
        legacy=deepcopy(self.s['story_memory']['working']['legacy_claims'])
        self.assertEqual(legacy[0]['action'],'喝茶')
        wm.bootstrap(self.s);self.assertEqual(self.s['story_memory']['working']['legacy_claims'],legacy)

    def test_ref_key_changes_cache_but_numeric_parameters_do_not(self):
        self.form();key=PyramidRunner.observation_key(self.s,'你好')
        self.s['relationship']['trust']=5;self.assertEqual(key,PyramidRunner.observation_key(self.s,'你好'))
        ref=wm.current_items(self.s)[0]['id'];append(self.s,'我是一隻紙鶴',updates=[patch('我是一隻紙鶴',op='confirm',ref=ref)])
        self.assertNotEqual(key,PyramidRunner.observation_key(self.s,'你好'))


class ProjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_notes_reach_single_packet_without_ids(self):
        s=initial_state(STAMP);append(s,'我是一隻紙鶴',updates=[patch('我是一隻紙鶴')])
        d=PyramidCore().decide(s,[],observation(),DecisionTrace());d['information_scope']='general'
        msgs=await PyramidRunner(FakeLLM([])).prepare_writer(s,d,'規則\n',DecisionTrace())
        c=json.loads(msgs[0]['content'].split('\n',1)[1])
        self.assertIn('紙鶴',c['story_context'])
        self.assertFalse(any(m['content'].startswith('【系統提供的當前互動記憶，') for m in msgs))
        self.assertNotIn(wm.current_items(s)[0]['id'],str(msgs))
        self.assertEqual(msgs[-1]['role'],'user')

    async def test_recent_window_is_bounded_contiguous_tail(self):
        s=initial_state(STAMP)
        for i in range(10):append(s,'原文'+str(i),'user' if i%2==0 else 'assistant')
        append(s,'你好')
        d=PyramidCore().decide(s,[],observation(),DecisionTrace());d['information_scope']='general'
        m=await PyramidRunner(FakeLLM([])).prepare_writer(s,d,'規則\n',DecisionTrace())
        self.assertEqual([x['content'] for x in m[1:]],[h['content'] for h in s['history'][-7:]])

    @mock_capacity("companion.context_plan.CAPACITY",4096)
    @mock_capacity("companion.pyramid_runner.CAPACITY",4096)
    @mock_capacity("companion.services.CONTEXT_CAPACITY",4096)
    async def test_required_note_overflow_does_not_silently_drop(self):
        class Tight(FakeLLM):
            async def count_context(self,msg):return 4000 if any(m['content'].startswith('【系統提供的當前互動記憶，') for m in msg) else 500
        s=initial_state(STAMP);append(s,'我是紙鶴',updates=[patch('我是紙鶴')])
        d=PyramidCore().decide(s,[],observation(),DecisionTrace())
        messages=await PyramidRunner(Tight([])).prepare_writer(s,d,'規則\n',DecisionTrace())
        self.assertIn('紙鶴',str(messages))
        self.assertIn('紙鶴',wm.reading(s))

    async def test_existing_three_calls_are_kept_and_snapshot_contains_notes(self):
        s=initial_state(STAMP);append(s,'我是紙鶴',updates=[patch('我是紙鶴')])
        llm=FakeLLM([json.dumps(observation()),'你好',json.dumps(observation())])
        await PyramidRunner(llm).run(s,TurnInput('你好','new',True))
        self.assertEqual(len(llm.calls),3)
        self.assertIn('working_memory',json.loads(llm.calls[0][1]['content'])['snapshot'])

    async def test_observer_refs_are_closed_to_existing_memory_ids(self):
        class Runner(PyramidRunner):
            async def call(self,trace,layer,messages,max_tokens,schema=None,temperature=0,**kwargs):
                if schema is not None:
                    allowed=schema['properties']['working_updates']['items']['properties']['ref']['enum']
                    self.allowed_refs=allowed
                return json.dumps(observation())
        s=initial_state(STAMP);append(s,'我是紙鶴',updates=[patch('我是紙鶴')])
        runner=Runner(FakeLLM([]));await runner.observe(s,'你好','user',DecisionTrace())
        self.assertEqual(runner.allowed_refs,['',wm.current_items(s)[0]['id']])
        await runner.observe(initial_state(STAMP),'你好','user',DecisionTrace())
        self.assertEqual(runner.allowed_refs,[''])
