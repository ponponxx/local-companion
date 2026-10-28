from unittest.mock import patch as mock_capacity
"""Bounded admission fallback; no dialogue/source deletion and no generation retry."""
import json,unittest
from copy import deepcopy
from companion.pyramid_runner import PyramidRunner,observer_output_reserve
from companion.pyramid import DecisionTrace,initial_state
from companion.services import ContextCapacityError,ServiceError
from test_pyramid import FakeLLM,observation,STAMP

class CapacityReliefTests(unittest.IsolatedAsyncioTestCase):
    @mock_capacity("companion.context_plan.CAPACITY",4096)
    @mock_capacity("companion.pyramid_runner.CAPACITY",4096)
    @mock_capacity("companion.services.CONTEXT_CAPACITY",4096)
    async def test_exact_thresholds_reserve_the_largest_safe_amount(self):
        self.assertEqual([observer_output_reserve(n) for n in [3008,3011,3061,3074,3173,3264,3265]],[1024,992,960,928,832,768,None])

    async def trial(self,tokens,finish='stop'):
        class Provider(FakeLLM):
            async def count_context(self,messages):return tokens
            async def generate(self,messages,**kwargs):
                self.reserve=kwargs['max_tokens']
                raw=await super().generate(messages,**kwargs)
                self.last_metrics['finish_reason']=finish
                return raw
        llm=Provider([json.dumps(observation())]);s=initial_state(STAMP)
        s['history']=[{'role':'user','content':'較早完整提問'},{'role':'assistant','content':'較早完整回答'}]
        before=deepcopy(s);trace=DecisionTrace()
        try:result=await PyramidRunner(llm).observe(s,'目前完整原文','user',trace)
        except Exception as exc:result=exc
        return result,llm,s,before,trace

    @mock_capacity("companion.context_plan.CAPACITY",4096)
    @mock_capacity("companion.pyramid_runner.CAPACITY",4096)
    @mock_capacity("companion.services.CONTEXT_CAPACITY",4096)
    async def test_overflow_borrows_reserve_without_removing_protected_sources(self):
        result,llm,s,before,trace=await self.trial(3061)
        self.assertIsInstance(result,dict);self.assertEqual(llm.reserve,960);self.assertEqual(len(llm.calls),1)
        self.assertIn('較早完整回答',str(llm.calls[0]));self.assertEqual(llm.calls[0][-1]['content'],'目前完整原文')
        self.assertEqual(s['history'],before['history']);self.assertTrue(any(n['rule']=='context.output_reserve' for n in trace.nodes))

    @mock_capacity("companion.context_plan.CAPACITY",4096)
    @mock_capacity("companion.pyramid_runner.CAPACITY",4096)
    @mock_capacity("companion.services.CONTEXT_CAPACITY",4096)
    async def test_larger_input_still_stops_before_generation(self):
        result,llm,_,_,_=await self.trial(3265)
        self.assertIsInstance(result,ContextCapacityError);self.assertFalse(llm.calls)

    async def test_normal_input_keeps_original_output_budget(self):
        result,llm,_,_,trace=await self.trial(2900)
        self.assertIsInstance(result,dict);self.assertEqual(llm.reserve,1024)
        self.assertFalse(any(n['rule']=='context.output_reserve' for n in trace.nodes))

    async def test_truncated_observer_is_rejected_not_retried_or_committed(self):
        result,llm,s,before,_=await self.trial(3061,'length')
        self.assertIsInstance(result,ServiceError);self.assertEqual(len(llm.calls),1)
        self.assertEqual(s['history'],before['history'])

    @mock_capacity("companion.context_plan.CAPACITY",4096)
    @mock_capacity("companion.pyramid_runner.CAPACITY",4096)
    @mock_capacity("companion.services.CONTEXT_CAPACITY",4096)
    async def test_required_summary_overflow_uses_same_reserve_fallback(self):
        class Provider(FakeLLM):
            async def count_context(self,messages):
                return 3043 if '較早完整回答' in str(messages) else 2816
            async def generate(self,messages,**kw):
                self.reserve=kw['max_tokens'];return await super().generate(messages,**kw)
        llm=Provider([json.dumps(observation())]);s=initial_state(STAMP)
        s['history']=[{'role':'user','content':'較早提問'},{'role':'assistant','content':'較早完整回答'},
                      {'role':'user','content':'近期提問'},{'role':'assistant','content':'近期完整回答'}]
        trace=DecisionTrace();before=deepcopy(s['history'])
        await PyramidRunner(llm).observe(s,'晚安','user',trace)
        self.assertEqual(llm.reserve,960);self.assertEqual(len(llm.calls),1)
        self.assertIn('較早完整回答',str(llm.calls[0]));self.assertIn('近期完整回答',str(llm.calls[0]))
        self.assertEqual(s['history'],before)
        failure=next(n for n in trace.nodes if n['rule']=='context.capacity_failed')
        self.assertEqual(failure['output']['component'],'continuity_summary')
        self.assertTrue(any(n['rule']=='context.output_reserve' for n in trace.nodes))
