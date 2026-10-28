from .proactive_limits import parse_limit, limit_label, limit_reached
"""Idle orchestration for the shared Pyramid state. No separate state engine.

Called by the existing cancellable idle task, so all writes stay serialized with
inbox handling. Outbound receipts are durable before any scene deadline starts.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import logging

from .life import in_quiet_hours, parse_daily_plan
from .pyramid import DecisionTrace, date
from .pyramid_runner import PyramidRunner, writer_contract, writer_length, WRITER_VOICE
from .pyramid_memory import MemorySearch
from .memory_metadata import clock_context, message_context, scene_snapshot, record_scene, with_scene_after
from .services import ServiceError
from .diagnostics import diagnosed,link
from .story_proactive import StoryProactive
from .scene_catalog_store import SceneCatalogStore, NEW_LOCATION_SCHEMA
from . import life_events

log=logging.getLogger(__name__)

def transitioning(state):
    """Scene notifications and ordinary proactive chat have separate turns."""
    scene=state['scene']
    return bool(scene.get('pending') or scene.get('journeys',{}).get('character',{}).get('status')=='moving')

PLAN_FIELDS={key:{'type':'string','minLength':1,'maxLength':120} for key in ('start','activity','location','outcome','mood','clothing','accessories')}
for _key in ('activity','outcome','mood','accessories'):PLAN_FIELDS[_key]['maxLength']=24
PLAN_FIELDS['clothing']['maxLength']=60
PLAN_FIELDS['arrival_activity']={'type':'string','maxLength':120}
PLAN_FIELDS.update(availability={'enum':['free','busy','sleep']},share_worthy={'type':'boolean'})
DAILY_PLAN_SCHEMA={'type':'object','additionalProperties':False,'required':['activities','new_locations'],'properties':{
    'new_locations':{'type':'array','maxItems':9,'items':NEW_LOCATION_SCHEMA},
    'activities':{'type':'array','minItems':5,'maxItems':9,'items':{'type':'object','additionalProperties':False,
        'required':list(PLAN_FIELDS),'properties':PLAN_FIELDS}}}}



class PyramidLife:
    def __init__(self, adapter):
        self.adapter=adapter;self.app=adapter.app;self.store=adapter.store
        with self.store.db:
            self.store.db.execute('''CREATE TABLE IF NOT EXISTS pyramid_outbound(
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, sent_at TEXT, assistant_id INTEGER, detail TEXT NOT NULL DEFAULT '')''')
            # Interrupted HTTP sends are ambiguous. Never automatically repeat.
            self.store.db.execute("UPDATE pyramid_outbound SET status='uncertain' WHERE status='sending'")
        if self.store.get('scene_quota_accounting')!='casual-only-v1':
            receipts=[date(r[0]) for r in self.store.db.execute(
                "SELECT sent_at FROM pyramid_outbound WHERE kind='casual' AND status='delivered' AND sent_at IS NOT NULL")]
            last_user=self.store.get('last_user_at')
            since=date(last_user) if last_user else None
            self.store.set('last_proactive_at',max(receipts).isoformat() if receipts else '')
            self.store.set('proactive_unanswered',str(sum(since is None or sent>since for sent in receipts)))
            self.store.set('scene_quota_accounting','casual-only-v1')
        self.next_tick=None

    def count_today(self, now):
        return sum(date(r[0]).astimezone(self.app.zone).date()==now.date() for r in self.store.db.execute(
            "SELECT sent_at FROM pyramid_outbound WHERE kind='casual' AND status='delivered' AND sent_at IS NOT NULL"))

    def gate(self, now, casual=False):
        if self.store.get('proactive_enabled')!='true':return '主動訊息已關閉'
        if in_quiet_hours(now,self.store.get('quiet_start'),self.store.get('quiet_end')):return '安靜時段'
        if casual and limit_reached(self.store.get('proactive_daily_limit','2'), self.count_today(now)):return '今日主動訊息上限'
        # Casual messages back off after two unanswered contacts; scene notices
        # obey quiet hours, but do not consume the casual-message quota.
        unanswered=int(self.store.get('proactive_unanswered','0'))
        if casual and unanswered>=2:return '等待使用者回覆先前的主動訊息'
        if casual:
            last_user=date(self.store.get('last_user_at'))
            if (now-last_user).total_seconds()<int(self.store.get('proactive_min_silence_minutes','120'))*60:return '尚未達主動聊天閒置時間'
            last=self.store.get('last_proactive_at')
            if last and (now-date(last)).total_seconds()<int(self.store.get('proactive_cooldown_minutes','240'))*60:return '主動聊天冷卻中'
        return ''

    def advance(self, state, now, trace):
        state['clock']=clock_context(now,'mac_mini_system_clock')
        enabled=self.store.get('life_enabled')=='true'
        state['scene_clock_enabled']=enabled
        state['scene_notice_delivery']='external'
        state['scene_notifications_enabled']=enabled and not self.gate(now)
        # Any uncertain/failed notification is consumed, not auto-retried.
        state['scene_notice_blocked']=[r[0] for r in self.store.db.execute("SELECT id FROM pyramid_outbound WHERE kind='scene' AND status IN ('uncertain','failed')")]
        pending=state['scene'].get('pending')
        if pending and pending.get('delivery')=='queued' and pending.get('at') and date(pending['at']).astimezone(now.tzinfo).date()<now.date():
            before=scene_snapshot(state)
            state['scene']['pending']=None;pending=None
            record_scene(state,'cancelled',before,'expired_schedule',now.isoformat())
        if pending and pending.get('delivery') not in {'queued','delivered'}:
            state['scene']['pending']=None;pending=None
        if pending and (not enabled or (pending.get('delivery')=='queued' and not state['scene_notifications_enabled'])
                        or pending.get('notice_id') in state['scene_notice_blocked']):
            before=scene_snapshot(state)
            state['scene']['pending']=None
            if state['scene'].get('status')=='moving':
                state['scene']['status']='active';state['activity']=''
            record_scene(state,'cancelled',before,'background_policy',now.isoformat())
        if enabled:self.ensure_overnight_schedule(state,now,trace)
        self.adapter.core.advance(state,max(date(state['now']),now).isoformat(),trace)

    def ensure_overnight_schedule(self,state,now,trace):
        """Authored current-day routine while a full model plan is unavailable.

        Never replay yesterday as completed history or wake a sleeping model.
        Reuse a previously planned sleep location/outfit when available; the
        same reset baseline supplies the fallback home. Only current state may
        subsequently be changed by the usual idle/scene-clock reducer.
        """
        day=now.date().isoformat()
        if state.get('life_plan_day')==day:return
        if any(date(x['at']).astimezone(now.tzinfo).date()==now.date() for x in state['schedule']):return
        old=state['schedule']
        sleep=next((x for x in reversed(old) if x.get('availability')=='sleep'),{})
        wake=next((x for x in reversed(old) if x.get('availability')!='sleep' and 300<=x.get('start_minute',-1)<720),{})
        location=sleep.get('location') or ('青葉莊園臥室' if state.get('scene_background_world')=='original_world' else '住處房間')
        wake_minute=wake.get('start_minute',480)
        midnight=now.replace(hour=0,minute=0,second=0,microsecond=0)
        common={'location':location,'mood':'平靜','clothing':sleep.get('clothing') or '素色棉質睡衣、室內拖鞋',
                'accessories':'無','share_worthy':False,'arrival_activity':'','plan_source':'overnight_routine'}
        bridge=[dict(common,start='00:00',start_minute=0,at=midnight.isoformat(),activity='夜間休息',outcome='休息',availability='sleep'),
                dict(common,start=f'{wake_minute//60:02d}:{wake_minute%60:02d}',start_minute=wake_minute,
                     at=(midnight+timedelta(minutes=wake_minute)).isoformat(),activity='起床與晨間整理',outcome='開始新的一天',availability='free')]
        state['schedule']=old+bridge
        state['life_overnight_bridge']={'day':day,'source':'authored_routine','location':location,'wake_time':bridge[1]['start']}
        trace.add(1,'life.overnight_bridge',{'day':day},{'location':location,'wake_time':bridge[1]['start']},
                  '完整日程尚未產生；建立今天的睡眠／起床安排，不補造過去行動，不標成9B已規劃。')

    async def plan_day(self, state, now, trace, *, force=False):
        day=now.date().isoformat()
        if state.get('life_plan_day')==day:return
        if self.store.get('life_calendar_enabled','false')=='true':
            # Local table lookup is safe during quiet hours; no model wakes up.
            from .annual_schedule import install_day
            try:
                row=install_day(state,now)
                trace.add(1,'life.daily_plan',{'day':day},{'count':len(state['schedule']),'new_locations':0,'source':'annual_table','title':row['title']},
                          '從預先編好的年度表讀取計畫；事件草案不是已發生事實，不呼叫模型。')
                self.store.set('last_life_plan_failure','')
            except (ValueError,KeyError,TypeError,OSError) as exc:
                state['life_plan_failure']={'at':now.isoformat(),'reason':str(exc)[:160],'source':'annual_table'}
                self.store.set('last_life_plan_failure',json.dumps(state['life_plan_failure'],ensure_ascii=False))
                trace.add(1,'life.plan_failed',{}, {'error':'年度行程表讀取失敗'},'保留現有行程，不偷偷改用模型生成。')
            self.adapter.save_clock(state)
            return
        retry=state.get('life_plan_retry')
        if not force and retry and now<date(retry) and state.get('life_plan_attempt_version')=='brief-v2':return
        # No periodic model wakeups during quiet hours or image generation.
        if self.app.image_busy or (not force and in_quiet_hours(now,self.store.get('quiet_start'),self.store.get('quiet_end'))):return
        locations=SceneCatalogStore.from_state(self.store,state)
        messages=[{'role':'system','content':
            '替成年虛構角色安排一日生活，只輸出指定JSON。activities每項必填start/activity/location/outcome/mood/availability/share_worthy/clothing/accessories/arrival_activity，另必填new_locations陣列。'
            '安排7項，第一項00:00必須為臥室睡眠；最後一項22:00至23:59之間回臥室睡眠。依時間排序，涵蓋飲食、工作或興趣與休息。各文字欄位用短語，不寫散文；activity/outcome/mood/accessories最多24字，clothing最多60字。availability只用free/busy/sleep。'
            '依角色故事的年齡與職業安排；畢業的成年人不能自動安排中學課程。地點名稱一致且屬同一世界，衣著寫具體當下穿著，不能列偏好；睡眠只寫棉質睡衣上衣、睡褲，訓練寫運動上衣、運動長褲和運動鞋，不混入襯衫領帶或裙子。'
            'location是抵達地點；activity應寫在該地點從事的活動，不把移動當抵達後活動。若activity描述前往途中，arrival_activity必須填抵達後活動，其他情況填空字串。'
            'accessories只寫包、魔杖等裝備，不包含耳環戒指等飾品；沒有配件必須填「無」，不得留空。share_worthy最多3項，代表當時值得分享的活動；outcome是計畫目標，不能當已完成事件。不要安排使用者行為或共同經歷。'
            '優先重用location_options中的完整地名。可創造新地點，但每個未收錄的location必須在new_locations提供一次{name,environment,background}。'
            'environment只用indoor/outdoor/air/travel；background寫12至500字的純環境細節，描述空間、材質、地標與可見物，室內/戶外/空中要相符。'
            '只描述地點，不含人物、衣著、裸露、親密行為、口吻或鏡頭。已有地點不重寫背景，沒有新地點時new_locations=[]。'},
            {'role':'user','content':json.dumps({'date':day,'story':state['story'],'appearance_defaults':state.get('appearance_defaults',{}),
                'visual_world':state.get('visual_world',''),'current_location':state['scene']['location'],
                'location_options':locations.options()},ensure_ascii=False)}]
        try:
            state['life_plan_attempt_version']='brief-v2'
            state['life_plan_contract']='night-boundary-v1'
            raw=await self.app.llm.generate(messages,max_tokens=1800,response_schema=DAILY_PLAN_SCHEMA)
            if getattr(self.app.llm,'last_metrics',{}).get('finish_reason')=='length':raise ServiceError('日程生成未完成')
            items=parse_daily_plan(raw)
            if items[0]['start_minute']!=0 or items[0]['availability']!='sleep' or items[-1]['start_minute']<1320 or items[-1]['availability']!='sleep':
                raise ValueError('日程缺少00:00與22:00後的睡眠銜接')
            document=json.loads(raw)
            parsed={row['start']:row for row in document['activities']}
            from .scene_activity import is_travel
            for item in items:
                original=parsed.get(item['start'],{})
                # Arrival overrides belong only to actual travel activities.
                item['arrival_activity']=original.get('arrival_activity','').strip() if is_travel(item['activity']) else ''
                if (item['arrival_activity'] and is_travel(item['arrival_activity'])) or (is_travel(item['activity']) and not item['arrival_activity']):
                    raise ValueError('移動日程缺少抵達後活動')
            midnight=now.replace(hour=0,minute=0,second=0,microsecond=0)
            schedule=[dict(item,at=(midnight+timedelta(minutes=item['start_minute'])).isoformat()) for item in items]
            added=locations.register_plan(items,document.get('new_locations',[]),now.isoformat())
            state['scene_wardrobe_locations']=locations.wardrobe_locations()
            state['schedule']=schedule
            state['life_plan_day']=day;state['life_plan_retry']=''
            state.pop('life_plan_failure',None)
            trace.add(1,'life.daily_plan',{'day':day},{'count':len(items),'new_locations':added},'本地模型安排計畫並補新地點環境；背景登錄不代表抵達')
        except asyncio.CancelledError:raise
        except (ServiceError,ValueError,TypeError) as exc:
            state['life_plan_retry']=(now+timedelta(minutes=30)).isoformat()
            state['life_plan_failure']={'at':now.isoformat(),'reason':str(exc),'retry_at':state['life_plan_retry']}
            self.store.set('last_life_plan_failure',json.dumps(state['life_plan_failure'],ensure_ascii=False))
            trace.add(1,'life.plan_failed',{}, {'error':str(exc),'metrics':getattr(self.app.llm,'last_metrics',{})},'保留現有行程，30分鐘後才重試')
        self.adapter.save_clock(state)

    async def events(self,state,now,trace):
        """No foreground call: at most one due continuation per idle heartbeat."""
        def report(blocked=''):
            output=life_events.status(state,now)
            if blocked:output['blocked']=blocked
            trace.add(1,'life_event.status',{},output,'候選、已發生、未分享與等待條件分開記錄；不代表已傳訊。')
        if self.store.get('life_enabled')!='true' or self.store.get('story_events_enabled','true')!='true':report('生活事件已關閉');return
        if self.app.inbox_event.is_set():report('使用者新訊息優先');return
        if self.app.image_busy:report('先完成圖片工作');return
        if state.get('story_events_enabled'):life_events.sync_visit(state,now,trace)
        pace=self.store.get('story_event_pace','normal')
        if pace not in life_events.PACE:pace='normal'
        story=life_events.advance(state,now,pace,trace)
        self.adapter.save_clock(state)
        report()
        if not story or story['status']!='open' or now<date(story['next_at']) or story['attempts']>=3:return
        if not life_events.eligible(state,story):return
        if in_quiet_hours(now,self.store.get('quiet_start'),self.store.get('quiet_end')):report('安靜時段暫停模型續寫');return
        if not await self.app.llm.healthy():report('本地模型未就緒，等待續寫');return
        story['attempts']+=1
        story['next_at']=(now+timedelta(minutes=30)).isoformat()
        self.adapter.save_clock(state)
        original=self.store.get('pyramid_state')
        messages=[{'role':'system','content':life_events.STEP_RULES},
                  {'role':'user','content':json.dumps({
                      'current':{'location':story['location'],'activity':story['activity']},
                      'title':story['title'],'focus':story['focus'],
                      'committed':[{k:b[k] for k in ('at','text','outcome')} for b in story['beats']],
                      'contributions':[{k:c[k] for k in ('action','evidence')} for c in story['contributions'][-3:]],
                      'options':life_events.options(story),
                      'final':len(story['beats'])>=2},ensure_ascii=False)}]
        try:
            async with asyncio.timeout(30):
                raw=await self.app.llm.generate(messages,max_tokens=96,response_schema=life_events.step_schema(story))
            if getattr(self.app.llm,'last_metrics',{}).get('finish_reason')=='length':raise ServiceError('故事生成未完成')
            if self.app.inbox_event.is_set() or self.store.get('pyramid_state')!=original:
                trace.add(1,'life_event.stale',{}, {},'新訊息或狀態改變，捨棄未提交故事草稿。');return
            # It may have crossed an activity boundary while generation ran.
            current=self.app.now()
            if life_events.commit_step(state,story['id'],json.loads(raw),current,pace,trace):
                self.adapter.save_clock(state)
        except asyncio.CancelledError:raise
        except (TimeoutError,ServiceError,ValueError,TypeError):
            trace.add(1,'life_event.deferred',{}, {},'事件續寫未完成；保留正式故事，30分鐘後可重試，最多三次。')

    def outbound_status(self, identifier):
        row=self.store.db.execute('SELECT status FROM pyramid_outbound WHERE id=?',(identifier,)).fetchone()
        return row[0] if row else None

    async def send(self, state, trace, identifier, kind, text, prompt=None, decision=None, candidate=None):
        if self.outbound_status(identifier) in {'sending','uncertain','failed','delivered'}:return False
        now=self.app.now()
        if self.app.inbox_event.is_set() or self.gate(now,casual=kind=='casual'):return False
        if candidate:
            fresh=self.adapter.load()
            if (fresh['availability']=='sleep' or transitioning(fresh) or fresh['scene']!=state['scene']
                    or not StoryProactive().valid(fresh,candidate,now)):
                trace.add(5,'proactive.invalidated',{}, {},'故事或設定已變動，本次不傳送舊稿。')
                return False
        assistant_id=self.store.add_message('assistant',text,pending_delivery=True)
        with self.store.db:
            self.store.db.execute('INSERT OR REPLACE INTO pyramid_outbound VALUES(?,?,?,?,?,?,?)',
                (identifier,kind,'sending',now.isoformat(),None,assistant_id,
                 json.dumps({'subtype':candidate['subtype'],'thread':candidate['thread']} if candidate else {})))
        try:
            from .state_brief import with_state_brief
            from .external_info import footer
            await self.app.telegram.send(self.app.config.allowed_user,(with_state_brief(state,text)+footer((decision or {}).get('external_info'))) if kind=='casual' else text)
        except (asyncio.CancelledError,ServiceError):
            with self.store.db:self.store.db.execute("UPDATE pyramid_outbound SET status='uncertain' WHERE id=?",(identifier,))
            if candidate and candidate.get('subtype')=='life_event':
                life_events.disclosure(state,candidate,'uncertain');self.adapter.save_clock(state)
            if kind=='scene':
                before=scene_snapshot(state)
                state['scene']['pending']=None
                record_scene(state,'delivery_uncertain',before,'telegram_transport',self.app.now().isoformat())
                self.adapter.save_clock(state)
            raise
        # No await between receipt and durable checkpoint.
        sent=self.app.now().isoformat()
        life_events.disclosure(state,candidate,'delivered')
        delivery_context=message_context(state,sent,'mac_mini_system_clock')
        state['history'].append({'role':'assistant','content':text,'at':sent,'context':delivery_context,'turn_id':'background:'+identifier,'message_id':assistant_id,'background_kind':kind})
        if kind=='casual' and candidate:
            from .story_events import digest
            state['history'][-1]['_proactive']={'thread':candidate.get('thread',''),'digest':digest(state['history'][-1])}
        state['_last_message_id']=assistant_id
        if kind=='scene':
            self.adapter.core.acknowledge_scene_notice(state,identifier,sent,trace)
            delivery_context=with_scene_after(delivery_context,state)
            state['history'][-1]['context']=delivery_context
        else:state.setdefault('_pending_responses',[]).append({'reply':text,'turn_id':'background:'+identifier,'retain_memory':True,'at':sent,'context':delivery_context,'message_id':assistant_id,'decision':{k:v for k,v in (decision or {}).items() if k in ('primary','target_event','conditions','reply_brief','life_share')}})
        if kind!='scene':
            from .memory_tasks import baseline
            from .pyramid_runner import observer_snapshot
            state['_pending_responses'][-1].update(baseline=baseline(state),observer_snapshot=observer_snapshot(state,'assistant'))
            self.adapter.memory_tasks.sync(state['_pending_responses'])
        values={'pyramid_state':json.dumps(state,ensure_ascii=False),
                'relationship':json.dumps(state['relationship'],ensure_ascii=False)}
        if kind=='casual':values.update(last_proactive_at=sent,
            proactive_unanswered=str(int(self.store.get('proactive_unanswered','0'))+1))
        if prompt:
            snapshot=json.dumps(prompt,ensure_ascii=False,indent=2)
            values.update(last_prompt=snapshot,last_text_prompt=snapshot,last_prompt_kind='text',
                          last_text_prompt_assistant_id=str(assistant_id),last_strategy=json.dumps(decision,ensure_ascii=False))
        with self.store.db:
            self.store.db.execute("UPDATE messages SET delivery_state='delivered',recorded_at=?,context=? WHERE id=?",(sent,json.dumps(delivery_context,ensure_ascii=False),assistant_id))
            self.store.db.execute("UPDATE pyramid_outbound SET status='delivered',sent_at=? WHERE id=?",(sent,identifier))
            self.store.db.executemany('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',values.items())
        link('turn_id','background:'+identifier)
        link('assistant_id',assistant_id)
        trace.add(5,'background.delivered',{'kind':kind,'id':identifier},{'assistant_id':assistant_id},'Telegram確認送達；不把傳送本身當成使用者承接')
        return True

    async def scene_notice(self, state, trace):
        pending=state['scene'].get('pending')
        if not pending or pending.get('delivery')!='queued':return
        # A factual future-tense notice is rendered without waking a model or
        # allowing Writer wording to teleport the character before the deadline.
        from .journeys import departure_notice
        text=departure_notice(state,pending)
        await self.send(state,trace,pending['notice_id'],'scene',text)

    async def casual(self, state, now, trace):
        proposer=StoryProactive()
        pool=proposer.collect(state,now)
        self.adapter.save_clock(state)
        pending={p['turn_id'] for p in state.get('_pending_responses',[])}
        eligible=[];excluded=[]
        for candidate in pool:
            status=self.outbound_status(candidate['id'])
            if status in {'delivered','sending','uncertain','failed','skipped'}:
                excluded.append({'topic':candidate['topic'],'reason':'此聯絡理由已使用或傳送結果不明'});continue
            if candidate.get('source_index') is not None and state['history'][candidate['source_index']].get('turn_id') in pending:
                excluded.append({'topic':candidate['topic'],'reason':'等待相關回覆事實分析'});continue
            eligible.append(candidate)
        reason=self.gate(now,casual=True)
        if transitioning(state):reason='先完成換場通知與抵達，再接續一般主動話題'
        if proposer.contact_paused(state):reason='使用者已暫停主動聯絡'
        if self.app.image_busy:reason='先完成圖片工作'
        if state['availability']=='sleep':reason='角色正在睡眠'
        if self.app.inbox_event.is_set():reason='使用者新訊息優先'
        retry=self.store.get('pyramid_casual_retry_at')
        if retry and now<date(retry):reason=reason or '主動候選重試冷卻中'
        trace.add(4,'proactive.pool',{}, {'candidates':[{'topic':c['topic'],'subtype':c['subtype'],'actions':c['actions']} for c in eligible],
                  'excluded':excluded,'blocked':reason or ('' if eligible else '目前沒有新的可延續話題')},
                  '故事與生活分享共用候選池、頻率限制及同一次行為決策。')
        if reason or not eligible:return
        observation={'initiative':True,'asks_character':False,'missing_detail':'','appraisals':[],
                     'user_affect':{'status':'unknown','emotion':'','evidence':''},'proactive_candidates':eligible}
        decision=self.adapter.core.decide(state,[],observation,trace)
        trace.add(4,'background.selector',{'candidates':[c['topic'] for c in eligible]},decision,'同一候選行為評分；defer代表本次不主動聯絡')
        self.store.set('pyramid_casual_retry_at',(now+timedelta(minutes=30)).isoformat())
        if decision['primary']=='defer' or not decision.get('story_candidate'):return
        candidate=decision['story_candidate'];identifier=candidate['id']
        runner=PyramidRunner(self.app.llm,self.adapter.core,self.adapter.search,image_delivery=True,clock=self.app.now)
        runner.refresh_clock(state)
        decision['information_scope']='current_state'
        lang={'CH':'繁體中文','EN':'English','JP':'日本語'}[state['language']]
        system=(WRITER_VOICE+'本輪是角色主動發出的短訊，不是回答上一則使用者。依decision骨幹與delivery表達，'
                '只依定案詢問或分享選中故事，對方可選擇不回。過去說法不是現在狀態，計畫不是完成；不捏造使用者情緒或共同經歷，不抱怨沒回、不得生圖。'
                '成人mode與定案親密意圖不可提高；'+writer_length(state['language'],proactive=True)+'不揭露控制。只用'+lang+'。\n')
        messages=await runner.prepare_writer(state,decision,system,trace,output_tokens=256,
            control={'role':'user','content':'請撰寫本輪已決定的主動訊息。'})
        reply=await runner.call(trace,5,messages,256,temperature=.7)
        if not reply.strip():raise ServiceError('主動回文為空')
        if await self.send(state,trace,identifier,'casual',reply,messages,decision,candidate):
            self.store.set('pyramid_casual_retry_at','')
            trace.add(5,'response.scheduled',{}, {'pending':True},'主動短訊已送達，交由同一背景回文觀察路徑處理。')

    @diagnosed('life_heartbeat')
    async def tick(self):
        now=self.app.now()
        if self.next_tick and now<self.next_tick:return
        self.next_tick=now+timedelta(seconds=30)
        trace=DecisionTrace();state=self.adapter.load()
        self.advance(state,now,trace);self.adapter.save_clock(state)
        if self.store.get('life_enabled')=='true':
            await self.plan_day(state,now,trace)
            self.advance(state,self.app.now(),trace);self.adapter.save_clock(state)
            await self.events(state,self.app.now(),trace)
        if (not self.app.inbox_event.is_set() and not self.app.image_busy 
                and self.store.get('news_enabled','true')=='true' and self.store.get('proactive_enabled')=='true'
                and not in_quiet_hours(self.app.now(),self.store.get('quiet_start'),self.store.get('quiet_end'))):
            before_refresh=self.store.get('pyramid_state')
            await self.adapter.external.refresh(self.app.now())
            if self.app.inbox_event.is_set() or self.store.get('pyramid_state')!=before_refresh:
                trace.add(1,'external.stale',{}, {'status':'discarded','sources':0},'新聞取得期間有新訊息或狀態變更；不提交舊角色快照。')
                self.store.set('last_pyramid_background_trace',json.dumps(trace.nodes,ensure_ascii=False))
                return
            state['_news_candidates']=self.adapter.external.candidates(self.app.now())
        self.store.set('last_pyramid_heartbeat',self.app.now().isoformat())
        try:
            await self.scene_notice(state,trace)
            await self.casual(state,self.app.now(),trace)
        except asyncio.CancelledError:raise
        except (ServiceError,ValueError,TypeError):
            log.info('背景聯絡未完成；不重送結果不確定的訊息')
        finally:
            self.store.set('last_pyramid_background_trace',json.dumps(trace.nodes,ensure_ascii=False))
