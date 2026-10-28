from copy import deepcopy
import asyncio,json,unittest
from unittest.mock import AsyncMock
from companion.photo_clothing import project
from companion.services import ContextCapacityError
from companion.pyramid import initial_state,PyramidCore,DecisionTrace
from companion.pyramid_runner import PyramidRunner
from companion.external_info import packet
from test_pyramid import FakeLLM,observation,STAMP
import test_pyramid_telegram as fixtures


class PhotoWardrobeTests(unittest.TestCase):
    def row(self,**kw):
        return dict(wearer='character',slot='outerwear',operation='wear',intent='requested',value='紅色外套',evidence='穿紅色外套',**{})|kw
    def test_photo_override_is_local_and_source_bound(self):
        original={'top':'白色襯衫','outerwear':'黑色外套','shoes':'棕色皮鞋'};old=deepcopy(original)
        projected,accepted,rejected=project(original,[self.row()],'穿紅色外套，閉上眼睛')
        self.assertEqual(projected['outerwear'],'紅色外套');self.assertEqual(projected['shoes'],'棕色皮鞋')
        self.assertEqual(original,old);self.assertEqual(accepted,['outerwear']);self.assertEqual(rejected,0)
    def test_reference_wrong_actor_and_unsourced_override_do_not_apply(self):
        for row in [self.row(intent='reference'),self.row(intent='negated'),self.row(intent='hypothetical'),
                    self.row(wearer='user'),self.row(value='藍色外套'),self.row(evidence='昨天穿紅色外套')]:
            result,accepted,rejected=project({'outerwear':'黑色外套'},[row],'穿紅色外套')
            self.assertEqual(result,{'outerwear':'黑色外套'});self.assertFalse(accepted);self.assertEqual(rejected,1)
    def test_local_viewer_clause_is_not_character_wardrobe(self):
        result,accepted,rejected=project({'outerwear':'黑色外套'},[self.row(evidence='紅色外套')],'我穿紅色外套，妳保持現在的衣著')
        self.assertFalse(accepted);self.assertEqual(rejected,1);self.assertEqual(result['outerwear'],'黑色外套')
    def test_photo_dress_replaces_default_separates_without_changing_source(self):
        slots={'top':'白色襯衫','bottom':'長褲'}
        result,accepted,_=project(slots,[self.row(slot='dress',value='藍色洋裝',evidence='穿藍色洋裝')],'這張穿藍色洋裝')
        self.assertEqual(result['dress'],'藍色洋裝');self.assertEqual(result['bottom'],'未穿著')
        self.assertEqual(slots,{'top':'白色襯衫','bottom':'長褲'})
    def test_remove_does_not_mean_unknown(self):
        result,accepted,_=project({'shoes':'皮鞋'},[self.row(slot='shoes',operation='remove',value='',evidence='不穿鞋')],'這張不穿鞋')
        self.assertEqual(result['shoes'],'未穿著');self.assertEqual(accepted,['shoes'])


class NewsPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_packet_cannot_inject_itself_without_core_permission(self):
        state=initial_state(STAMP);state['history']=[{'role':'user','content':'你好'}]
        d=PyramidCore().decide(state,[],observation(),DecisionTrace())
        d['external_info']=packet('rss_only',[{'title':'UNSELECTED','source':'Example','excerpt':'not selected'}])
        trace=DecisionTrace();messages=await PyramidRunner(FakeLLM([])).prepare_writer(state,d,'',trace)
        self.assertNotIn('UNSELECTED',str(messages));self.assertNotIn('external_info',d)
        self.assertTrue(any(n['rule']=='external.not_used' for n in trace.nodes))
    async def test_explicit_query_gets_core_mode_and_brief(self):
        from companion.reply_brief import compile_reply_brief
        state=initial_state(STAMP)
        obs=observation(external_request={'reason':'explicit','query':'科技','freshness':'day'},external_status='rss_only')
        d=PyramidCore().decide(state,[],obs,DecisionTrace())
        self.assertEqual(d['external_use']['mode'],'answer_request')
        self.assertIn('來源摘要',compile_reply_brief(state,d)['outline'])
        obs.update(needs_clarification=True,missing_detail='要查哪個領域？')
        d=PyramidCore().decide(state,[],obs,DecisionTrace())
        self.assertEqual(d['primary'],'clarify');self.assertEqual(d['external_use']['mode'],'none')


class DiagnosticsIntegrationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.TelegramPyramidTests.asyncSetUp
    asyncTearDown=fixtures.TelegramPyramidTests.asyncTearDown
    async def test_capacity_notice_reports_safe_counters_without_prompt(self):
        message={'chat':{'id':42},'text':'你好'}
        self.store.db.execute('INSERT INTO inbox(update_id,payload) VALUES(?,?)',(123,json.dumps(message)));self.store.db.commit()
        self.app.handle=AsyncMock(side_effect=ContextCapacityError('user_observer',3032,1024,3008))
        received=[]
        async def notice(chat,text):received.append(text);raise asyncio.CancelledError()
        self.app.notify=notice
        with self.assertRaises(asyncio.CancelledError):await self.app.inbox_worker()
        self.assertIn('第一次事件判讀',received[0]);self.assertIn('超出 24',received[0])
        failure=json.loads(self.store.get('last_processing_failure'))
        self.assertEqual(failure['input_tokens'],3032);self.assertNotIn('你好',str(failure))
    async def test_root_menu_exposes_current_clothing_without_models(self):
        await self.app.settings_ui.show(42)
        markup=self.tg.call.await_args.args[1]['reply_markup']['inline_keyboard']
        self.assertTrue(any(b['text']=='當前穿著' and b['callback_data'].endswith('g.current_clothing') for row in markup for b in row))
        self.assertEqual(self.llm.calls,[])
