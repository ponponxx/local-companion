import asyncio
import json
from copy import deepcopy
import unittest
from unittest.mock import AsyncMock
import test_pyramid_telegram as fixtures
from companion.pyramid import DecisionTrace, initial_state, TurnInput
from companion.pyramid_runner import PyramidRunner
from companion.telegram_debug import DebugReporter, describe
from companion.services import ServiceError
from test_pyramid import FakeLLM, observation, event, STAMP


class WorkingDebugTests(unittest.TestCase):
    def test_conflict_and_pending_are_not_reported_as_committed_changes(self):
        conflict={'rule':'working.updated','output':{'accepted':[{
            'op':'replace','text':'使用者不是紙鶴','status':'shared',
            'current_text':'使用者是紙鶴','conflict_recorded':True}]}}
        rendered=describe(conflict)[1]
        self.assertIn('現況未覆寫',rendered);self.assertIn('維持：使用者是紙鶴',rendered)
        pending={'rule':'working.updated','output':{'accepted':[{
            'op':'replace','text':'書籤由使用者保管','status':'reported'}]}}
        self.assertIn('待另一方確認',describe(pending)[1])


class DebugTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.TelegramPyramidTests.asyncSetUp
    asyncTearDown=fixtures.TelegramPyramidTests.asyncTearDown
    text=fixtures.TelegramPyramidTests.text
    results=fixtures.TelegramPyramidTests.results
    obs=fixtures.TelegramPyramidTests.obs
    state=fixtures.TelegramPyramidTests.state

    async def test_enabled_trace_is_ordered_and_excluded_from_memory_and_prompt(self):
        await self.text('/debug on',1)
        self.assertFalse(self.llm.calls)
        self.tg.send.reset_mock()
        self.results(self.obs(event('question',evidence='茶')), '可以喝紅茶。', self.obs())
        await self.text('茶',2)
        sends=[c.args[1] for c in self.tg.send.await_args_list]
        role_index=next(i for i,s in enumerate(sends) if s.endswith('\n\n可以喝紅茶。'))
        before='\n'.join(sends[:role_index]);after='\n'.join(sends[role_index+1:])
        self.assertIn('第一次判讀：使用者',before)
        self.assertIn('比較所有候選行為',before)
        self.assertIn('★ 選中',before)
        self.assertIn('回文分析已排程',after)
        self.assertIn('本輪結束',after)
        self.assertEqual(len(self.llm.calls),3)
        self.assertEqual([h['content'] for h in self.state()['history']],['茶','可以喝紅茶。'])
        self.assertNotIn('系統 Debug',self.store.get('last_prompt'))
        self.assertTrue(all(s.startswith('【系統 Debug') for i,s in enumerate(sends) if i!=role_index))

    async def test_off_and_last_are_model_free(self):
        await self.text('/debug off',1)
        self.results(self.obs(),'你好。',self.obs())
        self.tg.send.reset_mock()
        await self.text('你好',2)
        self.tg.send.assert_awaited_once()
        self.assertTrue(self.tg.send.await_args.args[1].startswith('【目前狀態｜系統紀錄】'))
        self.assertTrue(self.tg.send.await_args.args[1].endswith('\n\n你好。'))
        count=len(self.llm.calls);state=deepcopy(self.state())
        await self.text('/debug last',3)
        self.assertEqual(len(self.llm.calls),count)
        self.assertEqual(state,self.state())
        self.assertIn('上一輪重看',self.tg.send.await_args.args[1])

    async def test_debug_delivery_failure_does_not_prevent_character_reply(self):
        self.store.set('telegram_debug_enabled','true')
        async def send(chat,text):
            if text.startswith('【系統 Debug'):raise ServiceError('debug transport error')
        self.tg.send.side_effect=send
        self.results(self.obs(),'你好。',self.obs())
        await self.text('你好',1)
        self.assertEqual(self.state()['history'][-1]['content'],'你好。')
        self.assertEqual(self.store.db.execute('select status from pyramid_turns').fetchone()[0],'done')

    async def test_failed_observer_reports_stopped_without_internal_prose(self):
        self.store.set('telegram_debug_enabled','true')
        self.results('not json')
        with self.assertRaises(ValueError):await self.text('你好',1)
        output='\n'.join(c.args[1] for c in self.tg.send.await_args_list)
        self.assertIn('本輪在角色回覆送達前停止',output)
        self.assertNotIn('not json',output)
        self.assertNotIn('Expecting value',output)

    async def test_long_debug_messages_remain_labelled_and_bounded(self):
        tg=AsyncMock();r=DebugReporter(tg,42,'test')
        r.queue.put_nowait('說明'*5000)
        await r.close()
        self.assertGreater(tg.send.await_count,1)
        for call in tg.send.await_args_list:
            self.assertTrue(call.args[1].startswith('【系統 Debug'))
            self.assertLess(len(call.args[1]),3600)
        self.assertTrue(r.task.done())

    async def test_formatter_renders_actual_scores_without_echoing_prompts(self):
        runner=PyramidRunner(FakeLLM([json.dumps(observation([event('invite',value='跳舞',field='social',evidence='跳舞')]))]))
        result=await runner.run(initial_state(STAMP),TurnInput('跳舞','test'))
        texts=[describe(n) for n in result.trace]
        self.assertTrue(any(t and '來源：邀請事件' in t[1] for t in texts))
        self.assertTrue(any(t and '跳舞' in t[1] and '勝任' in t[1] for t in texts))
        self.assertTrue(any(t and '先比較資格與適切分' in t[1] for t in texts))
        node={'rule':'model.request','layer':2,'inputs':{'structured':True,'messages':[{'content':'private source'}]},'output':{'raw':'private model output'}}
        self.assertNotIn('private',str(describe(node)))

    async def test_invalid_debug_argument_does_not_enable(self):
        with self.assertRaises(ValueError):await self.text('/debug surprise',1)
        self.assertNotEqual(self.store.get('telegram_debug_enabled'),'true')
        self.assertFalse(self.llm.calls)

    async def test_timing_is_saved_and_replay_does_not_recalculate(self):
        from companion.telegram_debug import DebugTrace
        tg=AsyncMock();reporter=DebugReporter(tg,42,'timing')
        ticks=iter([100.,100.022,125.])
        trace=DebugTrace(reporter,clock=lambda:next(ticks))
        trace.add(2,'model.failed',{}, {'reason_code':'context_capacity'},'test',seconds=.022)
        trace.add(1,'memory.context_budget',{'chars':700},{'chars':350},'test')
        await reporter.close()
        self.assertEqual(trace.nodes[0]['elapsed_seconds'],.022)
        self.assertEqual(trace.nodes[1]['interval_seconds'],24.978)
        self.assertEqual(trace.nodes[1]['elapsed_seconds'],25.)
        output='\n'.join(c.args[1] for c in tg.send.await_args_list)
        self.assertIn('實測耗時 0.022 秒',output)
        self.assertIn('本輪累計 25.000 秒',output)
        self.assertIn('節點間隔 24.978 秒',output)
        replay=DebugReporter(tg,42,'replay')
        for node in json.loads(json.dumps(trace.nodes)):replay.feed(node)
        await replay.close()
        self.assertIn('本輪累計 25.000 秒',tg.send.await_args.args[1])

    async def test_old_trace_marks_missing_timing_instead_of_inventing_it(self):
        tg=AsyncMock();r=DebugReporter(tg,42,'old')
        r.feed({'rule':'memory.context_budget','inputs':{'chars':700},'output':{'chars':350}})
        await r.close()
        self.assertIn('耗時未記錄',tg.send.await_args.args[1])
        self.assertNotIn('累計',tg.send.await_args.args[1])

    async def test_retrieval_has_own_measured_time(self):
        from companion.pyramid_memory import MemorySearch
        trace=DecisionTrace()
        await MemorySearch().select(initial_state(STAMP),'茶',trace)
        self.assertIn('seconds',trace.nodes[-1])
        self.assertGreaterEqual(trace.nodes[-1]['seconds'],0)

class FlowDebugTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.TelegramPyramidTests.asyncSetUp
    asyncTearDown=fixtures.TelegramPyramidTests.asyncTearDown
    text=fixtures.TelegramPyramidTests.text
    results=fixtures.TelegramPyramidTests.results
    obs=fixtures.TelegramPyramidTests.obs
    state=fixtures.TelegramPyramidTests.state

    async def test_flow_shows_actual_prompt_and_io_without_candidate_math(self):
        await self.text('/debug flow',801)
        self.assertFalse(self.llm.calls)
        self.tg.send.reset_mock()
        self.results(self.obs(event('question',evidence='茶')), '可以喝紅茶。', self.obs())
        await self.text('茶',802)
        output='\n'.join(c.args[1] for c in self.tg.send.await_args_list)
        self.assertIn('User Observer｜Input / Output',output)
        self.assertIn('Writer｜Input / Output',output)
        self.assertIn('實際送出的完整Prompt',output)
        self.assertIn('故事核',output)
        self.assertIn('決策核提供的定案',output)
        self.assertIn('編輯核選定來源',output)
        self.assertNotIn('★ 選中',output)
        self.assertNotIn('收益分子',output)
        self.assertEqual(len(self.llm.calls),3)
        self.assertEqual([h['content'] for h in self.state()['history']],['茶','可以喝紅茶。'])
        self.assertNotIn('Input / Output',self.store.get('last_prompt'))
        count=len(self.llm.calls)
        await self.text('/debug flow last',803)
        self.assertEqual(len(self.llm.calls),count)
        self.assertIn('Response Observer', '\n'.join(c.args[1] for c in self.tg.send.await_args_list))

    async def test_actual_idle_response_observer_sends_flow_to_original_chat(self):
        await self.text('/debug flow',811)
        self.results(self.obs(),'你好。',self.obs())
        await self.app.handle(812,{'chat':{'id':42},'text':'你好'})
        self.assertTrue(self.state()['_pending_responses'])
        self.llm.healthy=AsyncMock(return_value=True)
        self.tg.send.reset_mock()
        await self.app.pyramid.maintain_responses()
        output='\n'.join(c.args[1] for c in self.tg.send.await_args_list)
        self.assertIn('Response Observer｜Input / Output',output)
        self.assertTrue(all(c.args[0]==42 for c in self.tg.send.await_args_list))
        self.assertFalse(self.state()['_pending_responses'])

    async def test_flow_can_switch_back_or_off_without_models(self):
        await self.text('/debug flow',821)
        self.assertEqual(self.store.get('telegram_debug_mode'),'flow')
        await self.text('/debug on',822)
        self.assertEqual(self.store.get('telegram_debug_mode'),'detail')
        await self.text('/debug off',823)
        self.assertNotEqual(self.store.get('telegram_debug_enabled'),'true')
        self.assertFalse(self.llm.calls)

    async def test_flow_pages_keep_full_unicode_content_within_telegram_limit(self):
        from companion.telegram_debug import message_chunks
        original='測試😀'*2200
        chunks=list(message_chunks(original))
        self.assertEqual(''.join(chunks),original)
        self.assertTrue(all(len(x.encode('utf-16-le'))//2<=3200 for x in chunks))
        tg=AsyncMock();r=DebugReporter(tg,42,'unicode',mode='flow')
        r.feed({'layer':5,'rule':'model.request','inputs':{'messages':[{'role':'user','content':original}],'structured':False},'output':{'raw':'完成'}})
        await r.close()
        self.assertTrue(all(len(c.args[1].encode('utf-16-le'))//2<4096 for c in tg.send.await_args_list))
