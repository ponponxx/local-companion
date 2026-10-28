import asyncio
from copy import deepcopy
from datetime import datetime,timedelta
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import AsyncMock,patch
import httpx
from companion.external_info import ExternalInfo,parse_feed,reading,footer,public_query,packet
from companion.store import Store
from companion.pyramid import initial_state,PyramidCore,DecisionTrace,TurnInput
from companion.pyramid_runner import PyramidRunner
from companion.story_proactive import StoryProactive
from test_pyramid import FakeLLM,observation
import test_pyramid_telegram as fixtures

NOW=datetime.fromisoformat('2026-10-18T12:00:00+08:00')
FEED=b'''<rss><channel><item><title>New AI research</title><link>https://www.bbc.com/news/articles/example?utm_source=rss</link><pubDate>Sun, 18 Oct 2026 02:00:00 GMT</pubDate><description>Researchers announce an AI experiment. &lt;script&gt;bad()&lt;/script&gt;</description></item></channel></rss>'''

class ExternalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'test.db')
        self.requests=[]
        def handler(request):self.requests.append(request);return httpx.Response(200,content=FEED)
        self.info=ExternalInfo(self.store,httpx.MockTransport(handler))
        self.env=patch.dict('os.environ',{'BRAVE_SEARCH_API_KEY':''});self.env.start()
    async def asyncTearDown(self):self.env.stop();self.store.close();self.temp.cleanup()
    async def test_rss_refresh_cache_and_query_without_key(self):
        await self.info.refresh(NOW);await self.info.refresh(NOW)
        result=await self.info.query({'reason':'explicit','query':'AI','freshness':'week'},NOW)
        self.assertEqual(len(self.requests),1);self.assertEqual(result['status'],'rss_only')
        self.assertEqual(result['items'][0]['source'],'BBC Technology')
        self.assertNotIn('bad()',result['items'][0]['excerpt'])
        self.assertNotIn('utm_',result['items'][0]['url'])
        self.assertIn('非全文',reading(result));self.assertNotIn('02:00:00',reading(result))
    async def test_disabled_and_private_queries_do_not_fetch(self):
        self.store.set('web_enabled','false')
        self.assertEqual((await self.info.query({'reason':'explicit','query':'AI'},NOW))['status'],'disabled')
        self.store.set('web_enabled','true')
        for q in ['a@b.com','api_key abc','https://localhost/','123456789','密碼是abc']:
            self.assertEqual((await self.info.query({'reason':'explicit','query':q},NOW))['status'],'invalid_query')
        self.assertEqual(self.requests,[])
    async def test_unknown_and_non_search_do_nothing(self):
        self.assertIsNone(await self.info.query({'reason':'none','query':'AI'},NOW))
        self.assertIsNone(await self.info.query(None,NOW));self.assertEqual(self.requests,[])
    async def test_no_api_and_no_matches_is_not_web_absence(self):
        result=await self.info.query({'reason':'current','query':'量子電池價格'},NOW)
        self.assertEqual(result['status'],'unconfigured');self.assertIn('不代表',result['note'])
    async def test_only_fixed_network_destinations_no_redirect(self):
        for endpoint in ['http://127.0.0.1:8080','https://example.com','file:///etc/passwd']:
            with self.assertRaises(ValueError):await self.info.fetch(endpoint)
        self.info.transport=httpx.MockTransport(lambda r:httpx.Response(302,headers={'location':'http://127.0.0.1'}))
        await self.info.refresh(NOW);self.assertEqual(self.info.documents(NOW),[])
        self.assertEqual(self.store.get('news_status:technology'),'failed')
    async def test_brave_adapter_bounded_request_and_cache(self):
        def handler(r):
            self.requests.append(r)
            return httpx.Response(200,json={'web':{'results':[{'title':'Release','url':'https://example.org/release','description':'Official release summary'}]}})
        self.info.transport=httpx.MockTransport(handler)
        with patch.dict('os.environ',{'BRAVE_SEARCH_API_KEY':'test-not-real'}):
            req={'reason':'current','query':'public AI release','freshness':'day'}
            result=await self.info.query(req,NOW);await self.info.query(req,NOW)
        self.assertEqual(len(self.requests),1);self.assertEqual(self.requests[0].url.params['freshness'],'pd')
        self.assertEqual(self.requests[0].url.params['q'],'public AI release')
        self.assertEqual(result['items'][0]['coverage'],'search_excerpt')
        self.assertEqual(result['items'][0]['published_at'],'')
    async def test_network_failure_not_success_or_stale_cache(self):
        self.info.transport=httpx.MockTransport(lambda r:httpx.Response(503))
        with patch.dict('os.environ',{'BRAVE_SEARCH_API_KEY':'test'}):
            result=await self.info.query({'reason':'explicit','query':'AI'},NOW)
        self.assertEqual(result['status'],'failed');self.assertEqual(result['items'],[])
    async def test_dates_topic_off_and_expiry(self):
        await self.info.refresh(NOW)
        self.assertEqual(len(self.info.candidates(NOW)),1)
        self.assertEqual(self.info.candidates(NOW-timedelta(days=1)),[])
        self.assertEqual(self.info.candidates(NOW+timedelta(days=3)),[])
        self.store.set('news_topics','space');self.assertEqual(self.info.candidates(NOW),[])
        self.store.set('news_topics','technology');self.store.set('news_enabled','false');self.assertEqual(self.info.candidates(NOW),[])
    async def test_cancel_refresh_commits_no_partial_items(self):
        async def fetch(*a,**kw):raise asyncio.CancelledError()
        self.info.fetch=fetch
        with self.assertRaises(asyncio.CancelledError):await self.info.refresh(NOW)
        self.assertEqual(self.info.documents(NOW),[])
    async def test_reset_forgets_search_cache_not_public_documents(self):
        await self.info.query({'reason':'explicit','query':'AI'},NOW)
        self.assertGreater(self.store.db.execute('SELECT count(*) FROM external_cache').fetchone()[0],0)
        self.store.reset()
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM external_cache').fetchone()[0],0)
        self.assertEqual(len(self.info.documents(NOW)),1)
    async def test_candidates_use_same_pool_suppression_and_no_facts_mutation(self):
        await self.info.refresh(NOW);s=initial_state(NOW.isoformat());s['_news_candidates']=self.info.candidates(NOW)
        old=deepcopy(s['scene']);pool=StoryProactive().collect(s,NOW)
        news=next(c for c in pool if c['subtype']=='news')
        self.assertEqual(s['scene'],old);self.assertTrue(StoryProactive().valid(s,news,NOW))
        message={'role':'user','content':'這件事不要再說','at':NOW.isoformat()};s['history'].append(message)
        StoryProactive().control(s,message,{'story_control':{'scope':'topic','action':'close','ref':news['thread'],'evidence':message['content']}})
        self.assertFalse(any(c['subtype']=='news' for c in StoryProactive().collect(s,NOW)))
    def test_feed_rejects_entities_and_bad_source(self):
        with self.assertRaises(ValueError):parse_feed(b'<!DOCTYPE rss><rss/>','technology',NOW)
        bad=FEED.replace(b'https://www.bbc.com',b'http://localhost')
        self.assertEqual(parse_feed(bad,'technology',NOW),[])
        self.assertEqual(len(parse_feed(FEED.replace(b'</channel>',FEED.split(b'<channel>')[1].split(b'</channel>')[0]+b'</channel>'),'technology',NOW)),1)

