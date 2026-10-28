from unittest.mock import patch as mock_capacity
"""Story reading, capacity and idle maintenance using neutral synthetic chats."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import json
import unittest
from unittest.mock import AsyncMock
import test_pyramid_telegram as fixtures
from test_pyramid import FakeLLM, observation, STAMP
from companion.pyramid import initial_state, DecisionTrace, TurnInput, PyramidCore
from companion.pyramid_runner import PyramidRunner
from companion.story_core import StoryCore


def history():
    words=['明天我要面試','祝你順利','等結束再說結果','好，我會記得你提過面試',
           '下午想去花園','還沒有決定是否一起去','我剛才說的只是提議','知道了，還沒約好',
           '今天讀了一本書','哪一本？','一本植物圖鑑','聽起來有趣','我還沒讀完','慢慢讀吧']
    return [{'role':'user' if i%2==0 else 'assistant','content':text,'at':STAMP}
            for i,text in enumerate(words)]


class StoryTests(unittest.IsolatedAsyncioTestCase):
    def test_complete_exchanges_and_attribution_without_inferred_actions(self):
        state=initial_state(STAMP);state['history']=history()
        before=deepcopy(state)
        entries=StoryCore().continuity(state,8)
        self.assertEqual([r['source_indices'] for r in entries],[[4,5],[6,7]])
        self.assertIn('使用者說：我剛才說的只是提議',entries[-1]['text'])
        self.assertIn('角色說：知道了，還沒約好',entries[-1]['text'])
        # If recent starts at an assistant, don't also recap its user half.
        self.assertEqual(StoryCore().continuity(state,7)[-1]['source_indices'],[4,5])
        self.assertEqual(state,before)

    async def test_greeting_gets_bridge_without_duplicate_recent_or_internal_ids(self):
        state=initial_state(STAMP);state['history']=history()+[{'role':'user','content':'你好嗎？','at':STAMP}]
        d=PyramidCore().decide(state,[],observation(),DecisionTrace());d['information_scope']='current_state'
        trace=DecisionTrace()
        messages=await PyramidRunner(FakeLLM([])).prepare_writer(state,d,'規則\n',trace)
        contract=json.loads(messages[0]['content'].split('\n',1)[1])
        story=contract.get('story_context','')
        self.assertIn('一本植物圖鑑','\n'.join(m['content'] for m in messages))
        self.assertNotIn('我還沒讀完',story)
        self.assertNotIn('source_index',story)
        self.assertNotIn('past_memory',contract)
        self.assertEqual([m['content'] for m in messages[1:]],[h['content'] for h in state['history'][-7:]])
        self.assertTrue(any(n['rule']=='story.reading' and n['output']['text']==story for n in trace.nodes))

    async def test_chat_does_not_wait_for_archival_generation(self):
        state=initial_state(STAMP);state['history']=history()
        llm=FakeLLM([json.dumps(observation()),'你好。',json.dumps(observation())])
        result=await PyramidRunner(llm).run(state,TurnInput('你好','story-chat',True))
        self.assertEqual(len(llm.calls),3)
        self.assertEqual(result.after['summary_through'],0)
        self.assertFalse(any(n['rule']=='memory.coverage' for n in result.trace))
        self.assertEqual(result.reply,'你好。')

    @mock_capacity("companion.context_plan.CAPACITY",4096)
    @mock_capacity("companion.pyramid_runner.CAPACITY",4096)
    @mock_capacity("companion.services.CONTEXT_CAPACITY",4096)
    async def test_token_budget_omits_whole_exchange_without_cutting_qualifiers(self):
        class Counter(FakeLLM):
            async def count_context(self,messages):
                if messages[0]['role']!='system':return 20+sum(len(m['content']) for m in messages)
                contract=json.loads(messages[0]['content'].split('\n',1)[1])
                return 600+len(contract.get('story_context',''))+sum(len(m['content']) for m in messages[1:])
        state=initial_state(STAMP);state['history']=history()
        state['history'][10]['content']='尚未答應。'+'來源很長。'*800
        d=PyramidCore().decide(state,[],observation(),DecisionTrace());d['information_scope']='interaction'
        trace=DecisionTrace();original=deepcopy(state['history'])
        messages=await PyramidRunner(Counter([])).prepare_writer(state,d,'規則\n',trace)
        contract=json.loads(messages[0]['content'].split('\n',1)[1])
        self.assertNotIn('來源很長',contract.get('story_context',''))
        node=next(n for n in trace.nodes if n['rule']=='story.prepared')
        capacity=next(n['output'] for n in trace.nodes if n['rule']=='context.prepared')
        self.assertLessEqual(capacity['input_tokens'],3264)
        packet=next(n for n in trace.nodes if n['rule']=='memory.packet')
        self.assertTrue(packet['inputs']['omitted_units'] or node['output']['uncovered_exchanges']>0)
        self.assertEqual(state['history'],original)

    async def test_one_idle_batch_does_not_archive_entire_backlog(self):
        state=initial_state(STAMP);state['history']=history()*4
        llm=FakeLLM([json.dumps({'notes':[]})])
        await PyramidRunner(llm).compact(state,DecisionTrace(),max_batches=1,recall=False)
        self.assertEqual(state['summary_through'],2)
        self.assertEqual(len(llm.calls),1)


class IdleStoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.TelegramPyramidTests.asyncSetUp(self)
        self.now=datetime.fromisoformat(STAMP)+timedelta(minutes=5)
        self.app.now=lambda:self.now
        state=self.app.pyramid.load();state['history']=history();state['summary_through']=0
        self.app.pyramid.save_clock(state)
        self.llm.healthy=AsyncMock(return_value=True)

    asyncTearDown=fixtures.TelegramPyramidTests.asyncTearDown

    async def test_idle_commits_one_batch_and_cools_down(self):
        self.llm.results=iter([json.dumps({'notes':[]})])
        await self.app.pyramid.maintain_story()
        self.assertEqual(self.app.pyramid.load()['summary_through'],2)
        await self.app.pyramid.maintain_story()
        self.assertEqual(len(self.llm.calls),1)

    async def test_unloaded_or_new_message_never_starts_generation(self):
        self.llm.healthy.return_value=False
        await self.app.pyramid.maintain_story()
        self.assertFalse(self.llm.calls)
        self.llm.healthy.return_value=True;self.app.inbox_event.set()
        await self.app.pyramid.maintain_story()
        self.assertFalse(self.llm.calls)

    async def test_cancelled_background_does_not_advance_coverage(self):
        entered=asyncio.Event()
        async def generate(*args,**kwargs):
            entered.set();await asyncio.Event().wait()
        self.llm.generate=generate
        task=asyncio.create_task(self.app.pyramid.maintain_story())
        await asyncio.wait_for(entered.wait(),1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(self.app.pyramid.load()['summary_through'],0)

    async def test_changed_snapshot_cannot_be_overwritten(self):
        async def generate(messages,on_request=None,**kwargs):
            newer=self.app.pyramid.load();newer['activity']='讀書'
            self.app.pyramid.save_clock(newer)
            if on_request:on_request(messages)
            return json.dumps({'notes':[]})
        self.llm.generate=generate
        await self.app.pyramid.maintain_story()
        state=self.app.pyramid.load()
        self.assertEqual(state['activity'],'讀書')
        self.assertEqual(state['summary_through'],0)
