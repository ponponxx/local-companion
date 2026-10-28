from .proactive_limits import parse_limit, limit_label, limit_reached
"""Telegram transport adapter for the same PyramidRunner used by Web.

State is durable in the existing private SQLite. No second decision policy.
Legacy background writers are suspended while this adapter is enabled.
"""
from copy import deepcopy
import asyncio
import json
import logging
import time
from .pyramid import PyramidCore, DecisionTrace, TurnInput, VERSION, date
from .pyramid_import import import_legacy_state
from .pyramid_memory import MemorySearch
from .memory_metadata import message_context, with_scene_after, scene_snapshot, record_scene
from .pyramid_runner import PyramidRunner
from .router import EmbeddingClient
from .services import ServiceError
from .diagnostics import diagnosed,event,link,record_state
from .state_brief import with_state_brief

log = logging.getLogger(__name__)
CATEGORIES = {'稱呼':'name','興趣':'interest','習慣':'habit','偏好':'preference','目標':'goal','界線':'boundary'}


class TelegramPyramid:
    def __init__(self, app):
        self.app, self.store = app, app.store
        from .external_info import ExternalInfo
        self.external=ExternalInfo(self.store)
        self.core = PyramidCore()
        self.embedder = EmbeddingClient(app.config.router_python, app.config.router_model_path)
        from .vector_cache import VectorCache, model_fingerprint
        fingerprint=model_fingerprint(app.config.router_model_path)
        self.search = MemorySearch(self.embedder,VectorCache(self.store.db,fingerprint) if fingerprint else None,foreground=True)
        from .memory_tasks import MemoryTasks
        self.memory_tasks=MemoryTasks(self.store.db)
        from .pyramid_life import PyramidLife
        self.life = PyramidLife(self)
        with self.store.db:
            self.store.db.execute('CREATE TABLE IF NOT EXISTS pyramid_turns(update_id INTEGER PRIMARY KEY, status TEXT NOT NULL, trace TEXT NOT NULL, decision TEXT NOT NULL, assistant_id INTEGER)')

    def load(self):
        raw = self.store.get('pyramid_state')
        state = json.loads(raw) if raw else import_legacy_state(self.store.db_path, self.app.now().isoformat(), full_history=True)
        from .journeys import migrate_legacy
        migrate_legacy(state)
        state['version']=VERSION
        state['memory_index_mode']='scene-topic-v1'  # User authorized rollout, including reset/import paths.
        state['story_events_enabled']=self.store.get('life_enabled')=='true' and self.store.get('story_events_enabled','true')=='true'
        # Administrative controls remain usable through the existing Telegram commands.
        state['story'] = self.store.persona()
        state['traits'] = self.store.traits()
        state['need_weights'] = self.store.need_weights()
        state['adult_traits'] = self.store.adult_intimacy()
        state['adult_policy'] = self.store.adult_policy()
        state['relationship'].update(self.store.relationship())
        state['language'] = {'English':'EN','Japanese':'JP'}.get(self.store.output_language(),'CH')
        state['manual_memories'] = self.store.memories()
        state['user_profile'] = [dict(f, id=str(f['id']),category=CATEGORIES.get(f['category'],f['category'])) for f in self.store.profile_facts()]
        appearance = self.store.appearance()
        previous = state.get('_base_appearance', appearance)
        if not raw:
            for key in ('hair','makeup','jewelry'):
                if not state['appearance'].get(key):state['appearance'][key] = appearance.get(key,'')
        state['appearance']['body'] = appearance['body']
        for field in ('clothing','accessories','jewelry','hair','makeup'):
            if appearance.get(field) != previous.get(field):
                if field == 'clothing':
                    state['appearance']['clothing'] = {}
                    state['appearance']['legacy_outfit'] = appearance[field]
                    state['appearance']['wardrobe_version'] = 2  # Explicit admin default change supersedes old arrival migration.
                else: state['appearance'][field] = appearance[field]
        state['appearance_defaults'] = {k: appearance.get(k, '') for k in ('hair','makeup','clothing','accessories','jewelry')}
        state['visual_world'] = appearance.get('visual_world', '')
        state['scene_background_world'] = self.store.get('scene_background_world','original_world')
        from .scene_catalog_store import SceneCatalogStore
        state['scene_wardrobe_locations']=SceneCatalogStore.from_state(self.store,state).wardrobe_locations()
        from .scene_appearance import migrate_arrival_clothing
        migrate_arrival_clothing(state)
        state['_base_appearance'] = deepcopy(appearance)
        from .character_baseline import initialize_character
        initialize_character(state)
        cap = {'off':0,'flirt':40,'intimate':100}[state['adult_policy']['mode']]
        state['intimacy']['heat'] = min(cap,state['intimacy']['heat'])
        if cap == 0: state['intimacy'].update(tension=0,desire=0,momentum=0,mutuality=0,receptivity='off')
        # Catch up confirmed messages outside the adapter (e.g. image queue dialogue).
        rows = self.store.db.execute("SELECT id,role,content,recorded_at,context FROM messages WHERE id>? AND delivery_state!='generated' AND memory_scope='chat' ORDER BY id",(state.get('_last_message_id',0),)).fetchall()
        for row in rows:
            state['history'].append({'role':row['role'],'content':row['content'],'at':row['recorded_at'],'context':json.loads(row['context'] or '{}'),'turn_id':'external:'+str(row['id']),'message_id':row['id']})
            state['_last_message_id'] = row['id']
        state['_news_candidates']=self.external.candidates(self.app.now())
        # messages owns source text and delivery; history is an annotation/cache view.
        for h in state['history']:
            if h.get('message_id') is None:
                matches=self.store.db.execute('SELECT id FROM messages WHERE role=? AND content=? AND recorded_at=?',(h['role'],h['content'],h.get('at',''))).fetchall()
                if len(matches)==1:h['message_id']=matches[0]['id']
            if h.get('message_id') is not None:
                source=self.store.db.execute('SELECT content,delivery_state,memory_scope FROM messages WHERE id=?',(h['message_id'],)).fetchone()
                if source is None or source['delivery_state']=='generated' or source['memory_scope']!='chat':h['_memory_hidden']=True
                else:h['content']=source['content'];h.pop('_memory_hidden',None)
        from .memory_sources import bind
        bind(state)
        self.memory_tasks.sync(state.get('_pending_responses',[]))
        return state

    def note_user_activity(self):
        state=self.load();trace=DecisionTrace()
        stamp=self.app.now().isoformat()
        self.core.user_arrived(state,trace,at=stamp)
        self.save_clock(state)

    @diagnosed('memory_maintenance')
    async def maintain_story(self):
        """One bounded idle batch; inbox_worker owns serialization/cancellation."""
        if self.app.image_busy or self.app.inbox_event.is_set():return
        retry=self.store.get('pyramid_story_retry_at')
        if retry and self.app.now()<date(retry):return
        original=self.store.get('pyramid_state')
        state=self.load()
        from .story_core import StoryCore
        try:
            await StoryCore().maintain(state,self.search,DecisionTrace())
        except asyncio.CancelledError:raise
        except Exception:log.warning('背景向量暖機未完成；原文與關鍵字檢索仍可用')
        if len(state['history'])-state['summary_through']<=12:return
        stamps=[h.get('at') for h in state['history'][-2:] if h.get('at')]
        if not stamps or (self.app.now()-max(date(at) for at in stamps)).total_seconds()<60:return
        # Health checking must not call ensure/start and wake an unloaded model.
        if not await self.app.llm.healthy():return
        from datetime import timedelta
        self.store.set('pyramid_story_retry_at',(self.app.now()+timedelta(minutes=2)).isoformat())
        trace=DecisionTrace()
        runner=PyramidRunner(self.app.llm,self.core,self.search,clock=self.app.now)
        try:
            async with asyncio.timeout(15):
                await runner.compact(state,trace,max_batches=1,recall=False)
            if self.app.inbox_event.is_set() or self.store.get('pyramid_state')!=original:return
            # Only this serialized idle operation commits; never save on cancellation.
            self.save_clock(state)
        except TimeoutError:
            trace.add(1,'story.background_deferred',{}, {},
                      '背景整理超過15秒，本批未提交；原文保留，下次空閒再試。')
        except (ServiceError,ValueError,TypeError):
            trace.add(1,'story.background_deferred',{}, {},'背景整理未完成，本批未提交；原文仍可使用。')
        finally:
            self.store.set('last_story_background_trace',json.dumps(trace.nodes,ensure_ascii=False))

    def save_clock(self, state):
        # A time tick must not rewrite Profile evidence timestamps.
        self.store.set('pyramid_state',json.dumps(state,ensure_ascii=False))
        record_state(state,'clock')

    def has_response_work(self):
        state=self.load()
        return self.memory_tasks.eligible(state.get('_pending_responses',[]),self.app.now()) is not None

    @diagnosed('deferred_response')
    async def maintain_responses(self):
        if self.app.image_busy or self.app.inbox_event.is_set():return
        state=self.load();pending=self.memory_tasks.eligible(state.get('_pending_responses',[]),self.app.now())
        if not pending or not await self.app.llm.healthy():return
        trace=DecisionTrace();reporter=None
        if self.store.get('telegram_debug_enabled','false')=='true' and self.store.get('telegram_debug_mode')=='flow' and pending.get('chat_id') is not None:
            from .telegram_debug import DebugReporter,DebugTrace
            reporter=DebugReporter(self.app.telegram,pending['chat_id'],'回文後觀察 '+pending['turn_id'],mode='flow');trace=DebugTrace(reporter)
        runner=PyramidRunner(self.app.llm,self.core,self.search,clock=self.app.now)
        try:
            async with asyncio.timeout(45):
                await self.recover_response(state,runner,trace,background=True,pending=pending)
        except TimeoutError:
            self.memory_tasks.fail(pending,self.app.now(),'回應後分析逾時')
            trace.add(5,'memory.task_timeout',{},self.memory_tasks.overview(),'整理逾時只延後該工作，不阻止新聊天。')
        finally:
            self.store.set('last_response_background_trace',json.dumps(trace.nodes,ensure_ascii=False))
            if reporter:await reporter.close()

    def save(self, state):
        # Profile ids remain the ids accepted by /profile update/delete.
        with self.store.db:
            existing={r['id'] for r in self.store.db.execute('SELECT id FROM user_profile_facts')}
            keep=set()
            for f in state['user_profile']:
                identifier=int(f['id']) if str(f.get('id','')).isdigit() and int(f['id']) in existing else None
                category={v:k for k,v in CATEGORIES.items()}.get(f['category'],f['category'])
                args=(category,f['value'],f.get('confidence',.55),f.get('source','pyramid'),state['now'])
                if identifier is None:
                    identifier=self.store.db.execute('INSERT INTO user_profile_facts(category,value,confidence,source,updated_at,created_at) VALUES(?,?,?,?,?,?)',args+(state['now'],)).lastrowid
                else:
                    self.store.db.execute('UPDATE user_profile_facts SET category=?,value=?,confidence=?,source=?,updated_at=? WHERE id=?',args+(identifier,))
                f['id']=str(identifier);keep.add(identifier)
            for identifier in existing-keep:self.store.db.execute('DELETE FROM user_profile_facts WHERE id=?',(identifier,))
            payloads={'pyramid_state':state,'relationship':state['relationship']}
            for key,value in payloads.items():
                self.store.db.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,json.dumps(value,ensure_ascii=False)))
        self.store._write_profile_file()
        record_state(state,'full')

    def record(self, update_id, status, trace, decision=None, assistant_id=None):
        link('turn_id','tg:'+str(update_id))
        link('assistant_id',assistant_id)
        event('turn.checkpoint',{'update_id':update_id,'status':status,'assistant_id':assistant_id,'decision':decision})
        with self.store.db:
            self.store.db.execute('INSERT OR REPLACE INTO pyramid_turns VALUES(?,?,?,?,?)',
                (update_id,status,json.dumps(trace.nodes,ensure_ascii=False),json.dumps(decision or {},ensure_ascii=False),assistant_id))
            self.store.db.execute("INSERT INTO settings VALUES('last_pyramid_trace',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(trace.nodes,ensure_ascii=False),))

    async def command(self, command, argument, chat_id):
        if command=='/outfit':
            from .visual_state import SLOT_NAMES,current_appearance,is_removed
            from .scene_appearance import arrival_clothing
            state=self.load();parts=argument.strip().split(maxsplit=1)
            stamp=self.app.now().isoformat()
            if not parts:
                await self.app.settings_ui.show(chat_id,'current_clothing')
                return True
            if parts and parts[0]=='reset':
                if len(parts)!=1:raise ValueError('用法：/outfit reset')
                clothes,_=arrival_clothing(state)
                if not clothes:
                    clothes,_=arrival_clothing(state,{'clothing':state.get('appearance_defaults',{}).get('clothing','')})
                if not clothes:raise ValueError('目前地點沒有預設穿搭；請逐項設定當前穿著。')
                from .character_baseline import DEFAULT_CLOTHING
                clothes={**deepcopy(DEFAULT_CLOTHING),**clothes}
                state['appearance'].update(clothing=clothes,legacy_outfit='',wardrobe_version=2)
                state.setdefault('_clothing_edits',{}).update({k:stamp for k in SLOT_NAMES})
                self.save_clock(state)
            elif parts:
                aliases={v:k for k,v in SLOT_NAMES.items()}
                slot=aliases.get(parts[0],parts[0])
                if slot not in SLOT_NAMES or len(parts)!=2 or not 1<=len(parts[1])<=160:
                    raise ValueError('用法：/outfit top 白色襯衫；可用外衣、上衣、下裝、連身衣裙、內衣、襪子、鞋子、衣著配件；內容限160字。')
                value=parts[1].strip()
                worn=None if value in {'未確認','unknown'} else not is_removed(value)
                state['appearance'].setdefault('clothing',{})[slot]={'description':value,'worn':worn,'source':'manual_setting'}
                state.setdefault('_clothing_edits',{})[slot]=stamp
                self.save_clock(state)
            current=current_appearance(state['appearance'])['clothing']
            lines=['當前穿著（只修改現況，不改預設）：']+[SLOT_NAMES[k]+'：'+v for k,v in current.items()]
            lines.append('用 /outfit 開啟部位按鈕，再輸入衣物內容；不用輸入「上衣：」等欄位名稱。/outfit reset 依當前地點恢復預設穿搭。')
            await self.app.telegram.send(chat_id,'\n'.join(lines));return True
        if command in {'/web','/news','/search'}:
            from .external_info import TOPICS,reading,footer
            value=argument.strip()
            if command=='/search':
                if not value:raise ValueError('用法：/search 公開主題詞（不要輸入私人資訊）')
                info=await self.external.query({'reason':'explicit','query':value,'freshness':'week'},self.app.now())
                self.store.set('last_external_result',json.dumps(info,ensure_ascii=False))
                await self.app.telegram.send(chat_id,reading(info)+footer(info));return True
            if command=='/web':
                if value not in {'','status','on','off'}:raise ValueError('用法：/web on|off|status')
                if value in {'on','off'}:self.store.set('web_enabled','true' if value=='on' else 'false')
                text='公開資料：'+('開啟' if self.external.enabled else '關閉')+'\n一般搜尋：'+('Brave已設定' if self.external.search_configured else '尚未設定；目前只有訂閱新聞摘要')
            else:
                if value in {'on','off'}:self.store.set('news_enabled','true' if value=='on' else 'false')
                elif value.startswith('topics '):
                    topics=list(dict.fromkeys(value[7:].replace('，',',').replace(' ',',').split(',')))
                    topics=[t for t in topics if t]
                    if not topics or any(t not in TOPICS for t in topics):raise ValueError('主題：technology（科技）、space（太空），可用逗號分隔')
                    self.store.set('news_topics',','.join(topics))
                elif value not in {'','status'}:raise ValueError('用法：/news on|off|status；/news topics technology,space')
                text='主動新聞：'+('開啟' if self.store.get('news_enabled','true')=='true' else '關閉')+'\n訂閱：'+', '.join(TOPICS[t] for t in self.external.topics())+'\n沿用既有主動冷卻、安靜時間及額度；不是有新聞就傳。'
            await self.app.telegram.send(chat_id,text);return True
        if command=='/storyevents':
            from . import life_events
            option=argument.strip().lower()
            if option not in {'','status','on','off','slow','normal','fast'}:
                raise ValueError('用法：/storyevents、/storyevents on|off、/storyevents slow|normal|fast')
            if option in {'on','off'}:self.store.set('story_events_enabled','true' if option=='on' else 'false')
            if option in life_events.PACE:self.store.set('story_event_pace',option)
            state=self.load();book=state.get('life_events',{})
            pace=self.store.get('story_event_pace','normal')
            lines=['生活小事件：'+('開啟' if self.store.get('story_events_enabled','true')=='true' else '關閉'),
                   '進展間隔：'+str(life_events.PACE.get(pace,90))+'分鐘（最早可評估時間，不是準時通知）',
                   '事件與主動訊息頻率分開；/proactive 查看傳訊限制。',
                   '尚未發生草案：'+str(len(book.get('drafts',[])))]
            recent=book.get('stories',[])[-1:]
            lines.extend(life_events.reading(s) for s in recent)
            report=life_events.status(state,self.app.now())
            lines.append('本次場景規劃：'+str(report['planned'])+' 項；已發生未分享：'+str(report['unshared'])+' 項。')
            if report['blocked']:lines.append('等待原因：'+report['blocked'])
            if not recent:lines.append('尚無正式事件；確認抵達新場景後規劃1至2項，候選不等於已發生。')
            lines.append('設定：on／off；slow 180、normal 90、fast 45 分鐘。')
            await self.app.telegram.send(chat_id,'\n'.join(lines));return True
        if command=='/debug':
            option=argument.strip().lower()
            if option=='records' or option.startswith('record '):
                archive=getattr(self.app,'diagnostics',None)
                if not archive:
                    await self.app.telegram.send(chat_id,'本機診斷資料庫尚未啟用。');return True
                if option=='records':
                    rows=archive.recent()
                    text='本機診斷記錄（不進角色記憶）：\n'+'\n'.join(f"#{r['id']}｜{r['kind']}｜{r['status']}｜{r['started_at']}" for r in rows)
                    text+='\n/debug record 編號 查看完整Prompt／輸出與事件；資料庫寫入錯誤數：'+str(archive.errors)
                else:
                    value=option.split(maxsplit=1)[1]
                    if not value.isdigit():raise ValueError('用法：/debug record 數字編號')
                    text=archive.report(int(value))
                from .telegram_debug import message_chunks
                for part in message_chunks(text):await self.app.telegram.send(chat_id,part)
                return True
            if option not in {'','on','off','last','proactive','flow','flow last'}:raise ValueError('用法：/debug flow、/debug flow last、/debug on、/debug off、/debug last、/debug proactive')
            if option in {'last','proactive','flow last'}:
                from .telegram_debug import DebugReporter
                nodes=json.loads(self.store.get('last_pyramid_background_trace' if option=='proactive' else 'last_pyramid_trace','[]'))
                if not nodes:
                    await self.app.telegram.send(chat_id,'尚無可讀取的判斷流程。');return True
                reporter=DebugReporter(self.app.telegram,chat_id,'主動聯絡檢查' if option=='proactive' else '上一輪重看',mode='flow' if option=='flow last' else self.store.get('telegram_debug_mode','detail'))
                try:
                    for node in nodes:reporter.feed(node)
                finally:await reporter.close()
                return True
            if option=='flow':
                self.store.set('telegram_debug_mode','flow')
                self.store.set('telegram_debug_enabled','true')
                await self.app.telegram.send(chat_id,'流程觀測已開啟：顯示各階段Input／Output與實際Prompt，省略候選分數與計算。模型輸出完成後才顯示該次輸入輸出；長內容會分頁。回文後觀察完成時另補送。\n/debug flow last 重看；/debug on 切回詳細診斷；/debug off 關閉。不增加模型呼叫，不寫進角色記憶。')
                return True
            if option=='on':self.store.set('telegram_debug_mode','detail')
            if option:self.store.set('telegram_debug_enabled','true' if option=='on' else 'false')
            enabled=self.store.get('telegram_debug_enabled','false')=='true'
            await self.app.telegram.send(chat_id,('系統 Debug 已開啟：接下來每輪依序顯示判讀、驗證、需求、候選評分、口吻與回覆核對。' if enabled else '系統 Debug 已關閉。')+'\n/debug on 開啟；/debug off 關閉；/debug last 重看上一輪。診斷不寫入角色記憶，不增加模型呼叫。')
            return True
        if command in {'/life','/proactive'}:
            if argument:
                if argument.lower() not in {'on','off'}:raise ValueError('請選擇 on 或 off')
                self.store.set('life_enabled' if command=='/life' else 'proactive_enabled','true' if argument.lower()=='on' else 'false')
            state=self.load()
            if command=='/proactive' and argument.lower()=='on':
                state.setdefault('story_proactive',{'threads':{},'controls':[]})['resume_after']=self.app.now().isoformat()
                self.save_clock(state)
            lines=[f"模擬生活：{'開啟' if self.store.get('life_enabled')=='true' else '關閉'}",
                   f"主動訊息：{'開啟' if self.store.get('proactive_enabled')=='true' else '關閉'}",
                   f"最近心跳：{self.store.get('last_pyramid_heartbeat') or '等待首次執行'}",
                   f"今日主動聊天：{self.life.count_today(self.app.now())}/{limit_label(self.store.get('proactive_daily_limit'))}（移動告知不計額度）",
                   f"安靜時段：{self.store.get('quiet_start')}–{self.store.get('quiet_end')}",
                   f"主動聊天閒置：{self.store.get('proactive_min_silence_minutes')} 分鐘；冷卻：{self.store.get('proactive_cooldown_minutes')} 分鐘"]
            if command=='/proactive':
                lines.append('故事主動推進：已接入；/debug proactive 查看候選與等待原因。')
            if command=='/life':
                lines.extend(f"{date(item['at']).astimezone(self.app.zone):%H:%M} {item.get('activity','')}｜{item['location']}" for item in state['schedule'])
            pending=state['scene'].get('pending')
            if pending:lines.append('待切換：'+pending['location']+'；'+('通知已送達，等待十分鐘' if pending.get('deadline') else '等待通知送達'))
            await self.app.telegram.send(chat_id,'\n'.join(lines));return True
        if command not in {'/state','/scene','/memory'} and not (command=='/adult' and argument.lower()=='state'):
            return False
        state=self.load()
        if command=='/state':
            view={k:state[k] for k in ('now','emotions','energy','stress','activity','availability','scene','appearance','intimacy')}
            await self.app.telegram.send(chat_id,'新核心狀態（上次提交快照）：\n'+json.dumps(view,ensure_ascii=False,indent=2))
        elif command=='/scene':
            if argument=='history':
                from .pyramid_memory import catalogue
                docs=[d for d in catalogue(state) if d['kind']=='scene_event'][-10:]
                lines=[f"{d['at']}｜{d['text']}" for d in docs]
                await self.app.telegram.send(chat_id,'最近場景事件：\n'+('\n'.join(lines) or '尚未記錄場景歷史。'));return True
            if argument:
                if argument!='close':raise ValueError('用法：/scene、/scene history 或 /scene close')
                from .pyramid import initial_state
                before=scene_snapshot(state)
                state['scene']=initial_state()['scene'];state['presentation']={}
                record_scene(state,'closed',before,'admin_command',self.app.now().isoformat())
                self.save(state)
            await self.app.telegram.send(chat_id,json.dumps(state['scene'],ensure_ascii=False,indent=2))
        elif command=='/adult':
            await self.app.telegram.send(chat_id,'新核心親密動態狀態：\n'+json.dumps(state['intimacy'],ensure_ascii=False,indent=2))
        else:
            lines=[f"#{m['id']}：{m['content']}" for m in self.store.memories()]
            mem=state['memory'];selection=mem.get('selection',{})
            lines += [f"觀察筆記：{len(mem['notes'])} 筆；未整理原文：{len(state['history'])-state['summary_through']} 則。",
                      f"最近檢索候選：{len(selection.get('items',[]))} 筆（實際各階段取用見 /debug last）；檢索：{selection.get('mode','尚未執行')}。"]
            lines += [f"場景事件：{len(state.get('scene_history',[]))} 筆。"]
            lines += [f"{n.get('at') or '時間未記錄'}｜{n.get('context',{}).get('scene',{}).get('location') or '場景未記錄'}｜{'重要｜' if n['important'] else ''}{n['actor']}：{n['text']}" for n in mem['notes'][-3:]]
            await self.app.telegram.send(chat_id,'\n'.join(lines))
        return True

    async def recover_response(self,state,runner,trace,background=False,pending=None):
        pending=pending or next(iter(state.get('_pending_responses',[])),None)
        if not pending:return True
        self.memory_tasks.sync([pending])
        from .memory_sources import excluded
        origin=next(((i,h) for i,h in enumerate(state['history']) if h['role']=='assistant' and h.get('turn_id')==pending['turn_id']),None)
        if origin and excluded(state,origin[1],origin[0]):
            async with self.app.control_lock:
                current=self.load();current['_pending_responses']=[p for p in current.get('_pending_responses',[]) if p['turn_id']!=pending['turn_id']]
                self.save(current);self.memory_tasks.finish(pending,'annotation_only');state.clear();state.update(current)
            trace.add(5,'memory.forgotten_task',{}, {'model_called':False},'來源已停用；只保留稽核紀錄，不再送模型或補狀態。')
            return True
        link('turn_id',pending.get('turn_id'));link('assistant_id',pending.get('message_id'))
        event('response.origin',{'turn_id':pending.get('turn_id'),'message_id':pending.get('message_id')})
        started=time.monotonic();previous=(runner.response_request,runner.response_decision,getattr(runner,'response_snapshot',None))
        runner.response_request=pending.get('request') or next((h['content'] for h in state['history'] if h['role']=='user' and h.get('turn_id')==pending['turn_id']),'')
        runner.response_decision=pending.get('decision',{});runner.response_snapshot=pending.get('observer_snapshot')
        try:
            # Model work owns no state lock. Foreground arrival may cancel it.
            result=await runner.observe(deepcopy(state),pending['reply'],'assistant',trace)
            async with self.app.control_lock:
                latest=self.load()
                current=next((p for p in latest.get('_pending_responses',[]) if p['turn_id']==pending['turn_id']),None)
                if current is None:return True  # Another completed commit already consumed it.
                row=self.store.db.execute('SELECT content FROM messages WHERE id=?',(pending.get('message_id'),)).fetchone() if pending.get('message_id') else None
                if row is not None and row[0]!=pending['reply']:raise ValueError('待整理原文已更動，不套用舊分析')
                if pending.get('message_id') and row is None:raise ValueError('待整理來源已刪除，不重新建立記憶')
                from .memory_sources import excluded
                message=next((h for h in latest['history'] if h['role']=='assistant' and (pending.get('message_id') is not None and h.get('message_id')==pending['message_id'] or h.get('turn_id')==pending['turn_id'])),None)
                hidden=message is not None and excluded(latest,message,latest['history'].index(message))
                from .memory_tasks import rebase
                result=rebase(result,pending,latest,trace)
                if not hidden:
                    _,message=runner.commit_response(latest,pending['reply'],result,pending['turn_id'],pending.get('decision',{}),trace,
                        pending.get('at') or latest['now'],pending.get('context') or message_context(latest,pending.get('at') or latest['now']),
                        retain=pending.get('retain_memory',True),message=message,message_id=pending.get('message_id'))
                    if message and message.get('message_id'):self.store.set_message_context(message['message_id'],message['at'],message['context'])
                latest['_pending_responses']=[p for p in latest.get('_pending_responses',[]) if p['turn_id']!=pending['turn_id']]
                self.save(latest)
                trace.add(5,'telegram.response_recovered',{}, {'turn_id':pending['turn_id'],'historical_only':not pending.get('baseline')},'補整理已送達原文，不重送、不倒退較新現況。')
                saved=self.store.db.execute('SELECT update_id,trace,decision FROM pyramid_turns WHERE assistant_id=?',(pending.get('message_id'),)).fetchone()
                if saved:
                    prior=json.loads(saved['trace'])+trace.nodes
                    self.record(saved['update_id'],'done',type('RecoveredTrace',(),{'nodes':prior})(),json.loads(saved['decision']),pending.get('message_id'))
                self.memory_tasks.finish(pending,'annotation_only' if hidden or not pending.get('baseline') else 'done')
                state.clear();state.update(latest)
                return True
        except asyncio.CancelledError:raise
        except Exception as exc:
            self.memory_tasks.fail(pending,self.app.now(),str(exc))
            trace.add(5,'telegram.response_deferred',{}, {'error':type(exc).__name__,'message':str(exc),'tasks':self.memory_tasks.overview()},'待辦保留；新聊天沿用最後確認狀態，不等待這筆整理。')
            return False
        finally:
            runner.response_request,runner.response_decision,runner.response_snapshot=previous
            trace.add(5,'pipeline.response_wait',{}, {'background':background},'背景整理耗時；不作為新聊天的前置依賴。',seconds=round(time.monotonic()-started,3))

    async def handle(self, update_id, chat_id, text, photo_request=False, queue_seconds=None):
        if self.store.get('telegram_debug_enabled','false')!='true':
            return await self._handle(update_id,chat_id,text,photo_request,queue_seconds=queue_seconds)
        from .telegram_debug import DebugReporter, DebugTrace
        reporter=DebugReporter(self.app.telegram,chat_id,str(update_id),mode=self.store.get('telegram_debug_mode','detail'))
        trace=DebugTrace(reporter)
        try:
            return await self._handle(update_id,chat_id,text,photo_request,trace,reporter,queue_seconds)
        finally:
            await reporter.close()

    async def _handle(self, update_id, chat_id, text, photo_request=False, trace=None, debug=None, queue_seconds=None):
        started=time.monotonic()
        user_at=self.app.now().isoformat()
        state=self.load();trace=trace or DecisionTrace()
        trace.add(1,'pipeline.queue',{}, {'seconds':queue_seconds},'持久inbox入列到開始處理；舊資料沒有時間則標示未知。')
        search=MemorySearch() if self.app.image_busy else self.search
        runner=PyramidRunner(self.app.llm,self.core,search,image_delivery=True,clock=self.app.now,external=self.external)
        if state.get('_pending_responses'):
            trace.add(1,'memory.pending_nonblocking',{},self.memory_tasks.overview(),'舊分析不阻塞聊天；原文可接話，正式狀態仍以最後確認資料為準。')
        self.life.advance(state,self.app.now(),trace)
        exclude_from_memory=bool(photo_request)
        input_context=message_context(state,user_at,'mac_mini_system_clock')
        user_id=self.store.add_message('user',text,memory_scope='tool' if exclude_from_memory else 'chat',at=user_at,context=input_context)
        state['_last_message_id']=user_id
        checkpoint=None;delivered=False;assistant_id=None;decision={};answer=''
        turn_id='tg:'+str(update_id)

        async def user_state(current, plan, active_trace):
            nonlocal checkpoint,decision,exclude_from_memory
            exclude_from_memory=not plan['memory_retained']
            if exclude_from_memory:
                with self.store.db:self.store.db.execute("UPDATE messages SET memory_scope='tool' WHERE id=?",(user_id,))
            entry=(runner.transient_dialogue if exclude_from_memory else current['history'])[-1]
            entry['message_id']=user_id;entry['source_ref']='message:'+str(user_id)
            self.store.set_message_context(user_id,entry['at'],entry['context'])
            decision=deepcopy(plan);self.save(current);checkpoint=deepcopy(current)
            self.record(update_id,'user_committed',active_trace,plan)

        async def reply_ready(current, plan, reply, active_trace):
            nonlocal checkpoint,delivered,assistant_id,answer
            writer=next(n for n in reversed(active_trace.nodes) if n['rule']=='model.request' and n['layer']==5)
            prompt=json.dumps(writer['inputs']['messages'],ensure_ascii=False,indent=2)
            assistant_id=self.store.add_message('assistant',reply,pending_delivery=True,memory_scope='tool' if exclude_from_memory else 'chat')
            if debug:await debug.flush()
            from .external_info import footer
            await self.app.telegram.send(chat_id,with_state_brief(current,reply)+footer(plan.get('external_info')))
            delivered=True;answer=reply
            active_trace.add(5,'pipeline.reply_delivered',{}, {},'從本輪開始處理到Telegram確認送達；不含進入處理前的佇列等待。',seconds=round(time.monotonic()-started,3))
            delivered_at=self.app.now().isoformat()
            delivery_context=message_context(current,delivered_at,'mac_mini_system_clock')
            self.store.mark_delivered(assistant_id)
            self.store.set_message_context(assistant_id,delivered_at,delivery_context)
            checkpoint=deepcopy(current)
            if not exclude_from_memory:
                checkpoint['history'].append({'role':'assistant','content':reply,'at':delivered_at,'context':delivery_context,'turn_id':turn_id,'message_id':assistant_id})
            checkpoint['_last_message_id']=assistant_id
            checkpoint.setdefault('_pending_responses',[]).append({'reply':reply,'turn_id':turn_id,'chat_id':chat_id,'retain_memory':not exclude_from_memory,'request':text if exclude_from_memory else '', 'decision':{k:deepcopy(plan[k]) for k in ('primary','target_event','conditions','reply_brief','life_share') if k in plan}, 'at':delivered_at,'context':delivery_context,'message_id':assistant_id})
            from .memory_tasks import baseline
            from .pyramid_runner import observer_snapshot
            checkpoint['_pending_responses'][-1].update(baseline=baseline(checkpoint),observer_snapshot=observer_snapshot(checkpoint,'assistant'))
            self.save(checkpoint)
            self.memory_tasks.sync(checkpoint['_pending_responses'])
            for key,value in {'last_prompt':prompt,'last_text_prompt':prompt,'last_prompt_kind':'text','last_prompt_image_id':'',
                              'last_text_prompt_assistant_id':str(assistant_id),'last_strategy':json.dumps(plan,ensure_ascii=False),
                              'last_metrics':json.dumps(writer['output']['metrics'],ensure_ascii=False)}.items():self.store.set(key,value)
            self.record(update_id,'delivered_pending_analysis',active_trace,plan,assistant_id)
            return {'at':delivered_at,'context':delivery_context,'message_id':assistant_id}

        try:
            result=await runner.run(state,TurnInput(text,turn_id,True,at=user_at),trace,on_user_state=user_state,on_reply=reply_ready,retain_turn=not photo_request,analyze_response=False)
            if any(n['rule']=='response.scheduled' for n in trace.nodes):
                decision=result.decision;answer=result.reply
                trace.add(5,'debug.finished',{}, {'status':'deferred'},'文字已送達；回應後觀察交給優先背景工作。')
                self.record(update_id,'delivered_pending_analysis',trace,decision,assistant_id)
                return answer,decision
            state=result.after;state['_last_message_id']=assistant_id
            state['_pending_responses']=[p for p in state.get('_pending_responses',[]) if p['turn_id']!=turn_id]
            if not exclude_from_memory:
                h=state['history'][-1]
                self.store.set_message_context(assistant_id,h['at'],h['context'])
            self.save(state);decision=result.decision;answer=result.reply
            self.store.set('last_strategy',json.dumps(decision,ensure_ascii=False,indent=2))
            trace.add(5,'debug.finished',{}, {'status':'done'},'傳輸與分析完成')
            self.record(update_id,'done',trace,decision,assistant_id)
        except asyncio.CancelledError:
            trace.add(5,'debug.finished',{}, {'status':'cancelled'},'工作取消')
            if checkpoint:self.save(checkpoint)
            else:
                if not exclude_from_memory:
                    state['history'].append({'role':'user','content':text,'at':user_at,'context':input_context,'turn_id':turn_id,'message_id':user_id})
                self.save(state)
            self.record(update_id,'delivered_pending_analysis' if delivered else 'cancelled',trace,decision,assistant_id)
            raise
        except Exception as exc:
            trace.add(5,'debug.finished',{}, {'status':'deferred' if delivered else 'failed','error_type':type(exc).__name__},'保留已提交狀態；不偽造完成')
            if checkpoint:self.save(checkpoint)
            else:
                if not exclude_from_memory:
                    state['history'].append({'role':'user','content':text,'at':user_at,'context':input_context,'turn_id':turn_id,'message_id':user_id})
                self.save(state)
            self.record(update_id,'delivered_pending_analysis' if delivered else 'failed',trace,decision,assistant_id)
            if not delivered:raise
            log.warning('文字已送達；回應後分析延後，不重送或清除回文')
        return answer,decision

    def visual_snapshot(self, kind=None):
        state=self.load();appearance=state['appearance'];scene=state['scene']
        from .visual_state import clothing_description,current_appearance
        appearance=current_appearance(appearance)
        clothing=clothing_description(state['appearance'])
        # Fresh, same-place source dialogue for composition only. Never reuse
        # yesterday's tea/book context after a move or a long gap.
        photo_dialogue=[]
        from datetime import timedelta
        location_now=scene.get('character_location') or scene.get('location')
        for row in reversed(self.store.db.execute("SELECT role,content,recorded_at,context FROM messages WHERE delivery_state!='generated' ORDER BY id DESC LIMIT 12").fetchall()):
            try:
                at=date(row['recorded_at']);context=json.loads(row['context'] or '{}')
                historical=context.get('scene_after') or context.get('scene') or {}
                where=historical.get('character_location') or historical.get('location')
                if where==location_now and timedelta(0)<=self.app.now()-at<=timedelta(minutes=20):
                    photo_dialogue.append({'role':row['role'],'content':row['content'][:600]})
            except (ValueError,TypeError):continue
        photo_dialogue=photo_dialogue[-6:]
        facts={'character.hair':appearance['hair'],'character.clothing':clothing,'character.accessories':appearance['accessories']}
        facts.update({'character.'+k:v.get('value','') for k,v in state['presentation'].items() if k in {'pose','expression'}})
        location=(scene.get('character_location') or scene.get('location')) if kind=='user_photo' else (scene.get('location') or scene.get('character_location'))
        location=location or '地點未確認'
        from .scene_catalog_store import SceneCatalogStore
        background=SceneCatalogStore.from_state(self.store,state).resolve(location)
        from .visual_lighting import snapshot_lighting, timeless_background
        lighting=snapshot_lighting(self.app.now(),background)
        background=timeless_background(background)
        world=state.get('visual_world','')
        import re
        if re.search(r'臥室|寢室|bedroom|bedchamber',location,re.I):
            world += '；' + self.store.appearance().get('visual_bedroom','')
        return {'identity_anchors':appearance['body'] or '成年角色，保持同一張臉與基本身體特徵',
                'appearance_authoritative':True,'lighting':lighting,'photo_dialogue':photo_dialogue,
                'current_clothing':clothing,'current_accessories':appearance['accessories'],'current_scene':location,
                '_current_clothing_slots':deepcopy(appearance['clothing']),
                'current_jewelry':appearance.get('jewelry',state['appearance_defaults'].get('jewelry','無飾品')),
                'current_makeup':appearance.get('makeup',state['appearance_defaults'].get('makeup','')),
                'background_context':background,
                'visual_world':world.strip('；'),
                'viewpoint_context':{'character_location':scene.get('character_location') or location,
                    'user_location':scene.get('user_location',''),'relative_positions':deepcopy(scene.get('positions',{}))},
                'scene_facts':{'status':scene['status'],'facts':{k:v for k,v in facts.items() if v},'authority':'PyramidCore current state'}}