class RunnerExternalTests(unittest.IsolatedAsyncioTestCase):
    async def test_writer_separate_evidence_original_user_last_and_no_state_pollution(self):
        state=initial_state(NOW.isoformat());state['history']=[{'role':'user','content':'今天AI新聞？','at':NOW.isoformat()}]
        decision=PyramidCore().decide(state,[],observation(external_request={'reason':'explicit','query':'AI','freshness':'any'},external_status='rss_only'),DecisionTrace())
        decision.update(information_scope='general',external_info=packet('rss_only',parse_feed(FEED,'technology',NOW)))
        runner=PyramidRunner(FakeLLM([]));old=deepcopy(state['scene'])
        messages=await runner.prepare_writer(state,decision,'RULES\n',DecisionTrace())
        self.assertIn('不是使用者發言',messages[1]['content']);self.assertEqual(messages[-1]['content'],'今天AI新聞？')
        self.assertNotIn('New AI research',messages[0]['content']);self.assertEqual(state['scene'],old)
        self.assertNotIn('https://',messages[1]['content']);self.assertIn('https://',footer(decision['external_info']))
    async def test_missing_sources_do_not_fabricate_success(self):
        state=initial_state(NOW.isoformat());obs=observation(external_request={'reason':'current','query':'AI','freshness':'day'},information_scope='general')
        llm=FakeLLM([json.dumps(obs),'目前沒有取得資料。',json.dumps(observation())])
        external=type('External',(),{'query':AsyncMock(return_value=packet('unconfigured',note='未設定搜尋API'))})()
        result=await PyramidRunner(llm,clock=lambda:NOW,external=external).run(state,TurnInput('最新AI新聞','x',True))
        self.assertEqual(external.query.await_count,1)
        self.assertEqual(result.decision['external_info']['status'],'unconfigured')
        self.assertIn('未設定搜尋API',llm.calls[1][1]['content'])
    async def test_private_name_blocks_external_request(self):
        state=initial_state(NOW.isoformat());state['user_profile']=[{'id':'1','category':'name','value':'Alex'}]
        obs=observation(external_request={'reason':'explicit','query':'Alex news','freshness':'any'})
        llm=FakeLLM([json.dumps(obs),'請給我公開主題。',json.dumps(observation())]);external=type('External',(),{'query':AsyncMock()})()
        result=await PyramidRunner(llm,clock=lambda:NOW,external=external).run(state,TurnInput('查一下','x',True))
        external.query.assert_not_awaited();self.assertEqual(result.decision['external_info']['status'],'invalid_query')
    async def test_evidence_budget_removes_whole_source(self):
        class Counter(FakeLLM):
            async def count_context(self,messages):return 850 if 'New AI research' in str(messages) else 100
        state=initial_state(NOW.isoformat());state['history']=[{'role':'user','content':'查新聞','at':NOW.isoformat()}]
        d=PyramidCore().decide(state,[],observation(external_request={'reason':'explicit','query':'AI','freshness':'any'},external_status='rss_only'),DecisionTrace());d['external_info']=packet('rss_only',parse_feed(FEED,'technology',NOW))
        messages=await PyramidRunner(Counter([])).prepare_writer(state,d,'',DecisionTrace())
        self.assertEqual(d['external_info']['items'],[]);self.assertNotIn('New AI research',str(messages))
        self.assertEqual(d['external_info']['status'],'budget_exceeded')

class TelegramExternalTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.TelegramPyramidTests.asyncSetUp
    asyncTearDown=fixtures.TelegramPyramidTests.asyncTearDown
    async def test_commands_no_models_and_settings_independent(self):
        prior=self.store.get('proactive_cooldown_minutes')
        await self.app.pyramid.command('/news','topics technology,space',42)
        await self.app.pyramid.command('/news','off',42)
        await self.app.pyramid.command('/web','status',42)
        self.assertEqual(self.llm.calls,[]);self.assertEqual(self.store.get('news_topics'),'technology,space')
        self.assertEqual(self.store.get('proactive_cooldown_minutes'),prior)
    async def test_source_footer_not_character_memory(self):
        self.app.now=lambda:NOW
        obs=observation(external_request={'reason':'current','query':'AI','freshness':'day'},information_scope='general')
        self.llm.results=iter([json.dumps(obs),'BBC報導了一項研究。'])
        self.app.pyramid.external.query=AsyncMock(return_value=packet('rss_only',parse_feed(FEED,'technology',NOW)))
        await self.app.pyramid.handle(900,42,'最近AI新聞呢？')
        self.assertIn('參考來源',self.tg.send.await_args.args[1])
        state=self.app.pyramid.load();self.assertNotIn('參考來源',state['history'][-1]['content'])
        self.assertNotIn('https://',state['_pending_responses'][-1]['reply'])

class ExternalLifecycleTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.TelegramPyramidTests.asyncSetUp
    asyncTearDown=fixtures.TelegramPyramidTests.asyncTearDown
    async def test_background_fetch_cannot_overwrite_new_user_state(self):
        self.app.now=lambda:NOW
        self.store.set('proactive_enabled','true');self.store.set('news_enabled','true')
        self.store.set('life_enabled','false');self.store.set('quiet_start','23:00');self.store.set('quiet_end','07:00')
        self.app.pyramid.save_clock(self.app.pyramid.load())
        async def refresh(now):
            fresh=self.app.pyramid.load();fresh['activity']='使用者剛更新的新活動';self.app.pyramid.save_clock(fresh)
        self.app.pyramid.external.refresh=refresh
        await self.app.pyramid.life.tick()
        self.assertEqual(self.app.pyramid.load()['activity'],'使用者剛更新的新活動')
        self.tg.send.assert_not_awaited()
    async def test_news_footer_and_real_source_recheck(self):
        ext=self.app.pyramid.external
        from companion.external_info import identity
        original=parse_feed(FEED,'technology',NOW)[0]
        changed=dict(original,title='Updated title')
        self.assertEqual(identity(original),identity(changed))
        for item in [original,changed]:
            with self.store.db:self.store.db.execute('INSERT OR REPLACE INTO external_documents VALUES(?,?,?,?)',(item['id'],item['topic'],item['published_at'],json.dumps(item)))
            current=ext.candidates(NOW)[0]
            if item is original:first=current
        self.assertEqual(first['id'],current['id']);self.assertNotEqual(first['source_version'],current['source_version'])
    async def test_external_prompt_audit_keeps_valid_contract(self):
        from companion.prompt_audit import audit_writer
        s=initial_state(NOW.isoformat());s['history']=[{'role':'user','content':'查新聞'}]
        d=PyramidCore().decide(s,[],observation(),DecisionTrace());d.update(external_info=packet('rss_only',parse_feed(FEED,'technology',NOW)))
        messages=await PyramidRunner(FakeLLM([])).prepare_writer(s,d,'RULES\n',DecisionTrace())
        self.assertNotIn('invalid_contract',[i['kind'] for i in audit_writer(messages)['issues']])

class SettingsVersionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.TelegramPyramidTests.asyncSetUp
    asyncTearDown=fixtures.TelegramPyramidTests.asyncTearDown
    async def test_settings_from_previous_field_layout_are_not_reused(self):
        session=self.app.settings_ui.fresh()
        self.assertTrue(self.app.settings_ui.valid(session))
        session.pop('fields')
        self.assertFalse(self.app.settings_ui.valid(session))

class ExternalGateIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_clarification_does_not_fetch_or_inject_news(self):
        state=initial_state(NOW.isoformat())
        obs=observation(external_request={'reason':'explicit','query':'新聞','freshness':'day'},
                        needs_clarification=True,missing_detail='哪個領域？')
        llm=FakeLLM([json.dumps(obs),'你想看哪個領域的新聞？',json.dumps(observation())])
        external=type('External',(),{'query':AsyncMock(return_value=packet('unconfigured'))})()
        result=await PyramidRunner(llm,clock=lambda:NOW,external=external).run(state,TurnInput('查新聞','gate',True))
        external.query.assert_not_awaited()
        self.assertEqual(result.decision['external_use']['mode'],'none')
        self.assertNotIn('【系統檢索',str(llm.calls[1]))
