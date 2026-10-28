from copy import deepcopy
import json
import unittest
from unittest.mock import AsyncMock
import test_pyramid_telegram as fixtures
from companion.app import parse_visual_plan
from companion.services import ServiceError

class UserPhotoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.TelegramPyramidTests.asyncSetUp(self)
        self.images.installed.return_value=True
        self.store.update_appearance('body','22歲成年女性，白金長髮、灰綠眼睛')
        state=self.app.pyramid.load()
        state['scene'].update(location='莊園花園',character_location='莊園花園',user_location='花園長椅旁',status='active',
                              positions={'user':{'relation':'near','target':'character'}})
        state['appearance'].update(hair='盤髮',clothing={},legacy_outfit='深綠色長裙')
        state['presentation']={'pose':{'value':'坐在長椅上'},'expression':{'value':'放鬆微笑'}}
        self.app.pyramid.save_clock(state)

    asyncTearDown=fixtures.TelegramPyramidTests.asyncTearDown
    text=fixtures.TelegramPyramidTests.text

    def plan(self,**changes):
        plan={'include_character':True,'scene':'莊園花園','pose':'坐在長椅上','expression':'放鬆微笑',
              'camera':'角色持手機自拍','view_direction':'front','camera_height':'eye_level','framing':'half_body'}
        plan.update(changes)
        self.llm.results=iter([json.dumps(plan,ensure_ascii=False)])

    def prompt(self):return self.store.db.execute('select prompt from jobs order by id desc limit 1').fetchone()[0]

    async def test_bare_photo_uses_existing_state_and_only_visual_planner(self):
        self.plan(include_character=False)
        before=deepcopy(self.app.pyramid.load())
        await self.text('/photo',1)
        prompt=self.prompt()
        for part in ('固定外觀：22歲成年女性','衣著：下裝：深綠色長裙','妝髮／髮型：盤髮','莊園花園','坐在長椅上','使用者視角','拍攝者位於畫面外'):
            self.assertIn(part,prompt)
        self.assertNotIn('自拍',prompt)
        self.assertEqual(len(self.llm.calls),1)
        payload=json.loads(self.llm.calls[0][1]['content'])
        self.assertEqual(payload['image_kind'],'user_photo')
        self.assertIn('relative_positions',payload['viewpoint_context'])
        after=self.app.pyramid.load()
        self.assertEqual(after['history'],before['history'])
        self.assertEqual(after['scene'],before['scene'])
        self.assertEqual(after['appearance'],before['appearance'])
        self.assertEqual(self.store.recent(),[])
        self.assertTrue(self.app.image_event.is_set())

    async def test_short_natural_description_compiled_into_composition(self):
        self.plan(view_direction='side',camera_height='low',framing='full_body')
        await self.text('/photo 從側面拍全身，鏡頭低一點',1)
        payload=json.loads(self.llm.calls[0][1]['content'])
        self.assertEqual(payload['user_request'],'從側面拍全身，鏡頭低一點')
        prompt=self.prompt()
        for phrase in ('角色側面','由下往上仰拍','全身構圖'):self.assertIn(phrase,prompt)
        self.assertNotIn('使用者要求',prompt)
        self.assertNotIn('從側面拍全身，鏡頭低一點',prompt)

    async def test_air_background_ignores_planner_and_old_world_furniture(self):
        self.store.update_appearance('visual_world','星港原創幻想世界，室內石牆、銀製燭台、四柱床')
        state=self.app.pyramid.load()
        state['scene'].update(location='星港學院競技場上空',character_location='星港學院競技場上空')
        self.app.pyramid.save_clock(state)
        self.plan(scene='莊園臥室',background='室內石牆、銀製燭台、四柱床')
        await self.text('/photo',1)
        prompt=self.prompt();background=prompt.split('背景：',1)[1].split('鏡頭與構圖：',1)[0]
        self.assertIn('高空視野',background);self.assertIn('下方',background)
        for wrong in ['銀製燭台','四柱床','室內石牆']:self.assertNotIn(wrong,background)
        payload=json.loads(self.llm.calls[0][1]['content'])
        self.assertNotIn('background_context',payload);self.assertNotIn('visual_world',payload)
        from companion.app import USER_PHOTO_SCHEMA
        self.assertNotIn('background',USER_PHOTO_SCHEMA['properties']);self.assertNotIn('scene',USER_PHOTO_SCHEMA['properties'])
        self.assertNotIn('framing',USER_PHOTO_SCHEMA['properties'])

    async def test_unrequested_model_framing_is_ignored(self):
        self.plan(framing='half_body')
        await self.text('/photo',1)
        self.assertNotIn('半身構圖',self.prompt())
        self.assertNotIn('全身構圖',self.prompt())
        self.assertNotIn('臉部近景',self.prompt())
        from companion.app import requested_photo_framing
        self.assertEqual(requested_photo_framing('不要半身'), '')
        self.assertEqual(requested_photo_framing('穿半身裙拍一張'), '')
        self.assertEqual(requested_photo_framing('不要半身，拍全身'), 'full_body')

    async def test_explicit_shot_size_survives_planner_failure(self):
        self.app.llm.generate=AsyncMock(side_effect=ServiceError('unavailable'))
        await self.text('/photo 從正面拍全身',1)
        self.assertIn('全身構圖',self.prompt());self.assertNotIn('半身構圖',self.prompt())

    async def test_dynamic_background_reuses_daily_plan_scope_without_model(self):
        from companion.scene_catalog_store import SceneCatalogStore
        state=self.app.pyramid.load()
        catalog=SceneCatalogStore.from_state(self.store,state)
        detail={'name':'河畔石橋','environment':'outdoor','background':'河畔石橋橫跨窄河，古老石欄杆沿橋面排列，岸邊有樹木與石板步道。'}
        catalog.register_plan([{'location':'河畔石橋'}],[detail],state['now'])
        state['scene'].update(location='河畔石橋',character_location='河畔石橋',pending={'location':'莊園臥室','delivery':'delivered'})
        self.app.pyramid.save_clock(state)
        snapshot=self.app.visual_snapshot('user_photo')
        prompt=self.app.visual_prompt('user_photo','拍照',{},snapshot=snapshot)
        self.assertEqual(prompt.count(detail['background']),1)
        self.assertNotIn('四柱床',prompt);self.assertFalse(self.llm.calls)
        self.assertFalse(snapshot['background_context']['semantically_verified'])

    async def test_unknown_background_keeps_location_not_planner_replacement(self):
        state=self.app.pyramid.load();state['scene'].update(location='未知山谷',character_location='未知山谷',status='inactive')
        self.app.pyramid.save_clock(state)
        prompt=self.app.visual_prompt('user_photo','拍照',{'scene':'莊園臥室','background':'四柱床與燭台'})
        self.assertIn('背景：未知山谷。',prompt);self.assertNotIn('四柱床',prompt)

    async def test_failed_planner_uses_canonical_state_and_viewpoint(self):
        self.app.llm.generate=AsyncMock(side_effect=ServiceError('unavailable'))
        await self.text('/photo',1)
        prompt=self.prompt()
        for phrase in ('固定外觀','深綠色長裙','盤髮','莊園花園','坐在長椅上','使用者視角'):self.assertIn(phrase,prompt)
        self.assertNotIn('半身構圖',prompt)
        self.assertNotIn('自拍',prompt)
        self.assertEqual(self.store.recent(),[])

    async def test_missing_renderer_or_full_queue_skips_planner(self):
        self.images.installed.return_value=False
        await self.text('/photo',1)
        self.assertEqual(self.llm.calls,[])
        self.assertEqual(self.store.jobs(),[])
        self.images.installed.return_value=True
        for i in range(5):self.store.enqueue_image(i+20,42,'test')
        await self.text('/photo',2)
        self.assertEqual(self.llm.calls,[])
        self.assertEqual(len(self.store.jobs()),5)

    async def test_long_description_rejected_and_invalid_enum_is_not_rendered(self):
        with self.assertRaises(ValueError):await self.text('/photo '+('字'*1001),1)
        self.assertEqual(self.store.jobs(),[])
        with self.assertRaises(ValueError):parse_visual_plan(json.dumps({'view_direction':'角色自拍'}))

    async def test_original_selfie_and_character_pov_modes_are_preserved(self):
        snapshot=self.app.visual_snapshot()
        selfie=self.app.visual_prompt('selfie','拍照',{'camera':'角色手持相機自拍'},snapshot=snapshot)
        pov=self.app.visual_prompt('pov','眼前景色',{},snapshot=snapshot)
        self.assertIn('角色手持相機自拍',selfie)
        self.assertNotIn('使用者視角',selfie)
        self.assertNotIn('固定外觀',pov)
        self.assertIn('角色第一人稱所見',pov)

    async def test_photo_uses_character_location_when_people_are_apart(self):
        state=self.app.pyramid.load();state['scene'].update(location='浴室',character_location='書房',user_location='浴室')
        self.app.pyramid.save_clock(state)
        self.plan(scene='書房')
        await self.text('/photo',1)
        payload=json.loads(self.llm.calls[0][1]['content'])
        self.assertEqual(payload['current_scene'],'書房')
        self.assertEqual(payload['viewpoint_context']['user_location'],'浴室')
        self.assertIn('背景：書房',self.prompt())
        self.assertEqual(self.app.pyramid.load()['scene']['user_location'],'浴室')

    async def test_photo_worker_preserves_mode_prompt_inspection_and_deletes_file(self):
        import asyncio
        delivered=asyncio.Event()
        self.plan();await self.text('/photo',1)
        original=self.prompt()
        self.assertEqual(self.store.db.execute('select kind from jobs').fetchone()[0],'user_photo')
        async def generate(prompt,path):path.write_bytes(b'image-test')
        async def upload(*args,**kwargs):delivered.set()
        self.images.generate.side_effect=generate;self.tg.upload.side_effect=upload
        task=asyncio.create_task(self.app.image_worker())
        try:
            await asyncio.wait_for(delivered.wait(),1)
            self.assertEqual(self.store.jobs()[0]['status'],'done')
            self.assertEqual(self.store.get('last_prompt'),original)
            self.assertEqual(self.store.get('last_prompt_kind'),'image')
            self.assertEqual(self.tg.upload.call_args.kwargs['caption'],'這個角度的照片，給你。')
            self.assertFalse(list((self.config.data_dir/'images').glob('*.png')))
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True)
