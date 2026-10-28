import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import json
import unittest
from unittest.mock import AsyncMock
import test_pyramid_telegram as fixtures
from test_pyramid import observation, FakeLLM
from companion.pyramid import DecisionTrace
from companion.pyramid_life import PyramidLife
from companion.services import ServiceError

class LifeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.TelegramPyramidTests.asyncSetUp(self)
        self.now=datetime.fromisoformat('2026-10-16T10:00:00+08:00')
        self.app.now=lambda:self.now
        self.life=self.app.pyramid.life
        state=self.app.pyramid.load()
        state.update(now=self.now.isoformat(),updated_at=self.now.isoformat(),last_user_at=self.now.isoformat(),
                     life_plan_day=self.now.date().isoformat(),activity='休息',availability='free')
        state['scene'].update(location='臥室',character_location='臥室',user_location='浴室',status='active')
        state['schedule']=[{'at':self.now.isoformat(),'location':'書房','activity':'研究','availability':'busy','share_worthy':False,'clothing':'白襯衫','accessories':'魔杖'}]
        self.app.pyramid.save(state)
        self.store.set('last_user_at',self.now.isoformat())
        self.store.set('quiet_start','23:00');self.store.set('quiet_end','08:00')
        self.store.set('proactive_daily_limit','5')

    asyncTearDown=fixtures.TelegramPyramidTests.asyncTearDown
    def state(self):return self.app.pyramid.load()
    async def tick(self,minutes):
        self.now+=timedelta(minutes=minutes)
        await self.life.tick()

    async def test_idle_notice_and_ten_minutes_after_real_delivery(self):
        await self.tick(9)
        self.tg.send.assert_not_awaited()
        await self.tick(1)
        state=self.state();pending=state['scene']['pending']
        self.assertEqual(state['scene']['location'],'臥室')
        self.assertEqual(pending['delivery'],'delivered')
        self.assertEqual(pending['deadline'],(self.now+timedelta(minutes=10)).isoformat())
        self.tg.send.assert_awaited_once()
        await self.tick(9);self.assertEqual(self.state()['scene']['location'],'臥室')
        await self.tick(1)
        state=self.state()
        self.assertEqual(state['scene']['location'],'書房')
        self.assertEqual(state['scene']['character_location'],'書房')
        self.assertEqual(state['scene']['user_location'],'浴室')
        self.assertEqual(state['activity'],'研究')
        self.assertEqual(state['appearance']['clothing']['top']['description'],'白襯衫')
        self.assertEqual(self.llm.calls,[])
        self.assertEqual(len(state['history']),1)
        self.assertEqual(self.life.count_today(self.now),0)

    async def test_queue_time_is_not_delivery_time(self):
        state=self.state();trace=DecisionTrace()
        self.now+=timedelta(minutes=10);self.life.advance(state,self.now,trace)
        self.assertNotIn('deadline',state['scene']['pending'])
        self.now+=timedelta(minutes=30);self.life.advance(state,self.now,trace)
        self.assertEqual(state['scene']['location'],'臥室')
        await self.life.scene_notice(state,trace)
        self.assertEqual(state['scene']['pending']['deadline'],(self.now+timedelta(minutes=10)).isoformat())

    async def test_reply_does_not_cancel_or_restart_departed_trip(self):
        await self.tick(10);old=self.state()['scene']['pending']['notice_id']
        self.now+=timedelta(minutes=2)
        await self.app.handle(99,{'chat':{'id':42},'text':'/settings'})
        self.assertEqual(self.state()['scene']['pending']['notice_id'],old)
        await self.tick(7);self.assertEqual(self.state()['scene']['location'],'臥室')
        await self.tick(1)
        self.assertEqual(self.state()['scene']['location'],'書房')
        self.assertIsNone(self.state()['scene']['pending'])
        self.tg.send.assert_awaited_once()

    async def test_uncertain_send_never_moves_or_retries_after_restart(self):
        self.tg.send.side_effect=ServiceError('ambiguous')
        await self.tick(10)
        self.tg.send.assert_awaited_once()
        self.assertIsNone(self.state()['scene']['pending'])
        self.app.pyramid.life=PyramidLife(self.app.pyramid);self.life=self.app.pyramid.life
        await self.tick(30)
        self.assertEqual(self.state()['scene']['location'],'臥室')
        self.tg.send.assert_awaited_once()
        self.assertEqual(self.store.recent(),[])

    async def test_quiet_hours_silently_progress_and_normal_scene_notice_ignores_casual_quota(self):
        self.store.set('quiet_start','00:00');self.store.set('quiet_end','23:59')
        await self.tick(10);self.tg.send.assert_not_awaited()
        self.assertEqual(self.state()['scene']['character_location'],'書房')
        self.assertEqual(self.state()['scene']['user_location'],'浴室')
        self.assertIsNone(self.state()['scene']['pending'])
        self.store.set('quiet_start','23:00');self.store.set('quiet_end','08:00')
        self.store.set('proactive_enabled','false')
        await self.tick(1);self.tg.send.assert_not_awaited()
        state=self.state();state['scene'].update(location='臥室',character_location='臥室');self.app.pyramid.save(state)
        self.store.set('proactive_enabled','true');self.store.set('proactive_daily_limit','0')
        await self.tick(1);self.tg.send.assert_awaited_once()
        self.assertEqual(self.state()['scene']['location'],'臥室')
        self.assertGreater(float((datetime.fromisoformat(self.state()['now'])-datetime.fromisoformat('2026-10-16T10:00:00+08:00')).total_seconds()),0)

    async def test_user_daily_limit_unlimited_retains_other_gates(self):
        self.store.set('proactive_daily_limit','unlimited')
        self.store.set('last_user_at',(self.now-timedelta(hours=6)).isoformat())
        for i in range(12):
            self.store.db.execute('INSERT INTO pyramid_outbound VALUES(?,?,?,?,?,?,?)',
                (f'limit-{i}','casual','delivered',self.now.isoformat(),self.now.isoformat(),None,''))
        self.store.db.commit()
        self.assertEqual(self.life.gate(self.now,casual=True),'')
        self.store.set('proactive_daily_limit','10')
        self.assertEqual(self.life.gate(self.now,casual=True),'今日主動訊息上限')
        self.store.set('proactive_daily_limit','0')
        self.assertEqual(self.life.gate(self.now,casual=True),'今日主動訊息上限')
        self.store.set('proactive_daily_limit','unlimited')
        self.store.set('proactive_unanswered','2')
        self.assertEqual(self.life.gate(self.now,casual=True),'等待使用者回覆先前的主動訊息')

    async def test_delivered_deadline_can_commit_in_quiet_hours(self):
        await self.tick(10)
        self.store.set('quiet_start','00:00');self.store.set('quiet_end','23:59')
        await self.tick(10)
        self.assertEqual(self.state()['scene']['location'],'書房')
        self.tg.send.assert_awaited_once()

    async def test_scene_notice_does_not_consume_casual_quota_cooldown_or_unanswered(self):
        self.store.set('proactive_daily_limit','2')
        self.store.set('proactive_unanswered','2')
        self.store.set('last_proactive_at',self.now.isoformat())
        for i in range(2):
            self.store.db.execute('INSERT INTO pyramid_outbound VALUES(?,?,?,?,?,?,?)',
                (f'casual-{i}','casual','delivered',self.now.isoformat(),self.now.isoformat(),None,''))
        self.store.db.commit()
        self.assertEqual(self.life.count_today(self.now),2)
        self.assertEqual(self.life.gate(self.now,casual=True),'今日主動訊息上限')
        await self.tick(10)
        self.tg.send.assert_awaited_once()
        self.assertEqual(self.life.count_today(self.now),2)
        self.assertEqual(self.store.get('proactive_unanswered'),'2')
        self.assertEqual(self.store.get('last_proactive_at'),'2026-10-16T10:00:00+08:00')
        await self.tick(10)
        self.assertEqual(self.state()['scene']['character_location'],'書房')

    async def test_old_scene_accounting_is_rebuilt_once_from_casual_receipts(self):
        self.store.set('last_user_at',(self.now-timedelta(hours=1)).isoformat())
        self.store.set('proactive_unanswered','9')
        self.store.set('last_proactive_at',self.now.isoformat())
        sent=(self.now-timedelta(minutes=30)).isoformat()
        self.store.db.execute('INSERT INTO pyramid_outbound VALUES(?,?,?,?,?,?,?)',
            ('casual-old','casual','delivered',sent,sent,None,''))
        self.store.db.execute('INSERT INTO pyramid_outbound VALUES(?,?,?,?,?,?,?)',
            ('scene-old','scene','delivered',self.now.isoformat(),self.now.isoformat(),None,''))
        self.store.db.execute("DELETE FROM settings WHERE key='scene_quota_accounting'");self.store.db.commit()
        PyramidLife(self.app.pyramid)
        self.assertEqual(self.store.get('proactive_unanswered'),'1')
        self.assertEqual(self.store.get('last_proactive_at'),sent)
        self.store.set('proactive_unanswered','2');PyramidLife(self.app.pyramid)
        self.assertEqual(self.store.get('proactive_unanswered'),'2')

    async def test_life_off_cancels_pending_and_does_not_commit(self):
        await self.tick(10)
        self.store.set('life_enabled','false')
        await self.tick(10)
        self.assertEqual(self.state()['scene']['location'],'臥室')
        self.assertIsNone(self.state()['scene']['pending'])

    async def test_same_location_activity_advances_without_notification(self):
        state=self.state();state['schedule'][0]['location']='臥室';self.app.pyramid.save(state)
        await self.tick(10)
        self.assertEqual(self.state()['activity'],'研究')
        self.tg.send.assert_not_awaited()

    async def test_daily_plan_once_and_image_busy_does_not_wake_model(self):
        state=self.state();state['life_plan_day']='yesterday';self.app.pyramid.save(state)
        self.app.image_busy=True
        await self.tick(1);self.assertEqual(self.llm.calls,[])
        self.app.image_busy=False;self.store.set('proactive_enabled','false')
        items=[]
        for start in ['00:00','08:00','10:00','15:00','22:00']:
            items.append({'start':start,'activity':'休息','location':'莊園臥室','availability':'free','mood':'平靜','outcome':'休息','clothing':'家居服','accessories':'無','share_worthy':False})
        items[0]['availability']=items[-1]['availability']='sleep'
        self.llm.results=iter([json.dumps({'activities':items,'new_locations':[]},ensure_ascii=False)])
        await self.tick(1)
        self.assertEqual(len(self.llm.calls),1)
        self.assertEqual(len(self.state()['schedule']),5)
        await self.tick(1);self.assertEqual(len(self.llm.calls),1)

    async def test_new_environment_saved_in_same_plan_call_without_arrival(self):
        state=self.state();state['life_plan_day']='yesterday'
        items=[{'start':t,'activity':'觀察植物','location':'湖畔植物園','availability':'free',
                'mood':'平靜','outcome':'研究','clothing':'家居服','accessories':'無','share_worthy':False}
               for t in ['00:00','08:00','10:00','15:00','22:00']]
        detail={'name':'湖畔植物園','environment':'outdoor','background':'湖畔的戶外植物園，石板步道穿過花圃，遠處可見湖水與樹林。'}
        items[0]['availability']=items[-1]['availability']='sleep'
        self.llm.results=iter([json.dumps({'activities':items,'new_locations':[detail]},ensure_ascii=False)])
        before=deepcopy(state['scene'])
        await self.life.plan_day(state,self.now,DecisionTrace())
        self.assertEqual(len(self.llm.calls),1);self.assertEqual(state['scene'],before)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM scene_background_catalog').fetchone()[0],1)
        state['life_plan_day']='yesterday'
        items[0]['availability']=items[-1]['availability']='sleep'
        self.llm.results=iter([json.dumps({'activities':items,'new_locations':[]},ensure_ascii=False)])
        await self.life.plan_day(state,self.now,DecisionTrace())
        self.assertIn('湖畔植物園',json.loads(self.llm.calls[1][1]['content'])['location_options'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM scene_background_catalog').fetchone()[0],1)
        self.assertEqual(state['scene'],before)

    async def test_unknown_location_missing_details_does_not_commit_schedule(self):
        state=self.state();state['life_plan_day']='yesterday';before=deepcopy(state['schedule'])
        items=[{'start':t,'activity':'休息','location':'未建檔城堡頂樓','availability':'free',
                'mood':'平靜','outcome':'休息','clothing':'家居服','accessories':'無','share_worthy':False}
               for t in ['00:00','08:00','10:00','15:00','22:00']]
        items[0]['availability']=items[-1]['availability']='sleep'
        self.llm.results=iter([json.dumps({'activities':items,'new_locations':[]},ensure_ascii=False)])
        await self.life.plan_day(state,self.now,DecisionTrace())
        self.assertEqual(state['schedule'],before);self.assertEqual(state['life_plan_day'],'yesterday')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM scene_background_catalog').fetchone()[0],0)

    async def test_daily_plan_failure_backoff(self):
        state=self.state();state['life_plan_day']='yesterday';self.app.pyramid.save(state)
        self.llm.results=iter(['invalid'])
        await self.tick(1)
        self.assertEqual(len(self.llm.calls),1)
        await self.tick(5);self.assertEqual(len(self.llm.calls),1)
        self.assertEqual(self.state()['schedule'][0]['location'],'書房')

    async def prepare_casual(self):
        state=self.state();state['energy']=90;state['acute_stress']=state['stress']=0
        state['traits'].update(extraversion=90,expressiveness=90)
        self.store.update_trait('extraversion',90);self.store.update_trait('expressiveness',90)
        state['schedule']=[]
        state['last_user_at']=(self.now-timedelta(hours=3)).isoformat()
        self.store.set('last_user_at',state['last_user_at'])
        state['life_current']={'at':self.now.isoformat(),'location':'臥室','activity':'讀書','share_worthy':True}
        state['activity']='讀書'
        self.app.pyramid.save(state)

    async def test_casual_uses_core_writer_and_confirmed_message_memory(self):
        self.store.set('story_events_enabled','false')  # Isolate the existing contact receipt contract.
        await self.prepare_casual()
        self.llm.results=iter(['我剛在讀一本書，想到你可能也會喜歡。',json.dumps(observation([]),ensure_ascii=False)])
        await self.tick(1)
        self.tg.send.assert_awaited_once()
        self.assertEqual(len(self.state()['_pending_responses']),1)
        self.llm.healthy=AsyncMock(return_value=True)
        await self.app.pyramid.maintain_responses()
        state=self.state()
        self.assertEqual(len(state['history']),1)
        self.assertEqual(state['_pending_responses'],[])
        prompt=json.loads(self.store.get('last_text_prompt'))
        self.assertEqual([m['role'] for m in prompt],['system','user'])
        self.assertEqual(prompt[-1]['content'],'請撰寫本輪已決定的主動訊息。')
        self.assertNotIn('background_life_engine',json.dumps(prompt,ensure_ascii=False))
        contract=json.loads(prompt[0]['content'].split('\n',1)[1])
        self.assertIn(contract['decision']['primary'],{'share_self','invite'})
        self.assertEqual(contract['current_facts']['activity'],'讀書')
        self.assertNotIn('autonomous_context',contract)
        await self.tick(300)
        self.tg.send.assert_awaited_once()

    async def test_incoming_message_cancels_background_generation_before_send(self):
        await self.prepare_casual()
        entered=asyncio.Event()
        async def blocked(*args,**kwargs):entered.set();await asyncio.Future()
        self.app.llm.generate=blocked
        task=asyncio.create_task(self.app.idle_summary())
        await asyncio.wait_for(entered.wait(),1)
        self.app.inbox_event.set()
        await asyncio.wait_for(task,1)
        self.tg.send.assert_not_awaited()
        self.assertEqual(self.store.recent(),[])

    async def test_tick_is_throttled_and_heartbeat_status_visible(self):
        await self.life.tick()
        stamp=self.store.get('last_pyramid_heartbeat')
        self.assertTrue(stamp)
        await self.life.tick()
        self.assertEqual(stamp,self.store.get('last_pyramid_heartbeat'))
        await self.app.handle(99,{'chat':{'id':42},'text':'/proactive'})
        self.assertIn('最近心跳',self.tg.send.call_args.args[1])

    async def test_cancelled_http_send_is_uncertain_and_not_retried(self):
        entered=asyncio.Event()
        async def blocked(*args,**kwargs):entered.set();await asyncio.Future()
        self.tg.send.side_effect=blocked
        self.now+=timedelta(minutes=10)
        task=asyncio.create_task(self.life.tick())
        await asyncio.wait_for(entered.wait(),1);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(self.store.db.execute('select status from pyramid_outbound').fetchone()[0],'uncertain')
        self.assertIsNone(self.state()['scene']['pending'])
        await self.tick(20)
        self.tg.send.assert_awaited_once()
        self.assertEqual(self.state()['scene']['location'],'臥室')

    async def test_heartbeat_does_not_rewrite_profile_evidence(self):
        self.store.set_profile_fact('稱呼','Alex')
        self.store.db.execute("update user_profile_facts set updated_at='2026-10-01T00:00:00+08:00'");self.store.db.commit()
        await self.tick(1)
        self.assertEqual(self.store.profile_facts()[0]['updated_at'],'2026-10-01T00:00:00+08:00')

    async def test_morning_reset_runs_without_waking_model(self):
        state=self.state();state['appearance'].update(hair='盤髮',makeup='濃妝');state['appearance_day']='2026-10-16'
        self.app.pyramid.save_clock(state);self.store.set('life_enabled','false')
        self.now=datetime.fromisoformat('2026-10-17T06:00:00+08:00')
        await self.life.tick()
        self.assertEqual(self.state()['appearance']['hair'],self.store.appearance()['hair'])
        self.assertEqual(self.llm.calls,[])
        self.assertEqual(self.state()['scene']['location'],'臥室')

    async def test_old_simulated_receipt_cannot_commit_in_telegram(self):
        state=self.state();state['scene']['pending']={'location':'書房','at':state['now'],
            'deadline':state['now'],'delivery':'simulated_delivered'}
        self.app.pyramid.save_clock(state)
        await self.tick(1)
        self.assertEqual(self.state()['scene']['location'],'臥室')
        self.tg.send.assert_not_awaited()

    async def test_confirmed_deadline_survives_process_restart(self):
        await self.tick(10)
        self.app.pyramid.life=PyramidLife(self.app.pyramid);self.life=self.app.pyramid.life
        await self.tick(10)
        self.assertEqual(self.state()['scene']['location'],'書房')
        self.tg.send.assert_awaited_once()

    async def test_casual_model_failure_has_backoff_instead_of_waking_every_tick(self):
        await self.prepare_casual()
        self.app.llm.generate=AsyncMock(side_effect=ServiceError('本地模型暫時不可用'))
        await self.tick(1)
        self.app.llm.generate.assert_awaited_once()
        await self.tick(1)
        self.app.llm.generate.assert_awaited_once()
        self.tg.send.assert_not_awaited()

    async def test_daily_failure_is_persistent_diagnostic_not_lost_by_next_heartbeat(self):
        state=self.state();state.pop('life_plan_day',None)
        self.llm.results=iter(['invalid'])
        await self.life.plan_day(state,self.now,DecisionTrace())
        failure=json.loads(self.store.get('last_life_plan_failure'))
        self.assertEqual(failure,state['life_plan_failure'])
        self.assertEqual(failure['retry_at'],state['life_plan_retry'])
        self.assertEqual(state['scene']['character_location'],'臥室')

    async def test_old_queued_notice_is_cancelled_without_arrival(self):
        state=self.state();old=self.now-timedelta(days=1)
        state['scene']['pending']={'at':old.isoformat(),'location':'書房','activity':'研究','delivery':'queued','notice_id':'old'}
        state['schedule']=[]
        self.life.advance(state,self.now,DecisionTrace())
        self.assertIsNone(state['scene']['pending'])
        self.assertEqual(state['scene']['character_location'],'臥室')
        self.assertEqual(state['scene_history'][-1]['phase'],'cancelled')

    async def test_midnight_routine_and_wakeup_continue_without_models_or_messages(self):
        state=self.state();state['life_plan_day']='2026-10-15'
        state['scene'].update(location='球場',character_location='球場')
        state['schedule']=[{'at':'2026-10-15T00:00:00+08:00','start':'00:00','start_minute':0,'location':'青葉莊園臥室','availability':'sleep','activity':'睡眠','clothing':'白色睡衣'},
                           {'at':'2026-10-15T07:30:00+08:00','start':'07:30','start_minute':450,'location':'浴室','availability':'free','activity':'整理'}]
        self.now=datetime.fromisoformat('2026-10-17T01:00:00+08:00')
        self.life.advance(state,self.now,DecisionTrace())
        self.assertEqual(state['scene']['character_location'],'青葉莊園臥室')
        self.assertEqual(state['scene']['user_location'],'浴室')
        self.assertEqual(state['availability'],'sleep')
        self.assertEqual(state['scene_history'][-1]['at'],self.now.isoformat())
        self.assertEqual(state['scene_history'][-1]['source'],'daily_schedule_silent')
        self.assertEqual(state['life_plan_day'],'2026-10-15')
        count=len(state['schedule']);self.life.advance(state,self.now,DecisionTrace())
        self.assertEqual(len(state['schedule']),count)
        await self.life.plan_day(state,self.now,DecisionTrace())
        self.assertFalse(self.llm.calls)
        self.now=self.now.replace(hour=7,minute=30);self.life.advance(state,self.now,DecisionTrace())
        self.assertEqual(state['availability'],'free');self.assertEqual(state['activity'],'起床與晨間整理')
        self.tg.send.assert_not_awaited()

    async def test_quiet_transition_still_respects_recent_conversation(self):
        state=self.state();self.store.set('quiet_start','00:00');self.store.set('quiet_end','23:59')
        self.now+=timedelta(minutes=1);self.life.advance(state,self.now,DecisionTrace())
        self.assertEqual(state['scene']['character_location'],'臥室')
        self.now+=timedelta(minutes=9);self.life.advance(state,self.now,DecisionTrace())
        self.assertEqual(state['scene']['character_location'],'書房')
        self.tg.send.assert_not_awaited()

    async def test_manual_plan_can_run_in_quiet_then_replace_routine_at_same_location(self):
        state=self.state();state['life_plan_day']='yesterday';state['schedule']=[]
        self.now=datetime.fromisoformat('2026-10-17T01:00:00+08:00')
        self.life.advance(state,self.now,DecisionTrace())
        self.assertEqual(state['availability'],'sleep')
        items=[dict(start=t,activity='安靜睡眠' if j in (0,4) else '閱讀',location='青葉莊園臥室',availability='sleep' if j in (0,4) else 'free',mood='平靜',outcome='休息',clothing='藍色棉質睡衣',accessories='無',share_worthy=False,arrival_activity='') for j,t in enumerate(['00:00','08:00','10:00','15:00','22:30'])]
        self.llm.results=iter([json.dumps({'activities':items,'new_locations':[]},ensure_ascii=False)])
        await self.life.plan_day(state,self.now,DecisionTrace(),force=True)
        self.life.advance(state,self.now,DecisionTrace())
        self.assertEqual(len(self.llm.calls),1)
        self.assertEqual(state['life_plan_day'],'2026-10-17')
        self.assertEqual(state['activity'],'安靜睡眠')
        self.assertNotIn('plan_source',state['life_current'])
        self.tg.send.assert_not_awaited()

    async def test_annual_table_loads_in_quiet_with_zero_model_calls(self):
        self.store.set('life_calendar_enabled','true')
        self.store.set('quiet_start','00:00');self.store.set('quiet_end','23:59')
        state=self.state();state['life_plan_day']='yesterday';before=deepcopy(state['scene']);history=deepcopy(state['history'])
        await self.life.plan_day(state,self.now,DecisionTrace())
        self.assertEqual(len(state['schedule']),9);self.assertEqual(state['life_plan_day'],self.now.date().isoformat())
        self.assertEqual(state['life_plan_contract'],'annual-table-v1');self.assertEqual(self.llm.calls,[])
        self.assertEqual(state['scene'],before);self.assertEqual(state['history'],history)
        self.assertTrue(state['life_calendar_day']['planning_only'])
        self.tg.send.assert_not_awaited()

    async def test_broken_annual_table_never_silently_falls_back_to_model(self):
        from unittest.mock import patch
        self.store.set('life_calendar_enabled','true');state=self.state();state['life_plan_day']='yesterday';before=deepcopy(state['schedule'])
        with patch('companion.annual_schedule.install_day',side_effect=ValueError('invalid calendar')):
            await self.life.plan_day(state,self.now,DecisionTrace())
        self.assertEqual(state['schedule'],before);self.assertEqual(state['life_plan_day'],'yesterday')
        self.assertEqual(state['life_plan_failure']['source'],'annual_table');self.assertEqual(self.llm.calls,[])
