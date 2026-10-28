"""Small authenticated Telegram settings surface on loopback, not the workbench."""
import asyncio
import hashlib
import hmac
import json
import logging
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

STATIC=Path(__file__).parent/'miniapp_static'
MAX_BODY=16384
GROUPS={'persona','traits','needs','appearance','grooming','world','current_clothing',
        'relationship','adult','adult_traits','adult_relation','system'}
log=logging.getLogger('companion.miniapp')

class APIError(Exception):
 def __init__(self,status,message):self.status=status;self.message=message

def public_url(value):
 try:
  p=urlsplit(value)
  if p.scheme!='https' or not p.hostname or p.username or p.password or p.query or p.fragment or p.port not in (None,443):return ''
  if p.hostname in {'localhost','127.0.0.1','::1'} or p.path not in {'','/'}:return ''
  return value.rstrip('/')+'/'
 except (ValueError,TypeError):return ''

def verify_init_data(raw,token,allowed_user,now=None):
 if not isinstance(raw,str) or not raw or len(raw)>8192:raise APIError(401,'請從 Telegram 的角色面板開啟。')
 pairs=parse_qsl(raw,keep_blank_values=True)
 if len(pairs)!=len(dict(pairs)):raise APIError(401,'驗證資料無效。')
 data=dict(pairs);received=data.pop('hash','')
 check='\n'.join(k+'='+v for k,v in sorted(data.items()))
 secret=hmac.new(b'WebAppData',token.encode(),hashlib.sha256).digest()
 expected=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
 if not token or not hmac.compare_digest(received,expected):raise APIError(401,'驗證資料無效，請從 Telegram 重新開啟。')
 try:
  age=(time.time() if now is None else now)-int(data['auth_date']);user=json.loads(data['user'])
  if age < -30 or age>900:raise APIError(401,'面板驗證已過期，請關閉再從 Telegram 開啟。')
  if type(user.get('id')) is not int or user['id']!=allowed_user:raise APIError(403,'此帳號無法使用這個面板。')
 except (KeyError,ValueError,TypeError,AttributeError):raise APIError(401,'驗證資料不完整。') from None
 return user['id']

