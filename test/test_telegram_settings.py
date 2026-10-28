import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
import test_pyramid_telegram as fixtures
from companion.telegram_settings import FIELD_IDS, FIELDS


class SettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.TelegramPyramidTests.asyncSetUp(self)
        from dataclasses import replace
        self.app.config = replace(self.app.config,allowed_user=42)
    asyncTearDown = fixtures.TelegramPyramidTests.asyncTearDown
    text = fixtures.TelegramPyramidTests.text
    async def callback(self,action,uid=90,token=None,user=42):
        s=self.app.settings_ui.session(42)
        q={'id':'query','from':{'id':user},'data':'cfg:'+(token or s['token'])+':'+action,
           'message':{'message_id':51,'chat':{'id':42,'type':'private'}}}
        await self.app.handle(uid,{'chat':{'id':42},'_settings_callback':q})

    async def field(self,key):await self.callback('f.'+str(FIELD_IDS.index(key)))

    async def test_three_levels_numeric_entry_no_model_or_memory(self):
        await self.text('/settings',80)
        await self.callback('g.role');await self.callback('g.traits');await self.field('trait.agreeableness')
        await self.text('87',81)
        self.assertEqual(self.store.traits()['agreeableness'],87)
        self.assertEqual(self.store.recent(),[])
        self.assertEqual(self.llm.calls,[])
        self.assertNotIn('pending',self.app.settings_ui.session(42))

    async def test_invalid_numeric_is_retained_not_chat(self):
        await self.text('/settings',80);await self.field('trait.agreeableness')
        before=self.store.traits()
        for value in ('nan','inf','101','-1','hello'):
            await self.text(value,81)
            self.assertEqual(self.store.traits(),before)
            self.assertIn('pending',self.app.settings_ui.session(42))
        self.assertEqual(self.llm.calls,[])
        await self.text('/cancel',82)
        self.assertNotIn('pending',self.app.settings_ui.session(42))

    async def test_language_and_adult_mode_only_through_buttons(self):
        await self.text('/settings',80);await self.field('language');await self.callback('v.JP')
        self.assertEqual(self.store.output_language(),'Japanese')
        await self.field('mode');await self.callback('v.flirt')
        self.assertEqual(self.store.adult_policy()['mode'],'flirt')
        await self.field('mode');await self.callback('v.off')
        self.assertEqual(self.store.adult_policy()['mode'],'off')
        self.assertEqual(self.llm.calls,[])

    async def test_text_appearance_and_weights(self):
        await self.text('/settings',80);await self.field('look.body');await self.text('成年白金髮女性 pale skin',81)
        self.assertIn('pale skin',self.store.appearance()['body'])
        await self.field('look.hair');await self.text('盤髮',82)
        self.assertEqual(self.store.appearance()['hair'],'盤髮')
        await self.field('look.makeup');await self.text('淡妝',83)
        state=self.app.pyramid.load()
        self.assertEqual(state['appearance']['hair'],'盤髮')
        self.assertEqual(state['appearance']['makeup'],'淡妝')
        await self.field('need.relatedness');await self.text('1.5',84)
        self.assertEqual(self.store.need_weights()['relatedness'],1.5)
        self.assertEqual(self.store.recent(),[])
        self.assertEqual(self.llm.calls,[])

    async def test_expired_and_stale_callbacks_do_not_mutate(self):
        await self.text('/settings',80);token=self.app.settings_ui.session(42)['token']
        await self.field('language');await self.callback('v.EN')
        await self.callback('v.JP',token=token)
        self.assertEqual(self.store.output_language(),'English')
        await self.field('trait.agreeableness')
        s=self.app.settings_ui.session(42);s['expires']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat();self.app.settings_ui.save(42,s)
        await self.text('87',81)
        self.assertNotEqual(self.store.traits()['agreeableness'],87)
        self.assertEqual(self.llm.calls,[])

    async def test_stale_force_reply_never_becomes_chat(self):
        self.tg.call.return_value={'message_id':901}
        await self.text('/settings',80);await self.field('look.hair')
        await self.text('/cancel',81)
        await self.app.handle(82,{'chat':{'id':42},'text':'盤髮','reply_to_message':{'message_id':901}})
        self.assertEqual(self.llm.calls,[])
        self.assertEqual(self.store.recent(),[])

    async def test_callback_ingestion_authorization_private_and_dedup(self):
        def update(uid,user,kind='private'):
            return {'update_id':uid,'callback_query':{'id':'cb','from':{'id':user},'data':'cfg:token:g.root',
                    'message':{'message_id':3,'chat':{'id':42,'type':kind}}}}
        self.assertFalse(self.store.ingest(update(1,99),42))
        self.assertFalse(self.store.ingest(update(2,42,'group'),42))
        self.assertTrue(self.store.ingest(update(3,42),42))
        self.store.ingest(update(3,42),42)
        uid,message=self.store.next_update()
        self.assertEqual(uid,3);self.assertIn('_settings_callback',message)
        self.assertIsNone(self.store.next_update())
        self.store.finish_update(3)
        self.assertEqual(self.store.db.execute('select payload from inbox where update_id=3').fetchone()[0],'{}')

    async def test_all_buttons_fit_telegram_callback_limit(self):
        for group in __import__('companion.telegram_settings',fromlist=['GROUPS']).GROUPS:
            await self.app.settings_ui.show(42,group)
            rows=self.tg.call.call_args.args[1]['reply_markup']['inline_keyboard']
            for row in rows:
                for button in row:self.assertLessEqual(len(button['callback_data'].encode()),64)


    async def test_existing_profile_edit_delete_and_memory_confirmation(self):
        identifier=self.store.set_profile_fact('稱呼','原稱呼')
        await self.text('/settings',80)
        await self.callback('g.profile_records');await self.callback('pr.'+str(identifier))
        await self.callback('pe.'+str(identifier));await self.text('Alex',81)
        self.assertEqual(self.store.profile_facts()[0]['value'],'Alex')
        await self.callback('pr.'+str(identifier));await self.callback('pd.'+str(identifier))
        self.assertEqual(len(self.store.profile_facts()),1)
        await self.callback('px.'+str(identifier))
        self.assertEqual(self.store.profile_facts(),[])
        memory=self.store.remember('測試記憶')
        self.store.add_message('user','既有對話')
        await self.callback('g.memory_records');await self.callback('mr.'+str(memory))
        await self.callback('md.'+str(memory))
        self.assertIn('其他聊天保留',self.tg.call.call_args.args[1]['text'])
        self.assertEqual(len(self.store.memories()),1)
        await self.callback('g.memory_records')
        # A confirmation from the old menu cannot delete anything.
        await self.callback('mx.'+str(memory))
        self.assertEqual(len(self.store.memories()),1)
        await self.callback('mr.'+str(memory));await self.callback('md.'+str(memory));await self.callback('mx.'+str(memory))
        self.assertEqual(self.store.memories(),[])
        self.assertEqual(self.store.recent(),[{'role':'user','content':'既有對話'}])
        self.assertEqual(self.llm.calls,[])

    async def test_relationship_adult_integer_clock_and_profile_inputs(self):
        await self.text('/settings',80)
        for key,value in [('relation.trust','72'),('adult_relation.sexual_comfort','65'),('adult_trait.flirtatiousness','40'),('limit','3'),('quiet','22:00 07:00'),('profile.稱呼','Alex')]:
            await self.field(key);await self.text(value,81)
        self.assertEqual(self.store.relationship()['trust'],72)
        self.assertEqual(self.store.relationship()['sexual_comfort'],65)
        self.assertEqual(self.store.adult_intimacy()['flirtatiousness'],40)
        self.assertEqual(self.store.get('proactive_daily_limit'),'3')
        self.assertEqual(self.store.get('quiet_start'),'22:00')
        self.assertEqual(self.store.profile_facts()[0]['value'],'Alex')
        self.assertEqual(self.llm.calls,[])
        self.assertEqual(self.store.recent(),[])

    async def test_daily_limit_unlimited_and_user_selected_number(self):
        await self.text('/settings',80)
        await self.field('limit');await self.text('不限',81)
        self.assertEqual(self.store.get('proactive_daily_limit'),'unlimited')
        await self.field('limit');await self.text('20',82)
        self.assertEqual(self.store.get('proactive_daily_limit'),'20')
        await self.text('/proactive-limit unlimited',83)
        self.assertEqual(self.store.get('proactive_daily_limit'),'unlimited')
        self.assertEqual(self.llm.calls,[])
        self.assertEqual(self.store.recent(),[])

    async def test_records_pagination_and_unauthorized_button(self):
        for i in range(11):self.store.set_profile_fact('興趣','興趣'+str(i))
        await self.text('/settings',80);await self.callback('g.profile_records')
        buttons=[b for row in self.tg.call.call_args.args[1]['reply_markup']['inline_keyboard'] for b in row]
        self.assertEqual(sum(':pr.' in b['callback_data'] for b in buttons),8)
        await self.callback('page.1')
        buttons=[b for row in self.tg.call.call_args.args[1]['reply_markup']['inline_keyboard'] for b in row]
        self.assertEqual(sum(':pr.' in b['callback_data'] for b in buttons),3)
        before=self.app.settings_ui.session(42)
        await self.callback('g.root',user=99)
        self.assertEqual(self.app.settings_ui.session(42),before)

    async def test_poll_acknowledges_callback_and_requests_callback_updates(self):
        import asyncio
        self.tg.call.side_effect=[ [{'update_id':71,'callback_query':{'id':'q71','from':{'id':42},'data':'cfg:x:g.root','message':{'message_id':5,'chat':{'id':42,'type':'private'}}}}], True, asyncio.CancelledError() ]
        with self.assertRaises(asyncio.CancelledError):await self.app.poll()
        self.assertIn('callback_query',self.tg.call.call_args_list[0].args[1]['allowed_updates'])
        self.assertEqual(self.tg.call.call_args_list[1].args,('answerCallbackQuery',{'callback_query_id':'q71'}))
        self.assertEqual(self.store.next_update()[0],71)

    async def test_command_menu_registration_preserves_existing_commands(self):
        self.tg.call.side_effect=[[{'command':'status','description':'狀態'},{'command':'harness','description':'舊開關'}],True]
        await self.app.settings_ui.install_commands()
        payload=self.tg.call.call_args.args[1]
        self.assertEqual(payload['scope'],{'type':'chat','chat_id':42})
        self.assertEqual({c['command'] for c in payload['commands']},{'status','settings','cancel','help','photo','debug','web','news','search','outfit','panel'})
