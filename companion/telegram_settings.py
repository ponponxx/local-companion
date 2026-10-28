from .proactive_limits import parse_limit, limit_label, limit_reached
"""Telegram settings UI. No model calls, conversation writes or parallel policies."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import secrets
import hashlib

from .store import TRAIT_LABELS, RELATIONSHIP_LABELS
from .adult_intimacy import ADULT_TRAIT_LABELS, ADULT_RELATIONSHIP_LABELS
from .life import parse_clock


@dataclass(frozen=True)
class Field:
    label: str
    group: str
    command: str
    source: str
    key: str
    kind: str = 'text'
    minimum: float = 0
    maximum: float = 100
    choices: tuple = ()


GROUPS = {
    'root': '設定', 'role': '角色', 'persona': '身份與背景', 'traits': '性格',
    'needs': '需求權重', 'relationship': '關係', 'adult': '成人',
    'adult_traits': '成人傾向', 'adult_relation': '成人關係',
    'current_clothing':'當前穿著', 'appearance': '外觀', 'grooming': '妝髮', 'world': '場景風格',
    'system': '語言與生活', 'data': '我的資料與記憶', 'profile': '新增 Profile',
    'profile_records':'修改／刪除 Profile', 'memory_records':'管理指定記憶',
}
PARENTS = {'role':'root','persona':'role','traits':'role','needs':'role',
           'relationship':'root','adult':'root','adult_traits':'adult','adult_relation':'adult',
           'current_clothing':'appearance','appearance':'root','grooming':'appearance','world':'appearance',
           'system':'root','data':'root','profile':'data','profile_records':'data','memory_records':'data'}


def fields():
    result = {}
    def add(identifier, *args, **kwargs): result[identifier] = Field(*args, **kwargs)
    for key,label in {'name':'角色姓名','background':'背景故事'}.items():
        add('persona.'+key,label,'persona','/persona '+key,'persona',key)
    for key,label in TRAIT_LABELS.items():
        add('trait.'+key,label,'traits','/settrait '+key,'traits',key,'number')
    for key,label in RELATIONSHIP_LABELS.items():
        if key not in ADULT_RELATIONSHIP_LABELS:
            add('relation.'+key,label,'relationship','/setrelation '+key,'relationship',key,'number')
    for key,label in ADULT_TRAIT_LABELS.items():
        add('adult_trait.'+key,label,'adult_traits','/adult traits '+key,'adult_intimacy',key,'number')
    for key,label in ADULT_RELATIONSHIP_LABELS.items():
        add('adult_relation.'+key,label,'adult_relation','/adult relationship '+key,'relationship',key,'number')
    for key,label in {'relatedness':'連結','autonomy':'自主','competence':'勝任','curiosity':'好奇','homeostasis':'身心平衡','erotic_connection':'成人連結'}.items():
        add('need.'+key,label,'adult' if key=='erotic_connection' else 'needs','', 'need_weights',key,'number',.25,2)
    for key,label,group in [('body','固定外觀','appearance'),('clothing','衣著（預設）','appearance'),
                            ('jewelry','飾品','appearance'),('accessories','配件','appearance'),
                            ('hair','髮型','grooming'),('makeup','妝容','grooming'),
                            ('visual_world','世界風格','world'),('visual_bedroom','臥室風格','world')]:
        add('look.'+key,label,group,'/appearance '+key,'appearance',key)
    from .visual_state import SLOT_NAMES
    for key,label in SLOT_NAMES.items():
        add('worn.'+key,label,'current_clothing','/outfit '+key,'current_clothing',key,'clothing')
    add('mode','允許範圍','adult','/adult mode','adult_policy','mode','choice',choices=(('off','關閉'),('flirt','曖昧'),('intimate','親密')))
    add('initiate','角色主動發起','adult','/adult initiate','adult_policy','agent_may_initiate','bool')
    add('language','回覆語言','system','/language','language','','choice',choices=(('CH','繁體中文'),('EN','English'),('JP','日本語')))
    for key,label,command in [('life_enabled','模擬生活','/life'),('proactive_enabled','主動訊息','/proactive')]:
        add(key,label,'system',command,'setting',key,'bool')
    add('web_enabled','上網查資料','system','/web','setting','web_enabled','bool')
    add('news_enabled','主動聊新聞','system','/news','setting','news_enabled','bool')
    add('news_topics','新聞主題','system','/news topics','setting','news_topics','choice',choices=(('technology','科技'),('space','太空'),('technology,space','科技與太空')))
    add('story_events_enabled','生活小事件','system','/storyevents','setting','story_events_enabled','bool')
    add('story_event_pace','事件進展節奏','system','/storyevents','setting','story_event_pace','choice',
        choices=(('slow','慢：180分鐘'),('normal','一般：90分鐘'),('fast','快：45分鐘')))
    add('silence','主動聊天等待分鐘','system','/proactive-silence','setting','proactive_min_silence_minutes','integer',10,1440)
    add('cooldown','主動聊天冷卻分鐘','system','/proactive-cooldown','setting','proactive_cooldown_minutes','integer',10,1440)
    add('quiet','安靜時段','system','/quiet','quiet','','clock')
    add('limit','每日主動聊天上限（不含移動告知）','system','/proactive-limit','setting','proactive_daily_limit','daily_limit')
    for category in ('稱呼','興趣','習慣','偏好','目標','界線'):
        add('profile.'+category,category,'profile','/profile set '+category,'profile',category)
    add('remember','新增指定記憶','data','/remember','empty','')
    return result


FIELDS = fields()
FIELD_IDS = list(FIELDS)
FIELD_VERSION=hashlib.sha256('|'.join(FIELD_IDS).encode()).hexdigest()[:12]


class TelegramSettings:
    def __init__(self, app): self.app, self.store = app, app.store

    async def install_commands(self):
        scope={'type':'chat','chat_id':self.app.config.allowed_user}
        existing=await self.app.telegram.call('getMyCommands',{'scope':scope})
        if not existing:existing=await self.app.telegram.call('getMyCommands',{})
        commands={c['command']:c for c in existing if c['command'] not in {'harness','settings','cancel','help','photo','debug'}}
        for command,description in [('panel','開啟角色設定面板'),('outfit','查看或修改角色當前穿著'),('settings','開啟按鈕式設定'),('cancel','取消設定輸入'),('help','查看所有指令'),('photo','使用者視角拍角色照片'),('debug','白話逐步判斷流程'),('web','上網功能與服務狀態'),('news','新聞訂閱與主動分享'),('search','查詢公開主題，不輸入私人資料')]:
            commands[command]={'command':command,'description':description}
        await self.app.telegram.call('setMyCommands',{'scope':scope,'commands':list(commands.values())[-100:]})

    def session(self, chat_id):
        try: return json.loads(self.store.get('settings_ui:'+str(chat_id),'{}'))
        except (ValueError,TypeError): return {}

    def save(self, chat_id, session):
        previous=self.session(chat_id)
        retired=list(dict.fromkeys(previous.get('prompt_ids',[])+session.get('prompt_ids',[])))
        if previous.get('prompt_id'):retired.append(previous['prompt_id'])
        session['prompt_ids']=list(dict.fromkeys(retired))[-30:]
        self.store.set('settings_ui:'+str(chat_id),json.dumps(session,ensure_ascii=False))

    def fresh(self, group='root'):
        return {'token':secrets.token_hex(4),'fields':FIELD_VERSION,'group':group,'expires':(datetime.now(timezone.utc)+timedelta(minutes=15)).isoformat()}

    def valid(self, session):
        try: return session.get('fields')==FIELD_VERSION and datetime.fromisoformat(session['expires']) > datetime.now(timezone.utc)
        except (KeyError,ValueError,TypeError): return False

    def current(self, field):
        if field.source == 'current_clothing':
            from .visual_state import current_appearance
            return current_appearance(self.app.pyramid.load()['appearance'])['clothing'][field.key] if self.app.pyramid else '尚未啟用角色核心'
        if field.source == 'setting':
            value=self.store.get(field.key,{'story_events_enabled':'true','story_event_pace':'normal','web_enabled':'true','news_enabled':'true','news_topics':'technology'}.get(field.key))
            if field.choices:return dict(field.choices).get(value,value)
            return limit_label(value) if field.key=='proactive_daily_limit' else {'true':'開啟','false':'關閉'}.get(value,value)
        if field.source == 'language': return self.store.output_language()
        if field.source == 'quiet': return self.store.get('quiet_start')+' '+self.store.get('quiet_end')
        if field.source == 'profile':
            return '；'.join(f['value'] for f in self.store.profile_facts() if f['category']==field.key) or '尚未設定'
        if field.source == 'empty': return '請輸入要記住的內容'
        value=getattr(self.store,field.source)().get(field.key,'尚未設定')
        if field.kind=='bool':return '開啟' if value else '關閉'
        if field.choices:return dict(field.choices).get(value,value)
        return value

    def button(self, session, text, action):
        data='cfg:'+session['token']+':'+action
        assert len(data.encode()) <= 64
        return {'text':text,'callback_data':data}

    async def show(self, chat_id, group='root', message_id=None, page=0):
        session=self.fresh(group);session['page']=page;self.save(chat_id,session)
        buttons=[]
        if group=='root':buttons.append(self.button(session,'當前穿著','g.current_clothing'))
        panel=getattr(self.app,'miniapp',None)
        if group=='root' and panel and panel.public_url():buttons.insert(0,{'text':'開啟完整面板','web_app':{'url':panel.public_url()}})
        for key,parent in PARENTS.items():
            if parent==group: buttons.append(self.button(session,GROUPS[key],'g.'+key))
        for index,key in enumerate(FIELD_IDS):
            f=FIELDS[key]
            if f.group==group:
                value=str(self.current(f))
                buttons.append(self.button(session,f.label+('：'+value[:14] if f.kind in {'number','integer','bool','choice','daily_limit','clothing'} else ''),'f.'+str(index)))
        if group=='current_clothing':buttons.append(self.button(session,'恢復目前地點預設穿搭','outfit.reset'))
        if group=='adult': buttons.append(self.button(session,'查看當前成人狀態','view.adult'))
        if group=='data':
            buttons += [self.button(session,'查看指定記憶','view.memory'),self.button(session,'查看我的 Profile','view.profile')]
        if group in {'profile_records','memory_records'}:
            records=self.store.profile_facts() if group=='profile_records' else self.store.memories()
            page=max(0,min(page,max(0,(len(records)-1)//8)));session['page']=page;self.save(chat_id,session)
            prefix='pr.' if group=='profile_records' else 'mr.'
            for record in records[page*8:page*8+8]:
                label=record.get('category','記憶')+'：'+record.get('value',record.get('content',''))[:18]
                buttons.append(self.button(session,label,prefix+str(record['id'])))
            if page:buttons.append(self.button(session,'上一頁','page.'+str(page-1)))
            if (page+1)*8<len(records):buttons.append(self.button(session,'下一頁','page.'+str(page+1)))
        rows=[buttons[i:i+2] for i in range(0,len(buttons),2)]
        if group!='root':rows.append([self.button(session,'返回','g.'+PARENTS[group]),self.button(session,'主選單','g.root')])
        rows.append([self.button(session,'關閉設定','close')])
        text=GROUPS[group]+'\n選擇項目。修改數值或文字不會進入聊天記憶。'
        if group=='system' and self.app.pyramid:
            text+='\n移動通知不占主動聊天額度；通知送達後10分鐘抵達，途中回覆不重啟倒數。'
        if group=='current_clothing':text+='\n先按要修改的部位，再輸入衣物內容，例如「黑色襯衫」。只改當前穿著，不改預設。可輸入衣物描述、「未穿著」或「未確認」。下一次確認抵達新地點時會依該地點換裝。'
        payload={'chat_id':chat_id,'text':text,'reply_markup':{'inline_keyboard':rows}}
        if message_id:payload['message_id']=message_id
        await self.app.telegram.call('editMessageText' if message_id else 'sendMessage',payload)

    async def callback(self, update_id, chat_id, query):
        if query.get('from',{}).get('id') != self.app.config.allowed_user:return
        parts=query.get('data','').split(':',2)
        session=self.session(chat_id)
        if len(parts)!=3 or parts[0]!='cfg' or not self.valid(session) or parts[1]!=session.get('token'):
            await self.app.telegram.send(chat_id,'這組設定按鈕已過期，請輸入 /settings 重新開啟。');return
        action=parts[2];message_id=query.get('message',{}).get('message_id')
        if action=='close':
            self.save(chat_id,{})
            await self.app.telegram.call('editMessageText',{'chat_id':chat_id,'message_id':message_id,'text':'設定已關閉。','reply_markup':{'inline_keyboard':[]}});return
        if action=='outfit.reset' and session.get('group')=='current_clothing':
            await self.app.handle(update_id,{'chat':{'id':chat_id},'text':'/outfit reset','_settings_apply':True})
            await self.show(chat_id,'current_clothing',message_id);return
        if action.startswith('g.') and action[2:] in GROUPS:
            await self.show(chat_id,action[2:],message_id);return
        if action.startswith('page.') and action[5:].isdigit() and session.get('group') in {'profile_records','memory_records'}:
            await self.show(chat_id,session['group'],message_id,int(action[5:]));return
        if action.startswith(('pr.','mr.','pe.','pd.','md.','px.','mx.')) and action[3:].isdigit():
            operation=action[:2];identifier=int(action[3:]);profile=operation.startswith('p')
            records=self.store.profile_facts() if profile else self.store.memories()
            record=next((r for r in records if r['id']==identifier),None)
            if record is None:
                await self.app.telegram.send(chat_id,'這筆資料已不存在。');return
            group='profile_records' if profile else 'memory_records'
            value=record.get('value',record.get('content',''))
            if operation in {'pr','mr'}:
                session.pop('pending',None);self.save(chat_id,session)
                buttons=([self.button(session,'修改內容','pe.'+str(identifier))] if profile else [])
                buttons.append(self.button(session,'刪除','pd.'+str(identifier) if profile else 'md.'+str(identifier)))
                await self.app.telegram.call('editMessageText',{'chat_id':chat_id,'message_id':message_id,
                    'text':('Profile：' if profile else '指定記憶：')+value[:2800],
                    'reply_markup':{'inline_keyboard':[buttons,[self.button(session,'返回','g.'+group)]]}});return
            if operation=='pe':
                session.pop('prompt_id',None)
                session['pending']='profile_record';session['record_id']=identifier;self.save(chat_id,session)
                await self.prompt(chat_id,session,self.resolve(session));return
            if operation in {'pd','md'}:
                session['confirm']=operation+'.'+str(identifier);self.save(chat_id,session)
                text='確定刪除這筆 Profile？' if profile else '確定停用這筆指定記憶？之後不再引用該筆與其衍生內容，其他聊天保留；原文與診斷紀錄仍會保存。'
                await self.app.telegram.call('editMessageText',{'chat_id':chat_id,'message_id':message_id,'text':text,
                    'reply_markup':{'inline_keyboard':[[self.button(session,'確定刪除',('px.' if profile else 'mx.')+str(identifier))],
                    [self.button(session,'取消','g.'+group)]]}});return
            if operation in {'px','mx'}:
                expected=('pd.' if profile else 'md.')+str(identifier)
                if session.get('confirm')!=expected:return
                command=('/profile delete ' if profile else '/forget ')+str(identifier)
                await self.app.handle(update_id,{'chat':{'id':chat_id},'text':command,'_settings_apply':True})
                await self.show(chat_id,group);return
        if action.startswith('view.'):
            session.pop('pending',None);self.save(chat_id,session)
            cmd={'adult':'/adult state','memory':'/memory','profile':'/profile'}.get(action[5:])
            if cmd:await self.app.handle(update_id,{'chat':{'id':chat_id},'text':cmd})
            return
        if action.startswith('f.') and action[2:].isdigit() and int(action[2:])<len(FIELD_IDS):
            key=FIELD_IDS[int(action[2:])];f=FIELDS[key]
            session.pop('prompt_id',None)
            session['pending']=key;self.save(chat_id,session)
            if f.kind in {'choice','bool'}:
                choices=f.choices or (('on','開啟'),('off','關閉'))
                rows=[[self.button(session,label,'v.'+value) for value,label in choices],
                      [self.button(session,'返回','g.'+f.group)]]
                await self.app.telegram.call('editMessageText',{'chat_id':chat_id,'message_id':message_id,
                    'text':f.label+'\n目前：'+str(self.current(f)), 'reply_markup':{'inline_keyboard':rows}})
            else:
                await self.prompt(chat_id,session,f)
            return
        if action.startswith('v.') and session.get('pending') in FIELDS:
            await self.apply(update_id,chat_id,session,action[2:])

    def resolve(self, session):
        if session.get('pending')=='profile_record':
            record=next((f for f in self.store.profile_facts() if f['id']==session.get('record_id')),None)
            if not record:raise ValueError('這筆 Profile 已不存在，請用 /settings 重開')
            return Field('修改 '+record['category'],'profile_records',
                         '/profile update '+str(record['id'])+' '+record['category'],'record',str(record['id']))
        return FIELDS[session['pending']]

    async def prompt(self, chat_id, session, f):
        text_limit=160 if f.source=='current_clothing' else 240 if f.source in {'profile','record'} else 800
        hint=f'請輸入 {f.minimum:g}–{f.maximum:g}'+(' 的整數' if f.kind=='integer' else ' 的數值') if f.kind in {'number','integer'} else ('請輸入開始與結束時間，例如：23:00 08:00' if f.kind=='clock' else f'請輸入新內容（最多{text_limit}字）')
        if f.kind=='clothing':hint+='；只輸入衣物內容，例如「黑色襯衫」，不用加欄位名稱。輸入「未穿著」表示已脫下，「未確認」表示未知。'
        if f.kind=='daily_limit':hint='請輸入每日則數，或「不限」；0 表示不發主動聊天。'
        current=next((r['value'] for r in self.store.profile_facts() if str(r['id'])==f.key),'') if f.source=='record' else self.current(f)
        result=await self.app.telegram.call('sendMessage',{'chat_id':chat_id,'text':f.label+'\n目前：'+str(current)[:1800]+'\n'+hint+'\n取消請用 /cancel。',
            'reply_markup':{'force_reply':True,'selective':True,'input_field_placeholder':'輸入設定值；/cancel 取消'}})
        session['prompt_id']=result.get('message_id') if isinstance(result,dict) else None
        self.save(chat_id,session)

    async def apply(self, update_id, chat_id, session, value, *, refresh=True):
        f=self.resolve(session);value=value.strip()
        if f.kind=='daily_limit':
            value=parse_limit(value)
        elif f.kind in {'number','integer'}:
            try: number=float(value)
            except ValueError:raise ValueError('請輸入數值') from None
            if not math.isfinite(number) or not f.minimum<=number<=f.maximum or (f.kind=='integer' and not number.is_integer()):
                raise ValueError(f'請輸入 {f.minimum:g}–{f.maximum:g}'+(' 的整數' if f.kind=='integer' else ' 的數值'))
            value=f'{number:g}'
        elif f.kind in {'choice','bool'}:
            options=[v for v,_ in f.choices] if f.choices else ['on','off']
            if value not in options:raise ValueError('請使用本項目的選項按鈕')
        elif f.kind=='clock':
            parts=value.split()
            if len(parts)!=2:raise ValueError('格式：23:00 08:00')
            for p in parts:parse_clock(p)
        else:
            text_limit=160 if f.source=='current_clothing' else 240 if f.source in {'profile','record'} else 800
            if not 1<=len(value)<=text_limit:raise ValueError(f'內容限1–{text_limit}字')
        if f.source=='need_weights':
            weights=self.store.need_weights();weights[f.key]=round(number,2)
            self.store.set('need_weights',json.dumps(weights,ensure_ascii=False))
            await self.app.telegram.send(chat_id,f.label+'已設為 '+value)
        else:
            # Existing command is the only setter/validator. The input is an
            # argument, never a new instruction or a model prompt.
            await self.app.handle(update_id,{'chat':{'id':chat_id},'text':f.command+' '+value,'_settings_apply':True})
        if refresh:await self.show(chat_id,f.group)

    async def intercept(self, update_id, message):
        chat_id=message['chat']['id'];text=(message.get('text') or '').strip()
        if message.get('_settings_apply'):return False
        if '_settings_callback' in message:
            await self.callback(update_id,chat_id,message['_settings_callback']);return True
        command=text.split(' ',1)[0].split('@',1)[0].lower()
        if command=='/settings':await self.show(chat_id);return True
        if command=='/cancel':
            self.save(chat_id,{})
            await self.app.telegram.send(chat_id,'已取消設定輸入。');return True
        session=self.session(chat_id)
        replied=message.get('reply_to_message',{}).get('message_id')
        if replied and replied in session.get('prompt_ids',[]) and (not session.get('pending') or replied!=session.get('prompt_id')):
            await self.app.telegram.send(chat_id,'這是先前的設定輸入，這則內容未存入聊天；請用 /settings 重開。');return True
        if text.startswith('/'):
            session.pop('pending',None);self.save(chat_id,session);return False
        if not session.get('pending'):return False
        # A reply to a settings prompt never becomes character dialogue, even
        # after timeout/navigation. Check this before the generic chat path.
        if not self.valid(session):
            self.save(chat_id,{})
            await self.app.telegram.send(chat_id,'設定輸入已過期，這則內容未存入聊天；請用 /settings 重開。');return True
        if not text:
            await self.app.telegram.send(chat_id,'設定只接受文字或數值；請輸入內容或 /cancel。');return True
        try:await self.apply(update_id,chat_id,session,text)
        except ValueError as exc:await self.app.telegram.send(chat_id,str(exc)+'；請重輸入，或 /cancel。')
        return True
