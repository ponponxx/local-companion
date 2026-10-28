"""Response-only extraction must stay sparse without discarding real changes."""
import json,unittest
from copy import deepcopy
from companion.pyramid_runner import (PyramidRunner,RESPONSE_OBSERVATION_SCHEMA,
 OBSERVATION_SCHEMA,validate_output)
from companion.response_observer import normalize_response
from companion.pyramid import initial_state,DecisionTrace
from test_pyramid import FakeLLM,STAMP,observation,event

class DeltaTests(unittest.IsolatedAsyncioTestCase):
 def test_sparse_wire_restores_defaults_and_deduplicates_same_change(self):
  body={k:v for k,v in observation().items() if k in RESPONSE_OBSERVATION_SCHEMA['properties']}
  for k in ['ref','strength']:body['utterance'].pop(k,None)
  e=event('move',actor='character',target='character',field='location',value='花園',evidence='到花園了')
  for k in ['ref','strength']:e.pop(k,None)
  body['events']=[e,dict(e,evidence='我到花園了')]
  validate_output(body,RESPONSE_OBSERVATION_SCHEMA)
  result=normalize_response(body)
  self.assertEqual(len(result['events']),1);self.assertEqual(result['events'][0]['strength'],1)
  self.assertEqual(result['utterance']['ref'],'');self.assertEqual(len(body['events']),2)
 def test_distinct_events_and_real_references_survive(self):
  b=observation([event('move',actor='character',target='character',field='departure',value='花園',evidence='出發'),
    event('move',actor='character',target='character',field='location',value='花園',evidence='抵達')])
  r=normalize_response(b);self.assertEqual(len(r['events']),2)
  b['events']=[event('accept',ref='a'),event('accept',ref='b')]
  self.assertEqual(len(normalize_response(b)['events']),2)
 def test_user_contract_not_changed_by_response_sparsity(self):
  self.assertIn('ref',OBSERVATION_SCHEMA['properties']['events']['items']['required'])
  self.assertNotIn('ref',RESPONSE_OBSERVATION_SCHEMA['properties']['events']['items']['required'])
 async def test_response_prompt_uses_delta_and_character_journey_rules(self):
  llm=FakeLLM([json.dumps(observation())]);runner=PyramidRunner(llm)
  await runner.observe(initial_state(STAMP),'午安，我仍在溫室整理資料。','assistant',DecisionTrace())
  system=llm.calls[0][0]['content']
  self.assertIn('描述目前所在地不等於移動',system)
  self.assertIn('actor與target均為character',system)
  self.assertNotIn('user說「我們到圖書館了」',system)
 async def test_user_still_receives_full_journey_protocol(self):
  llm=FakeLLM([json.dumps(observation())]);runner=PyramidRunner(llm)
  await runner.observe(initial_state(STAMP),'我們到圖書館了','user',DecisionTrace())
  self.assertIn('user說「我們到圖書館了」',llm.calls[0][0]['content'])

import test_pyramid_telegram as fixtures
from unittest.mock import AsyncMock
from companion.services import ServiceError

class PendingRecoveryTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=fixtures.TelegramPyramidTests.asyncSetUp
 asyncTearDown=fixtures.TelegramPyramidTests.asyncTearDown
 async def test_sparse_recovery_commits_once_and_unblocks_next_turn(self):
  from test_pyramid import observation
  self.llm.results=iter([json.dumps(observation()),'我已經到花園了。','invalid json'])
  await fixtures.TelegramPyramidTests.text(self,'你好',1)
  self.assertEqual(len(self.app.pyramid.load()['_pending_responses']),1)
  body={k:v for k,v in observation().items() if k in RESPONSE_OBSERVATION_SCHEMA['properties']}
  body['utterance'].update(act='share',value='抵達花園',evidence='我已經到花園了。')
  body['events']=[{'kind':'move','actor':'character','target':'character','stage':'completed',
      'field':'location','value':'花園','evidence':'我已經到花園了。'}]
  self.llm.results=iter([json.dumps(body,ensure_ascii=False),json.dumps(observation()),'午安。',json.dumps(observation())])
  await fixtures.TelegramPyramidTests.drain(self)
  await fixtures.TelegramPyramidTests.text(self,'午安',2)
  state=self.app.pyramid.load();self.assertFalse(state['_pending_responses'])
  self.assertEqual(state['scene']['character_location'],'青葉莊園花園')
  self.assertEqual(sum(c.args[1].endswith('我已經到花園了。') for c in self.tg.send.await_args_list),1)
 async def test_failure_reason_remains_visible_and_pending_not_discarded(self):
  self.llm.results=iter([json.dumps(observation()),'午安。','invalid json'])
  await fixtures.TelegramPyramidTests.text(self,'你好',1)
  state=self.app.pyramid.load();before=deepcopy(state['_pending_responses'])
  self.llm.generate=AsyncMock(side_effect=ServiceError('模型輸出達上限；本輪標示未完成，不提交角色結果'))
  trace=DecisionTrace();ok=await self.app.pyramid.recover_response(state,PyramidRunner(self.llm),trace)
  self.assertFalse(ok);self.assertEqual(state['_pending_responses'],before)
  node=next(n for n in trace.nodes if n['rule']=='telegram.response_deferred')
  self.assertIn('輸出達上限',node['output']['message'])
