"""Actor/phase boundaries and rebuildable episodes; neutral source fixtures."""
import json
import unittest
from copy import deepcopy
from companion.pyramid import initial_state, DecisionTrace, PyramidCore, TurnInput
from companion.pyramid_runner import PyramidRunner
from companion.story_core import StoryCore
from companion.story_events import annotate, claims
from test_pyramid import STAMP, FakeLLM, observation, event
import test_pyramid_telegram as fixtures


def claim(action, actor='user', stage='completed', target='none', evidence=None):
    return dict(actor=actor,target=target,stage=stage,action=action,evidence=evidence or action)


def row(text, role='user', **extra):
    return dict(role=role,content=text,at=STAMP,**extra)


class EpisodeTests(unittest.IsolatedAsyncioTestCase):
    def test_reported_actor_is_distinct_from_speaker_and_never_updates_world(self):
        state=initial_state(STAMP);message=row('妳把書放回架上了');state['history']=[message]
        original=deepcopy(state['scene'])
        StoryCore().record(state,message,observation(story_events=[claim('把書放回架上',actor='character')]))
        recorded=state['story_memory']['episodes'][0]['events'][0]
        self.assertEqual(recorded['speaker'],'user');self.assertEqual(recorded['actor'],'character')
        self.assertEqual(state['scene'],original)
        self.assertFalse(state['events'])

    def test_negation_hypothesis_and_plan_are_not_completed(self):
        for stage,phrase in [('negated','沒有泡茶'),('planned','打算泡茶'),('hypothetical','假如泡茶'),('proposed','要不要泡茶')]:
            state=initial_state(STAMP);message=row(phrase);state['history']=[message]
            StoryCore().record(state,message,observation(story_events=[claim(phrase,stage=stage)]))
            text=StoryCore().continuity(state,1)[0]['text']
            self.assertNotIn('描述已完成',text)
            self.assertEqual(state['story_memory']['episodes'][0]['events'][0]['stage'],stage)

    def test_nonliteral_action_and_invented_evidence_rejected(self):
        message=row('我泡茶了')
        accepted,rejected=annotate(message,observation(story_events=[claim('泡咖啡',evidence='我泡茶了'),claim('已經散步')]))
        self.assertEqual(accepted,[]);self.assertEqual(rejected,2)

    def test_character_cannot_create_user_action_claim(self):
        message=row('你泡茶了','assistant')
        accepted,rejected=annotate(message,observation(story_events=[claim('泡茶')]))
        self.assertFalse(accepted);self.assertEqual(rejected,1)

    def test_source_edit_and_delete_cannot_resurrect_cached_episode(self):
        state=initial_state(STAMP);message=row('我泡茶了');state['history']=[message]
        StoryCore().record(state,message,observation(story_events=[claim('泡茶')]))
        message['content']='我沒有泡茶'
        self.assertFalse(claims(message))
        self.assertFalse(StoryCore().episodes(state)[0]['events'])
        state['history']=[]
        self.assertEqual(StoryCore().episodes(state),[])

    def test_time_or_scene_revision_invalidates_extraction(self):
        for key,value in [('at','2026-10-17T10:00:00+08:00'),('context',{'scene':{'location':'花園'}})]:
            message=row('我泡茶了')
            annotate(message,observation(story_events=[claim('泡茶')]))
            message[key]=value
            self.assertFalse(claims(message))

    def test_retrieved_fragment_expands_both_sides_and_deduplicates_episode(self):
        state=initial_state(STAMP)
        state['history']=[row('一起散步嗎'),row('今天不去','assistant'),row('那改聊書吧'),row('好啊','assistant')]
        items=[dict(source_index=0,kind='raw',actor='user',text='散步'),
               dict(source_index=1,kind='raw',actor='character',text='不去')]
        expanded=StoryCore().expand_memory(state,items,{2,3})
        self.assertEqual(len(expanded),1)
        self.assertIn('一起散步嗎',expanded[0]['text'])
        self.assertIn('今天不去',expanded[0]['text'])
        self.assertFalse(StoryCore().expand_memory(state,items,{0,1,2,3}))

    def test_same_explicit_topic_and_scene_merge_but_topic_change_splits(self):
        state=initial_state(STAMP)
        for text,topic in [('介紹植物圖鑑','植物圖鑑'),('看完第一章','植物圖鑑'),('明天面試','面試')]:
            message=row(text,context={'scene':{'character_location':'書店'}})
            annotate(message,observation(topic=topic,story_events=[]))
            state['history'].extend([message,row('知道了','assistant')])
        episodes=StoryCore().episodes(state)
        self.assertEqual([e['source_indices'] for e in episodes],[[0,1,2,3],[4,5]])
        self.assertEqual(StoryCore().episodes(state,3)[0]['source_indices'],[0,1])

    def test_invitation_progress_reads_authoritative_status_not_time_or_prose(self):
        state=initial_state(STAMP);core=PyramidCore();trace=DecisionTrace()
        invitation=event('invite',value='散步',field='social',evidence='一起散步嗎')
        accepted=core.apply(state,[invitation],'user','一起散步嗎','invite',trace)
        identifier=accepted[0]['id']
        state['history']=[row('一起散步嗎',turn_id='invite'),row('我還沒決定','assistant',turn_id='invite')]
        text=StoryCore().continuity(state,2)[0]['text']
        self.assertIn('僅提出，未同意',text)
        core.apply(state,[event('accept',actor='character',ref=identifier,value='晚飯後',evidence='晚飯後再去')],
                   'assistant','晚飯後再去','later',trace)
        text=StoryCore().continuity(state,2)[0]['text']
        self.assertIn('已同意，未開始',text);self.assertIn('晚飯後',text)
        self.assertNotIn('已完成',text)

    async def test_same_observer_call_records_tea_actor_and_writer_reads_it(self):
        state=initial_state(STAMP)
        user=observation(topic='喝茶',story_events=[claim('泡了紅茶')])
        llm=FakeLLM([json.dumps(user),'你好。',json.dumps(observation())])
        result=await PyramidRunner(llm).run(state,TurnInput('我剛才泡了紅茶','tea',True))
        self.assertEqual(len(llm.calls),3)
        contract=json.loads(llm.calls[1][0]['content'].split('\n',1)[1])
        self.assertEqual(llm.calls[1][-1],{'role':'user','content':'我剛才泡了紅茶'})
        self.assertNotIn('使用者原話',contract.get('story_context',''))  # Raw already appears as recent message.
        self.assertTrue(result.after['story_memory']['episodes'])

    async def test_unselected_memory_does_not_leak_from_intermediate_contract(self):
        state=initial_state(STAMP);state['history']=[row('你好嗎')]
        state['memory']['selection']={'items':[dict(id='old',source_index=99,actor='user',at=STAMP,
                                                  stage='unknown',text='不該放入的舊記憶')]}
        decision=PyramidCore().decide(state,[],observation(),DecisionTrace())
        decision['information_scope']='current_state'
        messages=await PyramidRunner(FakeLLM([])).prepare_writer(state,decision,'規則\n',DecisionTrace())
        self.assertNotIn('不該放入的舊記憶',json.dumps(messages,ensure_ascii=False))


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):await fixtures.TelegramPyramidTests.asyncSetUp(self)
    asyncTearDown=fixtures.TelegramPyramidTests.asyncTearDown

    async def test_delivered_reply_recovery_keeps_story_annotations(self):
        state=self.app.pyramid.load()
        state['history']=[row('整理得如何','user',turn_id='one'),row('整理好書架了','assistant',turn_id='one')]
        state['_pending_responses']=[dict(turn_id='one',reply='整理好書架了',at=STAMP)]
        self.app.pyramid.save_clock(state)
        self.llm.results=iter([json.dumps(observation(story_events=[claim('整理好書架',actor='character')]))])
        runner=PyramidRunner(self.llm,self.app.pyramid.core,self.app.pyramid.search)
        await self.app.pyramid.recover_response(state,runner,DecisionTrace())
        restored=self.app.pyramid.load()
        self.assertEqual(claims(restored['history'][-1])[0]['actor'],'character')
        self.assertFalse(restored['_pending_responses'])

    async def test_reset_clears_but_forget_retains_unrelated_story_and_diagnostics(self):
        s=self.app.pyramid.load();self.app.pyramid.save_clock(s)
        self.store.set('last_story_background_trace','old trace')
        memory=self.store.remember('我喜歡茶');self.store.add_message('user','今天晴天')
        self.store.forget(memory)
        self.assertEqual(self.store.get('last_story_background_trace'),'old trace')
        self.assertTrue(self.store.get('pyramid_state'))
        self.assertEqual(self.store.recent()[-1]['content'],'今天晴天')
        self.store.reset()
        self.assertFalse(self.store.get('last_story_background_trace'))
        self.assertFalse(self.store.get('pyramid_state'))
