from copy import deepcopy
from datetime import datetime, timedelta
import json
import tempfile
from pathlib import Path
import unittest
from companion.memory_metadata import clock_context,message_context,with_scene_after,target_dates,date_matches,record_scene,scene_snapshot
from companion.pyramid import initial_state,DecisionTrace
from companion.pyramid_memory import add_batch,MemorySearch,memory_context,catalogue
from companion.pyramid_runner import writer_contract
from companion.pyramid_import import import_legacy_state
from companion.store import Store
import test_pyramid_telegram as fixtures
from test_pyramid import observation,event

class MetadataTests(unittest.IsolatedAsyncioTestCase):
    async def test_notes_and_raw_keep_scene_at_the_time_not_current_scene(self):
        s=initial_state('2026-10-15T14:00:00+08:00');s['scene']['location']='花園'
        context=message_context(s)
        s['history']=[{'role':'user','content':'我想喝茶','at':s['now'],'context':context,'turn_id':'t'}]
        add_batch(s,0,s['history'],[{'source_index':0,'text':'使用者想喝茶','evidence':'想喝茶','stage':'statement'}])
        s['scene']['location']='書房'
        self.assertEqual(s['memory']['notes'][0]['context']['scene']['location'],'花園')
        self.assertEqual(next(d for d in catalogue(s) if d['kind']=='raw')['context']['scene']['location'],'花園')
        selected=await MemorySearch().select(s,'花園喝茶',budget_chars=4000)
        self.assertTrue(selected['items'])
        self.assertTrue(any(i['context']['scene']['location']=='花園' for i in selected['items']))
        self.assertIn('context',memory_context(s)['items'][0])

    async def test_yesterday_filter_uses_local_clock_and_scene_metadata(self):
        s=initial_state('2026-10-16T10:00:00+08:00');s['clock']=clock_context(s['now'],'mac_mini_system_clock')
        s['history']=[]
        for at,place,text in [('2026-10-15T14:00:00+08:00','花園','我想喝茶'),
                              ('2026-10-16T09:00:00+08:00','花園','今天吃蛋糕'),
                              ('','花園','未知時間的談話')]:
            old=deepcopy(s);old['scene']['location']=place
            s['history'].append({'role':'user','content':text,'at':at,'context':message_context(old,at or old['now']) if at else {},'turn_id':'t'})
        add_batch(s,0,s['history'][:2],[]);add_batch(s,2,s['history'][2:],[])
        selection=await MemorySearch().select(s,'昨天花園聊什麼',budget_chars=4000,max_items=6)
        self.assertEqual(selection['time_filter']['dates'],['2026-10-15'])
        self.assertTrue(selection['items'])
        self.assertTrue(all(i['at'].startswith('2026-10-15') for i in selection['items']))
        self.assertTrue(any('喝茶' in i['text'] for i in selection['items']))
        self.assertTrue(date_matches('2026-10-15T15:00:00+00:00',['2026-10-15'],s['now']))
        self.assertFalse(date_matches('2026-10-15T17:00:00+00:00',['2026-10-15'],s['now']))

    async def test_relative_dates_and_unknown_old_metadata(self):
        now='2026-10-16T00:01:00+08:00'
        self.assertEqual(target_dates('昨天',now),['2026-10-15'])
        self.assertEqual(target_dates('前天',now),['2026-10-14'])
        self.assertEqual(target_dates('day before yesterday',now),['2026-10-14'])
        self.assertEqual(target_dates('今天是2026-10-16',now),['2026-10-16'])
        s=initial_state(now);s['history']=[{'role':'user','content':'舊紀錄','at':'','turn_id':'legacy'}]
        add_batch(s,0,s['history'],[])
        self.assertEqual(s['memory']['notes'][0]['context'],{})
        selection=await MemorySearch().select(s,'昨天舊紀錄')
        self.assertEqual(selection['items'],[])

    async def test_scene_journal_distinguishes_announced_and_arrived(self):
        from companion.pyramid import PyramidCore
        s=initial_state('2026-10-16T10:00:00+08:00');core=PyramidCore();trace=DecisionTrace()
        s['scene']['location']='花園';s['scene_notice_delivery']='external';s['scene_notifications_enabled']=True
        s['schedule']=[{'at':s['now'],'location':'書房','activity':'閱讀'}]
        core.advance(s,'2026-10-16T10:10:00+08:00',trace)
        self.assertEqual(s['scene_history'][-1]['phase'],'preparing')
        identifier=s['scene']['pending']['notice_id']
        core.acknowledge_scene_notice(s,identifier,'2026-10-16T10:11:00+08:00',trace)
        docs=catalogue(s)
        announced=next(d for d in docs if d.get('scene_event_phase')=='announced')
        self.assertEqual(announced['stage'],'proposed')
        self.assertIn('尚未抵達',announced['text'])
        core.advance(s,'2026-10-16T10:21:00+08:00',trace)
        self.assertEqual(s['scene_history'][-1]['phase'],'arrived')
        core.advance(s,'2026-10-16T10:22:00+08:00',DecisionTrace())
        self.assertTrue(any(e['phase']=='arrived' for e in s['scene_history']))
        selection=await MemorySearch().select(s,'今天書房',budget_chars=5000)
        self.assertTrue(any(i.get('scene_event_phase')=='arrived' for i in selection['items']))

    async def test_raw_import_uses_known_recorded_at_but_never_guesses_scene(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'memory.sqlite3')
            store.add_message('user','已知時間',at='2026-10-15T15:00:00+08:00')
            store.db.execute("insert into messages(role,content) values('user','未知舊時間')");store.db.commit()
            state=import_legacy_state(store.db_path,full_history=True)
            self.assertEqual(state['history'][0]['at'],'2026-10-15T15:00:00+08:00')
            self.assertEqual(state['history'][0]['context'],{})
            self.assertEqual(state['history'][1]['at'],'')
            store.close()

class TelegramMetadataTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.TelegramPyramidTests.asyncSetUp
    asyncTearDown=fixtures.TelegramPyramidTests.asyncTearDown
    text=fixtures.TelegramPyramidTests.text
    results=fixtures.TelegramPyramidTests.results
    obs=fixtures.TelegramPyramidTests.obs

    async def test_host_clock_and_scene_context_on_both_messages(self):
        now=datetime.fromisoformat('2026-10-16T10:00:00+08:00');self.app.now=lambda:now
        s=self.app.pyramid.load();s['now']=now.isoformat();s['scene']['location']='書房';self.app.pyramid.save_clock(s)
        text='我們走到花園了'
        self.results(self.obs(event('move',actor='user',target='scene',field='location',value='花園',evidence=text)),
                     '好，這裡很安靜。',self.obs())
        await self.text(text,1)
        state=self.app.pyramid.load();user,assistant=state['history'][-2:]
        self.assertEqual(user['at'],now.isoformat())
        self.assertEqual(user['context']['scene']['location'],'書房')
        self.assertEqual(user['context']['scene_after']['location'],'青葉莊園花園')
        self.assertEqual(assistant['context']['scene']['location'],'青葉莊園花園')
        self.assertEqual(assistant['context']['clock_source'],'mac_mini_system_clock')
        prompt=json.loads(self.store.get('last_text_prompt'))
        contract=json.loads(prompt[0]['content'].split('\n',1)[1])
        self.assertEqual(contract['current_time'],'今天是2026年10月16日星期五，現在是10點00分。')
        self.assertEqual(state['clock']['source'],'mac_mini_system_clock')
        self.assertEqual(contract['current_facts']['scene']['character_location'],'青葉莊園花園')
        self.assertEqual(contract['recent_dialogue_context'][0]['context']['scene_after'],'current')
        rows=self.store.db.execute('select recorded_at,context from messages order by id').fetchall()
        self.assertEqual(rows[0]['recorded_at'],user['at'])
        self.assertEqual(json.loads(rows[1]['context']),assistant['context'])

    async def test_receipt_timestamp_is_delivery_time_not_turn_start(self):
        base=datetime.fromisoformat('2026-10-16T10:00:00+08:00');now=[base];self.app.now=lambda:now[0]
        s=self.app.pyramid.load();s['now']=base.isoformat();self.app.pyramid.save_clock(s)
        self.results(self.obs(),'你好。',self.obs())
        async def delivered(*args,**kwargs):now[0]=base+timedelta(seconds=40)
        self.tg.send.side_effect=delivered
        await self.text('你好',1)
        h=self.app.pyramid.load()['history']
        self.assertEqual(h[-2]['at'],base.isoformat())
        self.assertEqual(h[-1]['at'],now[0].isoformat())
        self.assertEqual(self.store.db.execute("select recorded_at from messages where role='assistant'").fetchone()[0],now[0].isoformat())

    async def test_post_analysis_failure_keeps_delivered_context(self):
        self.results(self.obs(),'你好。','invalid')
        await self.text('你好',1)
        state=self.app.pyramid.load();reply=state['history'][-1]
        self.assertTrue(reply['context'])
        pending=state['_pending_responses'][0]
        self.assertEqual(pending['at'],reply['at'])
        self.assertEqual(pending['context'],reply['context'])

    async def test_photo_turn_still_excluded_from_memory(self):
        self.results(self.obs(event('image_request',evidence='拍照給我')),'等我一下。',self.obs())
        await self.text('拍照給我',1)
        self.assertEqual(self.app.pyramid.load()['history'],[])
        self.assertEqual(self.store.recent(),[])
        row=self.store.db.execute('select recorded_at,context,memory_scope from messages limit 1').fetchone()
        self.assertTrue(row['recorded_at'])
        self.assertTrue(json.loads(row['context']))
        self.assertEqual(row['memory_scope'],'tool')

    async def test_writer_clock_is_refreshed_after_slow_observation(self):
        from test_pyramid import FakeLLM
        base=datetime.fromisoformat('2026-10-16T10:00:00+08:00');now=[base];self.app.now=lambda:now[0]
        s=self.app.pyramid.load();s['now']=base.isoformat();self.app.pyramid.save_clock(s)
        owner=self
        class SlowObserver(FakeLLM):
            async def generate(self,*args,**kwargs):
                result=await super().generate(*args,**kwargs)
                if len(self.calls)==1:now[0]=base+timedelta(minutes=3)
                return result
        self.app.llm=SlowObserver([self.obs(),'現在是十點三分。',self.obs()])
        await self.text('現在幾點？',1)
        prompt=json.loads(self.store.get('last_text_prompt'))
        contract=json.loads(prompt[0]['content'].split('\n',1)[1])
        self.assertEqual(contract['current_time'],'今天是2026年10月16日星期五，現在是10點03分。')
        self.assertEqual(contract['recent_dialogue_context'][0]['at'],'2026-10-16 10:00')

    async def test_late_reply_analysis_does_not_rewrite_historical_scene(self):
        from companion.pyramid_runner import PyramidRunner
        old=datetime.fromisoformat('2026-10-16T10:00:00+08:00');now=old+timedelta(hours=1);self.app.now=lambda:now
        s=self.app.pyramid.load();s['now']=old.isoformat();s['scene']['location']='書房'
        context=message_context(s,old.isoformat(),'mac_mini_system_clock')
        reply='我到了浴室'
        mid=self.store.add_message('assistant',reply,at=old.isoformat(),context=context)
        s['history']=[{'role':'assistant','content':reply,'turn_id':'old','at':old.isoformat(),'context':deepcopy(context),'message_id':mid}]
        s['_pending_responses']=[{'reply':reply,'turn_id':'old','at':old.isoformat(),'retain_memory':True}]
        before=scene_snapshot(s);s['scene']['location']='花園';s['now']=now.isoformat()
        record_scene(s,'arrived',before,'daily_schedule')
        self.results(self.obs(event('move',actor='character',target='character',field='location',value='浴室',evidence=reply)))
        runner=PyramidRunner(self.app.llm,self.app.pyramid.core,self.app.pyramid.search,clock=self.app.now)
        await self.app.pyramid.recover_response(s,runner,DecisionTrace())
        self.assertEqual(s['scene']['location'],'花園')
        self.assertEqual(s['history'][0]['context'],context)
        self.assertEqual(json.loads(self.store.db.execute('select context from messages where id=?',(mid,)).fetchone()[0]),context)

    async def test_scene_history_command_and_reset(self):
        state=self.app.pyramid.load();before=scene_snapshot(state);state['scene']['location']='花園'
        record_scene(state,'arrived',before,'daily_schedule',self.app.now().isoformat())
        self.app.pyramid.save_clock(state)
        await self.text('/scene history',2)
        self.assertIn('花園',self.tg.send.call_args.args[1])
        self.assertEqual(self.llm.calls,[])
        await self.text('/reset',3)
        history=self.app.pyramid.load()['scene_history']
        self.assertEqual(len(history),1)
        self.assertEqual(history[0]['source'],'startup_sample')
        self.assertEqual(history[0]['phase'],'initialized')  # New baseline, never old dialogue.

class LegacyMetadataMigrationTests(unittest.TestCase):
    def test_old_sqlite_rows_keep_unknown_timestamp_and_scene(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'old.sqlite3'
            with sqlite3.connect(path) as db:
                db.executescript("CREATE TABLE messages(id INTEGER PRIMARY KEY,role TEXT NOT NULL,content TEXT NOT NULL); INSERT INTO messages(role,content) VALUES('user','舊訊息');")
            store=Store(path)
            row=store.db.execute('select recorded_at,context from messages').fetchone()
            self.assertEqual(row['recorded_at'],'')
            self.assertEqual(json.loads(row['context']),{})
            store.close()
