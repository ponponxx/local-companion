"""/outfit uses the same local settings buttons, never a dialogue inference."""
import json,unittest
from copy import deepcopy
from unittest.mock import AsyncMock
from dataclasses import replace
import test_pyramid_telegram as fixtures
from companion.telegram_settings import FIELD_IDS
from companion.visual_state import SLOT_NAMES,current_appearance

class OutfitButtonsTests(unittest.IsolatedAsyncioTestCase):
 asyncSetUp=fixtures.TelegramPyramidTests.asyncSetUp
 asyncTearDown=fixtures.TelegramPyramidTests.asyncTearDown
 async def open(self):
  self.app.config=replace(self.app.config,allowed_user=42)
  await self.app.handle(10,{'chat':{'id':42},'text':'/outfit'})
 async def select(self,slot):
  session=self.app.settings_ui.session(42)
  query={'id':'test','from':{'id':42},'data':'cfg:'+session['token']+':f.'+str(FIELD_IDS.index('worn.'+slot)),
         'message':{'message_id':99,'chat':{'id':42,'type':'private'}}}
  await self.app.handle(11,{'chat':{'id':42},'_settings_callback':query})
 async def test_outfit_directly_opens_all_eight_current_clothing_buttons(self):
  await self.open();call=self.tg.call.await_args
  self.assertEqual(call.args[0],'sendMessage');payload=call.args[1]
  buttons=[b for row in payload['reply_markup']['inline_keyboard'] for b in row]
  for name in SLOT_NAMES.values():self.assertTrue(any(b['text'].startswith(name+'：') for b in buttons))
  self.assertEqual(self.app.settings_ui.session(42)['group'],'current_clothing')
  self.assertIn('先按',payload['text']);self.assertEqual(self.llm.calls,[])
 async def test_top_button_accepts_value_only_and_preserves_defaults_other_slots_and_pending(self):
  state=self.app.pyramid.load();state['_pending_responses']=[{'turn_id':'older','background_attempts':3,'reply':'中性舊回文'}]
  self.app.pyramid.save_clock(state);before=deepcopy(self.app.pyramid.load());defaults=self.store.appearance()
  self.app.pyramid.recover_response=AsyncMock(side_effect=AssertionError('settings must not recover a dialogue'))
  await self.open();await self.select('top')
  self.assertEqual(self.app.settings_ui.session(42)['pending'],'worn.top')
  await self.app.handle(12,{'chat':{'id':42},'text':'黑色襯衫'})
  after=self.app.pyramid.load();self.assertEqual(current_appearance(after['appearance'])['clothing']['top'],'黑色襯衫')
  for slot in SLOT_NAMES:
   if slot!='top':self.assertEqual(after['appearance']['clothing'].get(slot),before['appearance']['clothing'].get(slot))
  self.assertEqual(after['_pending_responses'],before['_pending_responses']);self.assertEqual(self.store.appearance(),defaults)
  self.assertEqual(self.llm.calls,[]);self.assertEqual(self.store.recent(),[])
  self.assertNotIn('pending',self.app.settings_ui.session(42));self.assertEqual(self.app.settings_ui.session(42)['group'],'current_clothing')
 async def test_invalid_value_remains_in_settings_and_never_enters_chat(self):
  await self.open();await self.select('top')
  before=deepcopy(self.app.pyramid.load()['appearance'])
  await self.app.handle(12,{'chat':{'id':42},'text':'a'*161})
  self.assertEqual(self.app.pyramid.load()['appearance'],before);self.assertEqual(self.app.settings_ui.session(42)['pending'],'worn.top')
  self.assertEqual(self.llm.calls,[]);self.assertEqual(self.store.recent(),[])
 async def test_selected_clothing_value_is_immediate_and_not_batched(self):
  from companion.store import InboxWait
  await self.open();await self.select('top')
  self.store.ingest({'update_id':55,'message':{'from':{'id':42},'chat':{'id':42,'type':'private'},'text':'黑色襯衫'}},42)
  item=self.store.next_update(window_seconds=10)
  self.assertNotIsInstance(item,InboxWait);self.assertEqual(item[1]['text'],'黑色襯衫')
