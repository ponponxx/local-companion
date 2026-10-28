from unittest.mock import patch as mock_capacity
"""Neutral source-linked examples, never private chat replay."""
from copy import deepcopy
from datetime import datetime
import unittest
from companion.pyramid import initial_state,DecisionTrace
from companion.story_events import annotate
from companion.story_core import StoryCore
from companion.scene_topic_index import build,MODE,query_scene
from companion.pyramid_memory import MemorySearch

AT='2026-10-21T09:00:00+08:00'
def state():
 s=initial_state(AT);s['memory_index_mode']=MODE;s['scene'].update(location='花園',character_location='花園',user_location='',status='active')
 return s

def visit(s,id,where,at=AT,phase='arrived',before=''):
 s['scene_history'].append({'id':id,'phase':phase,'actor':'character','at':at,'source':'synthetic','history_index':len(s['history']),
  'before':{'character_location':before,'location':before},'after':{'character_location':where,'location':where}})
 s['scene'].update(location=where,character_location=where)

def turn(s,text,topic='工作近況',where='花園',at='2026-10-21T09:01:00+08:00',actor='user',reply='我聽到了。'):
 for role,content in [('user',text),('assistant',reply)]:
  row={'role':role,'content':content,'at':at,'turn_id':'synth-'+str(len(s['history'])),
       'context':{'scene':{'character_location':where,'location':where,'user_location':'','status':'active'}}}
  s['history'].append(row)
  events=[dict(actor=actor,target='none',stage='completed',action=text,evidence=text)] if role=='user' and actor else []
  annotate(row,{'topic':topic,'story_events':events})
 return s['history'][-2]