class MiniApp:
 def __init__(self,app):self.app=app;self.server=None;self.last_menu_url='';self._requests=[]
 def public_url(self):
  path=self.app.config.data_dir/'miniapp-public.json'
  try:return public_url(json.loads(path.read_text()).get('url',''))
  except (OSError,ValueError,TypeError,AttributeError):return ''
 def available_fields(self):
  from .telegram_settings import FIELDS
  return {k:f for k,f in FIELDS.items() if f.group in GROUPS}
 def value(self,f):
  if f.source=='language':return {'Traditional Chinese':'CH','English':'EN','Japanese':'JP'}[self.app.store.output_language()]
  v=self.app.settings_ui.current(f)
  if f.kind=='bool':return 'on' if v in (True,'true','開啟','on') else 'off'
  if f.choices:return next((key for key,label in f.choices if label==v),v)
  return v
 def snapshot(self):
  if not self.app.pyramid:raise APIError(503,'目前尚未啟用角色核心。')
  from .visual_state import current_appearance
  state=self.app.pyramid.load()
  fields=[{'id':key,'label':f.label,'group':f.group,'kind':f.kind,'minimum':f.minimum,'maximum':f.maximum,
           'choices':list(f.choices),'value':self.value(f),'limit':160 if f.source=='current_clothing' else 800}
          for key,f in self.available_fields().items()]
  return {'name':state['story']['name'],'time':self.app.now().strftime('%m/%d %H:%M'),
   'scene':{'character':state['scene'].get('character_location') or state['scene'].get('location',''),
            'user':state['scene'].get('user_location') or '未確認','pending':(state['scene'].get('pending') or {}).get('location','')},
   'activity':state.get('activity',''),'energy':state['energy'],'stress':state['stress'],
   'appearance':current_appearance(state['appearance']),
   'plan':[{'time':x['at'][11:16],'location':x['location'],'activity':x.get('arrival_activity') or x.get('activity','')} for x in state['schedule']],
   'fields':fields,'busy':self.app.busy}
 async def apply(self,body):
  key=body.get('field');fields=self.available_fields()
  if not isinstance(key,str) or key not in fields:raise APIError(400,'這個欄位無法由面板修改。')
  value=body.get('value')
  if type(value) not in (str,int,float) or isinstance(value,bool):raise APIError(400,'設定值格式不正確。')
  if len(str(value))>1600:raise APIError(400,'設定內容過長。')
  try:
   async with asyncio.timeout(.3):await self.app.control_lock.acquire()
  except TimeoutError:raise APIError(409,'角色正在處理工作，請稍後儲存；編輯內容仍保留。') from None
  try:
   if 'expected' not in body or self.value(fields[key])!=body['expected']:
    raise APIError(409,'這個欄位已變更。請重新整理後再修改；其他編輯仍保留。')
   from .services import SILENT_SETTINGS_REPLY
   token=SILENT_SETTINGS_REPLY.set(True)
   archive=getattr(self.app,'diagnostics',None)
   from contextlib import nullcontext
   try:
    with archive.run('miniapp_setting',{'field':key,'value':value}) if archive else nullcontext():
     await self.app.settings_ui.apply(0,self.app.config.allowed_user,{'pending':key},str(value),refresh=False)
   except ValueError as e:raise APIError(400,str(e)) from None
   finally:SILENT_SETTINGS_REPLY.reset(token)
   return {'saved':True,'field':key,'value':self.value(fields[key])}
  finally:self.app.control_lock.release()
 async def open(self,chat_id):
  url=self.public_url()
  if not url:
   await self.app.telegram.send(chat_id,'角色面板已建立，正在等待 HTTPS 入口連線；目前可先用 /settings 按鈕。');return
  await self.app.telegram.call('sendMessage',{'chat_id':chat_id,'text':'角色面板｜查看近況與調整設定',
   'reply_markup':{'inline_keyboard':[[{'text':'開啟角色面板','web_app':{'url':url}}]]}})
 async def handle(self,method,path,headers,body):
  parsed=urlsplit(path)
  if method=='GET':
   if parsed.path=='/health':return 200,'application/json',b'{"ok":true}'
   name={'/':'index.html','/miniapp.js':'miniapp.js','/miniapp.css':'miniapp.css'}.get(parsed.path)
   if not name:raise APIError(404,'找不到這個頁面。')
   kind={'html':'text/html; charset=utf-8','js':'application/javascript; charset=utf-8','css':'text/css; charset=utf-8'}[name.rsplit('.',1)[1]]
   return 200,kind,(STATIC/name).read_bytes()
  if method!='POST' or parsed.path not in {'/api/snapshot','/api/apply'}:raise APIError(404,'找不到這個操作。')
  auth=headers.get('authorization','')
  verify_init_data(auth[4:] if auth.startswith('tma ') else '',self.app.config.token,self.app.config.allowed_user)
  origin=headers.get('origin')
  if origin and origin!=self.public_url().rstrip('/'):raise APIError(403,'來源網址不符。')
  if headers.get('content-type','').split(';')[0]!='application/json':raise APIError(415,'請使用 JSON。')
  try:data=json.loads(body or b'{}')
  except (ValueError,UnicodeError):raise APIError(400,'資料格式不正確。') from None
  if not isinstance(data,dict):raise APIError(400,'資料格式不正確。')
  now=time.monotonic();self._requests=[t for t in self._requests if now-t<60]
  if len(self._requests)>=120:raise APIError(429,'操作過於頻繁，請稍候。')
  self._requests.append(now)
  result=self.snapshot() if parsed.path=='/api/snapshot' else await self.apply(data)
  return 200,'application/json; charset=utf-8',json.dumps(result,ensure_ascii=False,allow_nan=False).encode()
 async def serve(self,reader,writer):
  try:
   async with asyncio.timeout(8):
    raw=await reader.readuntil(b'\r\n\r\n')
    if len(raw)>12288:raise APIError(431,'請求標頭過長。')
    lines=raw.decode('latin1').split('\r\n');parts=lines[0].split()
    if len(parts)!=3:raise APIError(400,'請求無效。')
    headers={}
    for line in lines[1:]:
     if not line:continue
     if ':' not in line:raise APIError(400,'請求無效。')
     key,value=line.split(':',1);key=key.lower()
     if key in headers:raise APIError(400,'重複請求標頭。')
     headers[key]=value.strip()
    if 'transfer-encoding' in headers:raise APIError(400,'不支援此傳輸格式。')
    try:size=int(headers.get('content-length','0'))
    except ValueError:raise APIError(400,'請求長度無效。') from None
    if size<0 or size>MAX_BODY:raise APIError(413,'資料過大。')
    body=await reader.readexactly(size)
    status,kind,data=await self.handle(parts[0],parts[1],headers,body)
  except APIError as e:status,kind,data=e.status,'application/json; charset=utf-8',json.dumps({'error':e.message},ensure_ascii=False).encode()
  except (TimeoutError,asyncio.IncompleteReadError,asyncio.LimitOverrunError,UnicodeError):status,kind,data=400,'application/json',b'{"error":"Invalid request"}'
  except Exception:
   log.warning('Mini App request failed; private request not logged')
   status,kind,data=500,'application/json',b'{"error":"Panel temporarily unavailable"}'
  try:
   hdr=(f'HTTP/1.1 {status} Response\r\nContent-Type: {kind}\r\nContent-Length: {len(data)}\r\nConnection: close\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\n'
    "Content-Security-Policy: default-src 'self'; script-src 'self' https://telegram.org; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors https://web.telegram.org https://*.telegram.org\r\n\r\n")
   writer.write(hdr.encode()+data);await writer.drain()
  except (ConnectionError,OSError):pass
  finally:
   writer.close()
   try:await writer.wait_closed()
   except (ConnectionError,OSError):pass
 async def sync_menu(self):
  while True:
   url=self.public_url()
   if url and url!=self.last_menu_url:
    try:
     await self.app.telegram.call('setChatMenuButton',{'chat_id':self.app.config.allowed_user,
      'menu_button':{'type':'web_app','text':'角色面板','web_app':{'url':url}}})
     self.last_menu_url=url
    except Exception:log.info('Mini App menu update deferred')
   await asyncio.sleep(30)
 async def run(self):
  if not self.app.pyramid or self.app.store.get('miniapp_enabled','false')!='true':return
  try:self.server=await asyncio.start_server(self.serve,'127.0.0.1',8767,limit=16384)
  except OSError:
   log.warning('Mini App loopback port unavailable; chat continues');return
  log.info('Mini App listening on loopback port 8767')
  async with self.server,asyncio.TaskGroup() as group:
   group.create_task(self.server.serve_forever());group.create_task(self.sync_menu())
