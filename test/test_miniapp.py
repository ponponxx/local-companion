"""Telegram-signed access, local settings reuse, and no concurrent state clobber."""
import asyncio,hashlib,hmac,json,time,unittest
from urllib.parse import urlencode
from dataclasses import replace
from unittest.mock import AsyncMock
from pathlib import Path
import httpx
import test_pyramid_telegram as fixtures
from companion.miniapp import MiniApp,APIError,verify_init_data,public_url
from companion.services import Telegram,SILENT_SETTINGS_REPLY

def signed(uid=42,when=None,token='unit-test-token',**extra):
 fields={'auth_date':str(int(time.time()) if when is None else when),'user':json.dumps({'id':uid,'first_name':'Test'}),**extra}
 key=hmac.new(b'WebAppData',token.encode(),hashlib.sha256).digest()
 fields['hash']=hmac.new(key,'\n'.join(k+'='+v for k,v in sorted(fields.items())).encode(),hashlib.sha256).hexdigest()
 return urlencode(fields)

class AuthTests(unittest.TestCase):
 def test_signature_user_expiry_and_duplicate_parameters(self):
  self.assertEqual(verify_init_data(signed(when=1000),'unit-test-token',42,now=1100),42)
  for raw,status in [(signed(uid=43,when=1500),403),(signed(when=1000),401),(signed(when=3000),401),(signed(when=1000)+'&user=other',401),('',401),(signed(when=1000).replace('Test','Changed'),401)]:
   with self.subTest(raw=raw):
    with self.assertRaises(APIError) as e:verify_init_data(raw,'unit-test-token',42,now=2000)
    self.assertEqual(e.exception.status,status)
 def test_bad_payload_and_secret_are_never_accepted(self):
  for raw in [signed()+'x',signed(token='different'),'x'*9000,'hash=abc&auth_date=x&user=[]']:
   with self.assertRaises(APIError):verify_init_data(raw,'unit-test-token',42)
 def test_only_https_root_urls_are_accepted(self):
  self.assertEqual(public_url('https://example.com'),'https://example.com/')
  for url in ['http://example.com','https://localhost','https://a:b@example.com','https://example.com/?token=secret','https://example.com/path','javascript:alert(1)']:
   self.assertFalse(public_url(url))

class PanelTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=fixtures.TelegramPyramidTests.asyncSetUp
 asyncTearDown=fixtures.TelegramPyramidTests.asyncTearDown
 async def setup_panel(self):
  self.app.config=replace(self.app.config,token='unit-test-token',allowed_user=42)
  self.panel=self.app.miniapp
  async def unexpected(request):raise AssertionError('panel must not send Telegram messages')
  client=httpx.AsyncClient(transport=httpx.MockTransport(unexpected));self.addAsyncCleanup(client.aclose)
  self.app.telegram=Telegram('unit-test-token',client)
  return self.panel
 async def test_authenticated_snapshot_is_allowlisted_and_has_no_transcripts_or_secrets(self):
  p=await self.setup_panel();status,_,raw=await p.handle('POST','/api/snapshot',{'authorization':'tma '+signed(),'content-type':'application/json'},b'{}')
  self.assertEqual(status,200);data=json.loads(raw);self.assertIn('fields',data)
  self.assertNotIn('history',data);self.assertNotIn('pending_responses',str(data));self.assertNotIn('unit-test-token',str(data))
  self.assertFalse(any(f['id'].startswith('profile.') for f in data['fields']));self.assertEqual(self.llm.calls,[])
 async def test_unauthenticated_and_cross_origin_requests_fail_before_reading_state(self):
  p=await self.setup_panel()
  for headers,status in [({'content-type':'application/json'},401),({'authorization':'tma '+signed(),'content-type':'application/json','origin':'https://other.example'},403)]:
   with self.assertRaises(APIError) as e:await p.handle('POST','/api/snapshot',headers,b'{}')
   self.assertEqual(e.exception.status,status)
 async def test_clothing_save_reuses_setter_without_model_memory_or_setting_session_changes(self):
  p=await self.setup_panel();state=self.app.pyramid.load();state['_pending_responses']=[{'turn_id':'older','background_attempts':3}];self.app.pyramid.save_clock(state)
  session={'pending':'trait.agreeableness','group':'traits'};self.app.settings_ui.save(42,session)
  before_session=self.app.settings_ui.session(42);before_default=self.store.appearance()
  expected=p.value(p.available_fields()['worn.top'])
  result=await p.apply({'field':'worn.top','value':'黑色襯衫','expected':expected})
  self.assertTrue(result['saved']);self.assertEqual(result['value'],'黑色襯衫');self.assertEqual(self.store.appearance(),before_default)
  self.assertEqual(self.app.settings_ui.session(42),before_session);self.assertEqual(len(self.app.pyramid.load()['_pending_responses']),1)
  self.assertEqual(self.store.recent(),[]);self.assertEqual(self.llm.calls,[]);self.assertFalse(SILENT_SETTINGS_REPLY.get())
 async def test_parameters_and_choices_match_existing_storage(self):
  p=await self.setup_panel()
  for field,value in [('trait.agreeableness',62),('relation.trust',87),('need.relatedness',1.5),('language','JP')]:
   f=p.available_fields()[field];await p.apply({'field':field,'value':value,'expected':p.value(f)})
   if f.kind=='number':self.assertEqual(float(p.value(f)),float(value))
   else:self.assertEqual(str(p.value(f)),str(value))
  self.assertEqual(self.llm.calls,[])
 async def test_validation_conflicts_and_unsupported_fields_preserve_state(self):
  p=await self.setup_panel();f=p.available_fields()['trait.agreeableness'];before=p.value(f)
  for body,status in [({'field':'trait.agreeableness','value':200,'expected':before},400),({'field':'trait.agreeableness','value':62,'expected':-1},409),({'field':'remember','value':'not a command'},400),({'field':[]},400)]:
   with self.assertRaises(APIError) as e:await p.apply(body)
   self.assertEqual(e.exception.status,status);self.assertEqual(p.value(f),before)
  self.assertFalse(self.app.control_lock.locked());self.assertFalse(SILENT_SETTINGS_REPLY.get())
 async def test_busy_foreground_rejects_write_without_losing_existing_value(self):
  p=await self.setup_panel();f=p.available_fields()['worn.top'];before=p.value(f)
  async with self.app.control_lock:
   with self.assertRaises(APIError) as e:await p.apply({'field':'worn.top','value':'藍色襯衫','expected':before})
   self.assertEqual(e.exception.status,409)
  self.assertEqual(p.value(f),before)
 async def test_real_http_surface_has_no_path_traversal_or_unauthenticated_state(self):
  p=await self.setup_panel();server=await asyncio.start_server(p.serve,'127.0.0.1',0,limit=16384)
  self.addAsyncCleanup(self.close_server,server);port=server.sockets[0].getsockname()[1]
  for request,expected in [(b'GET /../.env HTTP/1.1\r\nHost: localhost\r\n\r\n',404),(b'POST /api/snapshot HTTP/1.1\r\nHost: localhost\r\nContent-Length: 2\r\n\r\n{}',401),(b'POST /api/apply HTTP/1.1\r\nContent-Length: 99999\r\n\r\n',413),(b'GET /health HTTP/1.1\r\n\r\n',200)]:
   reader,writer=await asyncio.open_connection('127.0.0.1',port);writer.write(request);await writer.drain();raw=await reader.read();writer.close();await writer.wait_closed()
   self.assertTrue(raw.startswith(f'HTTP/1.1 {expected}'.encode()));self.assertIn(b'Cache-Control: no-store',raw)
 async def close_server(self,server):server.close();await server.wait_closed()
 async def test_static_app_is_public_but_contains_no_live_state(self):
  p=await self.setup_panel()
  status,kind,body=await p.handle('GET','/',{},b'')
  self.assertEqual(status,200);self.assertIn('text/html',kind)
  self.assertIn(b'telegram-web-app.js',body);self.assertNotIn(b'unit-test-token',body)
  for asset in ['/miniapp.js','/miniapp.css']:
   status,_,content=await p.handle('GET',asset,{},b'');self.assertEqual(status,200);self.assertTrue(content)
 async def test_panel_entry_has_webapp_button_without_secret_or_auth_in_url(self):
  self.app.config=replace(self.app.config,allowed_user=42)
  (self.path/'miniapp-public.json').write_text(json.dumps({'url':'https://example.com'}))
  await self.app.handle(1,{'chat':{'id':42},'text':'/panel'})
  method,payload=self.tg.call.await_args.args;self.assertEqual(method,'sendMessage')
  self.assertEqual(payload['reply_markup']['inline_keyboard'][0][0]['web_app']['url'],'https://example.com/')
  self.assertEqual(self.llm.calls,[])