class SceneTopicIndexTests(unittest.IsolatedAsyncioTestCase):
 def test_distinct_visits_to_same_place(self):
  s=state();visit(s,'v1','石板街');turn(s,'主管要我重寫報告',where='石板街')
  visit(s,'v2','茶館','2026-10-21T10:00:00+08:00');turn(s,'我想先休息',topic='休息',where='茶館',at='2026-10-21T10:01:00+08:00')
  visit(s,'v3','石板街','2026-10-21T11:00:00+08:00');turn(s,'我剛看到新書',topic='新書',where='石板街',at='2026-10-21T11:01:00+08:00')
  i=build(s);self.assertEqual([i['messages'][x]['visit'] for x in [0,2,4]],['v1','v2','v3'])
  q=query_scene(i,'上次在石板街說了什麼',s);self.assertEqual(q['visits'],['v1']);self.assertEqual(q['sources'],[0,1])
 def test_pending_and_relative_move_are_not_visits(self):
  s=state();visit(s,'v1','花園',phase='initialized');visit(s,'pending','茶館','2026-10-21T10:00:00+08:00',phase='announced')
  visit(s,'relative','花園','2026-10-21T10:00:00+08:00',phase='moved',before='花園');turn(s,'我靠近一步',topic='靠近',at='2026-10-21T10:01:00+08:00')
  self.assertEqual([v['id'] for v in build(s)['visits']],['v1'])
 def test_unknown_user_location_and_missing_legacy_visit_stay_unknown(self):
  s=state();turn(s,'昨天主管責備了我',where='石板街');i=build(s)
  self.assertEqual(i['messages'][0]['visit'],'');self.assertEqual(i['messages'][0]['user_location'],'')
  reading=StoryCore().entry(s,StoryCore().episodes(s)[0])['text']
  self.assertIn('使用者位置未知',reading);self.assertIn('不代表所談事件在此發生',reading);self.assertNotIn('使用者在石板街',reading)
 def test_cross_midnight_does_not_invent_another_arrival(self):
  s=state();visit(s,'v1','花園');turn(s,'晚上先休息',at='2026-10-21T23:59:00+08:00');turn(s,'過午夜了',at='2026-10-22T00:01:00+08:00')
  self.assertEqual(len(build(s)['visits']),1);self.assertEqual(build(s)['messages'][2]['visit'],'v1')
 def test_same_discussion_continues_across_two_scenes(self):
  s=state();visit(s,'v1','花園');turn(s,'主管希望我補一頁報告')
  visit(s,'v2','茶館','2026-10-21T10:00:00+08:00');turn(s,'我打算明天再談這份報告',where='茶館',at='2026-10-21T10:01:00+08:00')
  i=build(s);self.assertEqual(len(i['threads']),1);self.assertEqual(i['threads'][0]['visits'],['v1','v2']);self.assertEqual(len(StoryCore().episodes(s)),2)
 def test_topic_and_actor_changes_never_merge(self):
  s=state();turn(s,'主管責備了我');turn(s,'主管也責備了妳',actor='character');turn(s,'我想買茶',topic='挑茶')
  self.assertEqual(len(build(s)['threads']),3)
 def test_non_adjacent_vague_reference_with_two_candidates_is_not_guessed(self):
  s=state();turn(s,'第一次主管談報告');turn(s,'我想買茶',topic='挑茶');turn(s,'第二次主管談報告');turn(s,'我去買書',topic='買書');turn(s,'上次那件事後來再談')
  i=build(s);self.assertEqual(len(i['threads']),5);self.assertEqual(i['messages'][8]['thread_reason'],'ambiguous_reference_kept_separate')
 def test_unique_explicit_reference_can_reconnect(self):
  s=state();turn(s,'主管要我補報告');turn(s,'先談一杯茶',topic='喝茶');turn(s,'回到剛才那份報告')
  i=build(s);self.assertEqual(i['messages'][0]['thread'],i['messages'][4]['thread'])
 def test_edit_and_delete_invalidate_derived_links(self):
  s=state();m=turn(s,'主管要我補報告');self.assertTrue(build(s)['threads']);m['content']='今天沒有談工作'
  self.assertFalse(build(s)['threads']);s['history']=[];self.assertFalse(build(s)['messages'])
 def test_thread_expansion_preserves_both_sides_and_deduplicates(self):
  s=state();visit(s,'v1','花園');turn(s,'主管叫我改報告',reply='你先看看哪些地方要改。')
  visit(s,'v2','茶館','2026-10-21T10:00:00+08:00');turn(s,'我打算明天才改報告',where='茶館',at='2026-10-21T10:01:00+08:00')
  before=deepcopy(s);items=[{'source_index':0,'kind':'raw','actor':'user','text':'報告'}]
  expanded=StoryCore().expand_memory(s,items,set());self.assertEqual([i['source_indices'] for i in expanded],[[0,1],[2,3]])
  self.assertIn('角色在茶館',expanded[1]['text']);self.assertIn('明天才改報告',expanded[1]['text']);self.assertEqual(s,before)
 async def test_topic_search_not_polluted_by_incidental_character_location(self):
  s=state();turn(s,'主管要我修改月報',where='石板街');turn(s,'我想買巧克力',topic='零食',where='茶館')
  selection=await MemorySearch().select(s,'主管月報',budget_chars=1800)
  self.assertIn(0,[i['source_index'] for i in selection['items']]);self.assertFalse(selection['scene_query']['explicit'])
 async def test_explicit_scene_query_selects_transcript_without_content_keyword(self):
  s=state();visit(s,'v1','石板街');turn(s,'主管要我改報告',where='石板街');visit(s,'v2','茶館','2026-10-21T10:00:00+08:00');turn(s,'我今天想喝咖啡',topic='咖啡',where='茶館',at='2026-10-21T10:01:00+08:00')
  selection=await MemorySearch().select(s,'在石板街說了什麼',budget_chars=1800)
  self.assertTrue(selection['scene_query']['explicit']);self.assertTrue(selection['items']);self.assertTrue(all(i['source_index'] in [0,1] for i in selection['items']))
  expanded=StoryCore().expand_memory(s,selection['items'],set());self.assertTrue(all(set(i.get('source_indices',[]))<={0,1} for i in expanded))
 def test_existing_mode_is_unchanged_and_has_no_new_index(self):
  s=state();s.pop('memory_index_mode');m=turn(s,'主管要我改報告');StoryCore().record(s,m,{'topic':'工作近況','story_events':[]})
  self.assertNotIn('scene_topic_index',s['story_memory']);self.assertNotIn('聊天背景',StoryCore().continuity(s,2)[0]['text'])
 def test_closed_visit_is_not_reused_for_later_old_location_snapshot(self):
  s=state();visit(s,'v1','花園');visit(s,'closed','',at='2026-10-21T10:00:00+08:00',phase='closed',before='花園')
  turn(s,'晚些再談',at='2026-10-21T10:30:00+08:00');self.assertEqual(build(s)['messages'][0]['visit'],'')
 def test_move_utterance_retains_its_before_and_after_scene(self):
  s=state();visit(s,'v1','花園');visit(s,'v2','茶館',at='2026-10-21T10:00:00+08:00')
  m=turn(s,'我準備出發',at='2026-10-21T10:00:00+08:00');m['context']['scene_after']={'character_location':'茶館'}
  links=build(s)['messages'][0];self.assertEqual(links['visit'],'v1');self.assertEqual(links['character_location_after'],'茶館')
 async def test_event_date_is_not_limited_to_the_day_it_was_reported(self):
  s=state();turn(s,'昨天主管責備我，說月報缺頁。',at='2026-10-21T09:01:00+08:00')
  s['now']='2026-10-21T10:00:00+08:00'
  recalled=await MemorySearch().select(s,'昨天被主管責備是怎麼回事？',budget_chars=1800)
  self.assertIn(0,[i['source_index'] for i in recalled['items']])
  spoken=await MemorySearch().select(s,'昨天說過哪些話？',budget_chars=1800)
  self.assertNotIn(0,[i['source_index'] for i in spoken['items']])
 def test_relative_dates_are_anchored_to_original_speech_not_current_clock(self):
  s=state();visit(s,'v1','花園');turn(s,'明天早上十點面試，還沒開始。',topic='面試',at='2026-10-21T23:50:00+08:00')
  s['now']='2026-10-22T00:10:00+08:00';text=StoryCore().entry(s,StoryCore().episodes(s)[0])['text']
  self.assertIn('明天指2026-10-22',text);self.assertIn('還沒開始',text)
 @mock_capacity("companion.context_plan.CAPACITY",4096)
 @mock_capacity("companion.pyramid_runner.CAPACITY",4096)
 @mock_capacity("companion.services.CONTEXT_CAPACITY",4096)
 async def test_relevant_source_reaches_writer_before_unrelated_long_chatter(self):
  from companion.pyramid_runner import PyramidRunner
  from companion.pyramid import PyramidCore
  from test_pyramid import FakeLLM
  from scripts.scene_topic_cases import cases
  class Counter(FakeLLM):
   async def count_context(self,messages):return 350+sum(len(m['content']) for m in messages)
  s=cases(28)[0]['state'];q='主管要我改哪份月報？';await MemorySearch().select(s,q)
  s['history'].append({'role':'user','content':q,'at':s['now']})
  d=PyramidCore().decide(s,[],{'utterance':{'act':'question','purpose':'other'},'appraisals':[]},DecisionTrace());d.update(primary='answer',information_scope='past_events',content=q)
  trace=DecisionTrace();llm=Counter([])
  messages=await PyramidRunner(llm,clock=lambda:datetime.fromisoformat(s['now'])).prepare_writer(s,d,'規則\n',trace)
  import json
  body=json.loads(messages[0]['content'].split('\n',1)[1]);self.assertIn('藍色封面',body.get('story_context',''));self.assertIn('明天指2026-10-21',body['story_context'])
  self.assertEqual(messages[-1]['content'],q);self.assertLessEqual(await llm.count_context(messages),3264);self.assertFalse(llm.calls)
 def test_legacy_character_only_scene_snapshot_never_renders_empty_arrival(self):
  from companion.pyramid_memory import catalogue
  s=state();visit(s,'v1','石板街')
  docs=[d for d in catalogue(s) if d['kind']=='scene_event']
  self.assertIn('抵達石板街',docs[0]['text']);self.assertNotIn('切換到，',docs[0]['text'])
 async def test_enabled_index_reuses_existing_three_model_roles(self):
  import json
  from companion.pyramid_runner import PyramidRunner
  from companion.pyramid import TurnInput
  from test_pyramid import FakeLLM,observation
  s=state();llm=FakeLLM([json.dumps(observation()),'你好。',json.dumps(observation())])
  result=await PyramidRunner(llm).run(s,TurnInput('你好','index-call-count',True))
  self.assertEqual(len(llm.calls),3);self.assertTrue(any(n['rule']=='story.index' for n in result.trace));self.assertEqual(result.after['memory_index_mode'],MODE)
