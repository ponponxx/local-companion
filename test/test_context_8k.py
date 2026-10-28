"""Current 8K service admission; original 4K tests explicitly retain their old boundary."""
import json,unittest
from copy import deepcopy
from dataclasses import replace
from companion.config import Config,CONTEXT_CAPACITY
from companion.context_plan import CAPACITY
from companion.services import LLM,ServiceError,ContextCapacityError
from companion.pyramid_runner import PyramidRunner,observer_output_reserve
from companion.pyramid import initial_state,DecisionTrace
from test_pyramid import FakeLLM,observation,STAMP

class Context8KTests(unittest.IsolatedAsyncioTestCase):
 def test_shared_default_and_reserve_boundaries(self):
  from scripts.start_chat import CONTEXT_CAPACITY as launch_capacity
  self.assertEqual(CONTEXT_CAPACITY,8192);self.assertEqual(CAPACITY,launch_capacity)
  self.assertEqual([observer_output_reserve(n) for n in (3411,7104,7157,7360,7361)], [1024,1024,960,768,None])

 async def test_service_keeps_request_above_old_4k_ceiling(self):
  class Counter(LLM):
   async def count_context(self,messages):return 5000
  llm=Counter(Config.load(require_credentials=False),None)
  messages=[{'role':'system','content':'規則'},{'role':'user','content':'完整輸入'}]
  actual,count=await llm.fit_context(messages,1024)
  self.assertEqual(actual,messages);self.assertEqual(count,5000)

 async def test_3411_input_needs_no_reduction_or_extra_generation(self):
  class Counter(FakeLLM):
   async def count_context(self,messages):return 3411
   async def generate(self,messages,**kw):self.reserve=kw['max_tokens'];return await super().generate(messages,**kw)
  s=initial_state(STAMP);s['history']=[{'role':'user','content':'先前問題'},{'role':'assistant','content':'先前完整回答'}]
  original=deepcopy(s['history']);t=DecisionTrace();llm=Counter([json.dumps(observation())])
  await PyramidRunner(llm).observe(s,'今天一直在開會。','user',t)
  self.assertEqual(s['history'],original);self.assertEqual(llm.reserve,1024);self.assertEqual(len(llm.calls),1)
  self.assertFalse(any(n['rule'] in {'context.output_reserve','context.capacity_failed'} for n in t.nodes))

 async def test_8k_required_overflow_still_stops_before_generation(self):
  class Counter(FakeLLM):
   async def count_context(self,messages):return 7361
  llm=Counter([])
  with self.assertRaises(ContextCapacityError):await PyramidRunner(llm).observe(initial_state(STAMP),'你好','user',DecisionTrace())
  self.assertFalse(llm.calls)
