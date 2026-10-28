from __future__ import annotations
from unittest.mock import patch as mock_capacity

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx

from companion.app import (
    App, apply_visual_action, detect_visual_request, parse_relationship_analysis, parse_visual_plan,
    visual_reply_context,
)
from companion.character_profile import CharacterProfile
from companion.config import Config
from companion.life import in_quiet_hours, parse_daily_plan, parse_proactive_decision
from companion.services import GPTReviewer, Images, LLM, ServiceError, StreamingReply, Telegram, Voice, complete_text, run_process, visible_text
from companion.store import Store


def update(identifier=1, user=42, text="你好", private=True):
    return {"update_id": identifier, "message": {"chat": {"id": 42, "type": "private" if private else "group"}, "from": {"id": user}, "text": text}}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "memory.sqlite3"
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def test_allowlist_private_chat_and_dedup(self):
        for event in (update(), update(), update(2, user=99), update(3, private=False)):
            self.store.ingest(event, 42)
        self.assertEqual(self.store.next_update()[0], 1)
        self.assertIsNone(self.store.next_update())
        self.assertEqual(self.store.get("offset"), "4")

    def test_unauthorized_payload_not_saved(self):
        self.store.ingest(update(user=99, text="secret"), 42)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM inbox").fetchone()[0], 0)

    def test_stale_update_cannot_rewind_offset(self):
        self.store.ingest(update(10), 42)
        self.store.ingest(update(2), 42)
        self.assertEqual(self.store.get("offset"), "11")

    def test_restart_persists_persona_memory_chat_and_pending(self):
        self.store.update_persona("name", "阿明")
        self.store.remember("喜歡紅茶")
        self.store.add_message("user", "明天有約")
        self.store.ingest(update(), 42)
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.persona()["name"], "阿明")
        self.assertEqual(self.store.memories()[0]["content"], "喜歡紅茶")
        self.assertEqual(self.store.recent()[0]["content"], "明天有約")
        self.assertEqual(self.store.next_update()[0], 1)

    def test_reset_keeps_persona_and_explicit_memories(self):
        self.store.remember("喜歡紅茶")
        self.store.set("summary", "摘要")
        with self.store.db:
            self.store.apply_conversation_state({"topic": "舊話題", "scene_status": "active", "scene": "舊場景"})
        self.store.add_message("user", "歷史")
        self.store.reset()
        self.assertEqual(self.store.recent(), [])
        self.assertEqual(self.store.get("summary"), "")
        self.assertEqual(len(self.store.memories()), 1)
        self.assertEqual(self.store.conversation_state()["scene_status"], "none")

    def test_forget_removes_old_context(self):
        identifier = self.store.remember("秘密")
        self.store.add_message("user", "秘密")
        self.store.set("summary", "秘密")
        self.assertTrue(self.store.forget(identifier))
        self.assertNotIn("秘密", json.dumps(self.store.context(), ensure_ascii=False))

    def test_summary_keeps_recent_messages(self):
        for number in range(24):
            self.store.add_message("user" if number % 2 == 0 else "assistant", f"訊息{number}")
        batch = self.store.summary_batch()
        self.assertEqual(len(batch), 12)
        self.store.save_summary("摘要", batch[-1]["id"])
        self.assertEqual(self.store.summary_batch(), [])
        self.assertEqual(self.store.recent_since_summary()[-1]["content"], "訊息23")
        self.assertIn("訊息23", str(self.store.context()))

    def test_summary_starts_before_a_context_gap_can_form(self):
        for number in range(18):
            self.store.add_message("user" if number % 2 == 0 else "assistant", f"訊息{number}")
        batch = self.store.summary_batch()
        self.assertEqual([item["content"] for item in batch], [f"訊息{i}" for i in range(6)])
        self.store.save_summary("訊息0到訊息5的摘要", batch[-1]["id"])
        self.assertEqual(
            [item["content"] for item in self.store.recent_since_summary()],
            [f"訊息{i}" for i in range(6, 18)],
        )

    def test_context_never_repeats_messages_already_in_summary(self):
        for number in range(8):
            self.store.add_message("user" if number % 2 == 0 else "assistant", f"訊息{number}")
        self.store.save_summary("舊摘要", 4)
        context = self.store.context()
        self.assertEqual([item['content'] for item in context[1:]], [f'訊息{i}' for i in range(6, 8)])

    def test_generation_context_uses_recent_dialogue_but_not_derived_control_state(self):
        self.store.add_message("user", "第一個問題")
        self.store.add_message("assistant", "不可成為下一輪範本的很長角色台詞")
        self.store.record_turn_state("第一個問題", "我答應明天再談。後面是角色台詞。")
        self.store.add_message("user", "第二個問題")
        context = self.store.context()
        rendered = json.dumps(context, ensure_ascii=False)
        self.assertEqual([item["role"] for item in context], ["system", "user", "assistant", "user"])
        self.assertIn("第一個問題", rendered)
        self.assertNotIn("我答應明天再談", rendered)
        self.assertIn("不可成為下一輪範本", rendered)
        self.assertTrue(context[-1]["content"].endswith("第二個問題"))

    def test_recovery_does_not_repeat_in_progress_work(self):
        self.store.ingest(update(), 42)
        self.store.next_update()
        identifier = self.store.enqueue_image(2, 42, "風景")
        self.store.next_job()
        self.assertEqual(self.store.recover(), [42])
        self.assertIsNone(self.store.next_update())
        self.assertIsNone(self.store.next_job())
        self.assertEqual(self.store.jobs()[0]["status"], "failed")
        self.assertEqual(self.store.enqueue_image(2, 42, "風景"), identifier)

    def test_recovery_preserves_queued_images(self):
        self.store.enqueue_image(2, 42, "風景")
        self.store.recover()
        self.assertIsNotNone(self.store.next_job())

    def test_recovery_removes_terminal_image_files(self):
        path = self.path.parent / "images" / "image-1.png"
        path.parent.mkdir()
        path.write_bytes(b"image")
        identifier = self.store.enqueue_image(9, 42, "風景")
        self.store.update_job(identifier, "done", path=str(path))
        self.store.recover()
        self.assertFalse(path.exists())
        self.assertIsNone(self.store.db.execute("SELECT path FROM jobs WHERE id=?", (identifier,)).fetchone()[0])

    def test_completed_update_drops_duplicate_payload(self):
        self.store.ingest(update(text="秘密"), 42)
        self.store.finish_update(1)
        self.assertEqual(self.store.db.execute("SELECT payload FROM inbox").fetchone()[0], "{}")

    def test_traits_and_relationship_persist(self):
        self.store.update_trait("teasing", 81)
        self.store.set_relationship("trust", 63)
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.traits()["teasing"], 81)
        self.assertEqual(self.store.relationship()["trust"], 63)

    def test_character_profile_aggregates_definition_and_runtime_state(self):
        self.store.update_persona("name", "測試角色")
        self.store.update_trait("teasing", 81)
        self.store.update_appearance("body", "黑色長髮、棕色眼睛")
        profile = self.store.character_profile()
        self.assertIsInstance(profile, CharacterProfile)
        self.assertEqual(profile.story.name, "測試角色")
        self.assertEqual(profile.traits["teasing"], 81)
        self.assertEqual(profile.appearance.body, "黑色長髮、棕色眼睛")
        self.assertIn("joy", profile.affect.emotions)
        self.assertEqual(set(profile.motivation.need_weights), {
            "relatedness", "autonomy", "competence", "curiosity", "homeostasis", "erotic_connection"})
        self.assertEqual(profile.adult_intimacy.responsive_desire, 65)

    def test_new_character_can_use_plain_parameters_without_store_access(self):
        profile = CharacterProfile.from_dict({
            "story": {"name": "另一個角色", "background": "旅行者", "personality": "冷靜", "tone": "簡短"},
            "traits": {"openness": 90},
            "affect": {"emotions": {"joy": 20}},
            "motivation": {"need_weights": {"curiosity": 1.8, "relatedness": .5}},
            "adult_intimacy": {"flirtatiousness": 88, "inhibition_sensitivity": 25},
            "appearance": {"body": "銀髮", "clothing": "旅行服", "accessories": "地圖袋"},
            "physical": {"energy": 44, "stress": 31, "location": "車站", "activity": "候車", "availability": "free"},
        })
        self.assertEqual(profile.story.name, "另一個角色")
        self.assertEqual(profile.traits["openness"], 90)
        self.assertEqual(profile.motivation.need_weights["curiosity"], 1.8)
        self.assertEqual(profile.adult_intimacy.flirtatiousness, 88)
        self.assertEqual(profile.state_dict()["activity"], "候車")
        self.assertEqual(CharacterProfile.from_dict(profile.to_dict()), profile)

    def test_relationship_growth_is_queued_and_analysis_is_bounded(self):
        before = self.store.relationship()
        review_id = self.store.queue_relationship_review("謝謝你", "不用客氣")
        self.assertGreater(self.store.relationship()["familiarity"], before["familiarity"])
        review = self.store.next_relationship_review()
        self.assertEqual(review["id"], review_id)
        applied = self.store.apply_relationship_analysis(
            review_id,
            {"trust": 999, "tension": -999, "unknown": 10},
            "測試限制",
        )
        self.assertEqual(applied["trust"], 1)
        self.assertNotIn("unknown", applied)
        self.assertEqual(self.store.pending_relationship_reviews(), 0)

    def test_mutual_consensual_completed_sexting_updates_specific_relationship_axes(self):
        user_text = "我同意繼續這段親密文字互動"
        assistant_text = "好，我也願意；結束後我們就休息。"
        review_id = self.store.queue_relationship_review(user_text, assistant_text)
        before = self.store.relationship()
        applied = self.store.apply_relationship_analysis(
            review_id, {}, "無一般關係變化", adult_intimacy_event={
                "kind": "sexting", "participation": "mutual", "consent": "explicit",
                "completion": "completed", "boundary": "respected",
                "user_evidence": user_text, "assistant_evidence": "好，我也願意",
            },
        )
        after = self.store.relationship()
        self.assertEqual(applied["sexual_comfort"], 2.0)
        self.assertGreater(after["familiarity"], before["familiarity"])
        self.assertGreater(after["comfort"], before["comfort"])
        self.assertGreater(after["intimate_safety"], before["intimate_safety"])
        self.assertEqual(after["trust"], before["trust"])

    def test_one_sided_or_unquoted_adult_event_cannot_create_positive_growth(self):
        review_id = self.store.queue_relationship_review("談談成人親密話題", "我聽見了")
        before = self.store.relationship()
        applied = self.store.apply_relationship_analysis(
            review_id, {}, "無", adult_intimacy_event={
                "kind": "sexting", "participation": "user_only", "consent": "explicit",
                "completion": "completed", "boundary": "respected",
                "user_evidence": "不存在的引文", "assistant_evidence": "我聽見了",
            },
        )
        self.assertNotIn("sexual_comfort", applied)
        self.assertEqual(self.store.relationship()["sexual_comfort"], before["sexual_comfort"])

    def test_withdrawn_consent_reduces_intimate_safety(self):
        review_id = self.store.queue_relationship_review("停下，我不想繼續", "但我仍繼續了")
        before = self.store.relationship()
        self.store.apply_relationship_analysis(
            review_id, {}, "無", adult_intimacy_event={
                "kind": "sexting", "participation": "mutual", "consent": "withdrawn",
                "completion": "interrupted", "boundary": "violated",
                "user_evidence": "停下，我不想繼續", "assistant_evidence": "但我仍繼續了",
            },
        )
        after = self.store.relationship()
        self.assertLess(after["intimate_safety"], before["intimate_safety"])
        self.assertGreater(after["tension"], before["tension"])

    def test_writer_context_excludes_upstream_scores_and_relationship_stage(self):
        self.store.update_trait("impulsiveness", 88)
        system = self.store.context()[0]["content"]
        self.assertIn("<current_facts>", system)
        self.assertNotIn("deliberation", system)
        self.assertNotIn("目前關係階段", system)
        self.assertNotIn('"impulsiveness"', system)
        self.assertNotIn('"familiarity"', system)

    def test_writer_separates_character_identity_from_unknown_user(self):
        self.store.update_persona("name", "林青・青葉")
        self.store.add_message("user", "晚上好")
        context = self.store.context()
        system = context[0]["content"]
        current = context[-1]["content"]
        self.assertIn('"assistant_character_name": "林青・青葉"', system)
        self.assertIn('"user_name": null', system)
        self.assertIn("The user's name is unknown", current)
        self.assertIn("Never address the user with your own character name", current)

    def test_writer_uses_only_explicit_profile_name_for_user_identity(self):
        self.store.update_persona("name", "林青・青葉")
        self.store.set_profile_fact("稱呼", "希望被叫阿明")
        self.store.add_message("user", "晚上好")
        context = self.store.context()
        self.assertEqual(self.store.user_display_name(), "阿明")
        self.assertIn('"user_name": "阿明"', context[0]["content"])
        self.assertIn('explicitly known name/address is "阿明"', context[-1]["content"])

    def test_relationship_values_compile_to_observable_behavior(self):
        low = self.store.relationship_behavior()
        self.assertIn("保持中性", low)
        self.assertIn("維持清楚界線", low)
        for name in ("familiarity", "trust", "affection", "comfort", "respect", "intimacy", "initiative"):
            self.store.set_relationship(name, 90)
        high = self.store.relationship_behavior()
        self.assertIn("自然引用真實記憶", high)
        self.assertIn("清楚表現偏愛", high)
        self.assertIn("主動補一個具體想法或追問", high)
        self.assertNotEqual(low, high)

    def test_persona_tone_controls_language_instead_of_a_fixed_language_rule(self):
        self.store.update_persona("tone", "Use concise English")
        self.store.add_message("user", "你好")
        context = self.store.context()
        system = context[0]["content"]
        self.assertIn("<target_language>English</target_language>", system)
        self.assertIn("Use concise English", system)
        self.assertEqual(self.store.output_language(), "English")
        self.assertIn("Reply only in natural English", context[-1]["content"])
        self.assertEqual(self.store.recent()[-1]["content"], "你好")

    def test_language_override_is_independent_of_persona_tone(self):
        self.store.update_persona("tone", "Use concise English")
        self.assertEqual(self.store.set_output_language("jp"), "Japanese")
        self.assertEqual(self.store.output_language(), "Japanese")
        self.store.update_persona("tone", "請使用繁體中文")
        self.assertEqual(self.store.output_language(), "Japanese")
        self.store.add_message("user", "こんにちは")
        context = self.store.context()
        self.assertIn("<target_language>Japanese</target_language>", context[0]["content"])
        self.assertNotIn("請使用繁體中文", context[0]["content"])
        self.assertIn("<language_control>自然な日本語だけで回答", context[-1]["content"])

    def test_reset_chat_preserves_relationship_but_explicit_relationship_reset_does_not(self):
        self.store.set_relationship("trust", 80)
        self.store.reset()
        self.assertEqual(self.store.relationship()["trust"], 80)
        self.store.reset_relationship()
        self.assertEqual(self.store.relationship()["trust"], 15)

    def test_profile_starts_blank_and_has_inspectable_file(self):
        self.assertEqual(self.store.profile_facts(), [])
        content = (self.path.parent / "user-profile.md").read_text()
        self.assertIn("目前空白", content)

    def test_profile_can_add_update_delete_and_render(self):
        identifier = self.store.set_profile_fact("飲食偏好", "喜歡無糖紅茶")
        self.assertTrue(self.store.update_profile_fact(identifier, "飲食偏好", "喜歡無糖綠茶"))
        self.assertIn("喜歡無糖綠茶", self.store.context()[0]["content"])
        self.assertIn("喜歡無糖綠茶", (self.path.parent / "user-profile.md").read_text())
        self.assertTrue(self.store.delete_profile_fact(identifier))
        self.assertEqual(self.store.profile_facts(), [])

    def test_analysis_profile_ops_are_bounded_and_support_correction(self):
        review_id = self.store.queue_relationship_review("我喜歡紅茶", "記住了")
        self.store.apply_relationship_analysis(review_id, {}, "無", [
            {"action": "add", "category": "飲食偏好", "value": "喜歡紅茶"},
            {"action": "add", "category": "稱呼", "value": "希望被叫阿明"},
            {"action": "add", "category": "多餘", "value": "第三筆不應套用"},
        ])
        facts = self.store.profile_facts()
        self.assertEqual(len(facts), 2)
        identifier = facts[0]["id"]
        second = self.store.queue_relationship_review("我改喝咖啡", "好")
        self.store.apply_relationship_analysis(second, {}, "修正", [
            {"action": "update", "id": identifier, "category": "飲食偏好", "value": "喜歡咖啡"}
        ])
        self.assertEqual(self.store.profile_facts()[0]["value"], "喜歡咖啡")

    @staticmethod
    def plan():
        return [
            {"start": start, "start_minute": hour * 60, "activity": activity, "location": "家中",
             "outcome": f"完成{activity}", "mood": "平靜", "availability": availability,
             "share_worthy": share, "clothing": "日常服裝", "accessories": "手錶"}
            for hour, start, activity, availability, share in [
                (0, "00:00", "睡覺", "sleep", False), (8, "08:00", "吃早餐", "free", True),
                (9, "09:00", "工作", "busy", False), (12, "12:00", "午休", "free", False),
                (14, "14:00", "看書", "free", True), (18, "18:00", "做晚餐", "free", True),
            ]
        ]

    def test_life_plan_advances_and_creates_share_candidate_once(self):
        zone = ZoneInfo("Asia/Taipei")
        self.store.save_daily_plan("2026-10-13", self.plan(), 8 * 60 + 30)
        completed = self.store.advance_life(datetime(2026, 10, 13, 9, 5, tzinfo=zone))
        self.assertEqual(completed[0]["activity"], "吃早餐")
        candidate = self.store.next_proactive_candidate(datetime(2026, 10, 13, 9, 6, tzinfo=zone))
        self.assertIn("吃早餐", candidate["context"])
        self.store.finish_proactive_candidate(candidate["id"], "sent")
        self.store.advance_life(datetime(2026, 10, 13, 9, 10, tzinfo=zone))
        self.assertIsNone(self.store.next_proactive_candidate(datetime(2026, 10, 13, 9, 11, tzinfo=zone)))

    def test_user_activity_resets_proactive_backoff(self):
        self.store.set("proactive_unanswered", "3")
        self.store.touch_user()
        self.assertEqual(self.store.get("proactive_unanswered"), "0")

    def test_recovery_retries_only_pre_delivery_candidate(self):
        now = datetime.now(ZoneInfo("Asia/Taipei"))
        with self.store.db:
            self.store.db.execute("INSERT INTO proactive_candidates(kind,source_key,due_at,context,status) VALUES('life_event','a',?,'{}','running')", (now.isoformat(),))
            self.store.db.execute("INSERT INTO proactive_candidates(kind,source_key,due_at,context,status) VALUES('life_event','b',?,'{}','sending')", (now.isoformat(),))
        self.store.recover()
        states = [row[0] for row in self.store.db.execute("SELECT status FROM proactive_candidates ORDER BY id")]
        self.assertEqual(states, ["pending", "failed"])


class LifeParserTests(unittest.TestCase):
    def test_parses_fenced_plan_and_decision(self):
        activities = StoreTests.plan()
        for item in activities:
            item.pop("start_minute")
        parsed = parse_daily_plan("```json\n" + json.dumps({"activities": activities}, ensure_ascii=False) + "\n```")
        self.assertEqual(parsed[1]["start_minute"], 480)
        decision = parse_proactive_decision('text {"send":true,"message":" 剛做好早餐！ ","reason":"自然分享"}')
        self.assertEqual(decision["message"], "剛做好早餐！")

    def test_quiet_hours_wrap_midnight(self):
        zone = ZoneInfo("Asia/Taipei")
        self.assertTrue(in_quiet_hours(datetime(2026, 1, 1, 23, 30, tzinfo=zone), "23:00", "08:00"))
        self.assertTrue(in_quiet_hours(datetime(2026, 1, 1, 7, 59, tzinfo=zone), "23:00", "08:00"))
        self.assertFalse(in_quiet_hours(datetime(2026, 1, 1, 12, 0, tzinfo=zone), "23:00", "08:00"))

    def test_plan_normalizes_missing_midnight_and_share_flags(self):
        activities = StoreTests.plan()[1:]
        for item in activities:
            item.pop("start_minute")
            item.pop("share_worthy")
        parsed = parse_daily_plan(json.dumps({"activities": activities}, ensure_ascii=False))
        self.assertEqual(parsed[0]["start"], "00:00")
        self.assertTrue(any(item["share_worthy"] for item in parsed))

    def test_natural_visual_request_detection_avoids_capability_questions(self):
        self.assertEqual(detect_visual_request("你現在看到什麼？"), "pov")
        self.assertEqual(detect_visual_request("拍張你現在的照片給我"), "selfie")
        self.assertEqual(detect_visual_request("優雅。 我想看照片！"), "selfie")
        self.assertEqual(detect_visual_request("我想看你的照片"), "selfie")
        self.assertEqual(detect_visual_request("畫一張剛才說的花園圖片"), "general")
        self.assertIsNone(detect_visual_request("你會畫圖嗎？"))
        self.assertIsNone(detect_visual_request("我想聊聊圖片生成技術"))

    def test_visual_action_tells_writer_what_application_will_do(self):
        messages = [{"role": "user", "content": "我想看照片"}]
        controlled = apply_visual_action(messages, "selfie", True)
        self.assertIn("will queue a character selfie", controlled[-1]["content"])
        self.assertIn("Do not claim the image was already", controlled[-1]["content"])
        self.assertEqual(messages[-1]["content"], "我想看照片")

    def test_followup_photo_requests_need_recent_photo_context(self):
        recent = [{"role": "user", "content": "拍一張照片給我"}]
        for text in ("真美麗 換個角度再來一張好嗎？ 眼睛可以閉起來", "調好囉 快門給妳按", "沒看到照片呀2"):
            self.assertEqual(detect_visual_request(text, recent), "selfie")
        self.assertIsNone(detect_visual_request("再來一張", []))
        self.assertIsNone(detect_visual_request("給我再來一張報表", []))

    def test_visual_plan_parser_keeps_structured_visual_fields(self):
        plan = parse_visual_plan(json.dumps({
            "include_character": True,
            "scene": "臥室窗邊",
            "pose": "坐在床沿",
            "expression": "輕微挑眉",
            "clothing": "深綠色睡袍",
            "accessories": "銀戒指",
            "hair_state": "略微凌亂",
            "camera": "手機半身自拍",
            "identity_change": "",
            "identity_change_evidence": "",
        }, ensure_ascii=False))
        self.assertEqual(plan["clothing"], "深綠色睡袍")
        self.assertEqual(plan["hair_state"], "略微凌亂")

    def test_visual_reply_context_removes_spoken_line_but_keeps_visible_action(self):
        answer = "「妳腦子裡在想什麼……」她臉頰漲紅，用魔杖撥弄耳邊凌亂的長髮。"
        context = visual_reply_context(answer)
        self.assertNotIn("腦子裡", context)
        self.assertIn("臉頰漲紅", context)
        self.assertIn("撥弄耳邊凌亂的長髮", context)

class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.config = replace(Config.load(require_credentials=False), data_dir=self.directory, allowed_user=42, token="test-token", chat_core="legacy")
        self.store = Store(self.directory / "memory.sqlite3")
        self.telegram = AsyncMock(spec=Telegram)
        self.telegram.send.return_value = {"message_id": 17}
        self.llm = AsyncMock(spec=LLM)
        self.llm.last_metrics = {"first_text_seconds": 0.1}
        self.voice = AsyncMock(spec=Voice)
        self.images = AsyncMock(spec=Images)
        self.images.installed.return_value = True
        self.app = App(self.config, self.store, self.telegram, self.llm, self.voice, self.images)
        # Scene extraction has its own integration tests; isolate dialogue
        # tests from this additional structured-model call.
        self.app.extract_scene_events = AsyncMock()
        self.app.gpt_reviewer = AsyncMock(spec=GPTReviewer)

    async def asyncTearDown(self):
        self.store.close()
        self.temporary.cleanup()

    async def test_visual_prompt_fallback_keeps_immediate_visible_state(self):
        answer = "「才不要。」她耳根紅透，咬著下唇，用魔杖撥弄凌亂的白金色長髮。"
        prompt = self.app.visual_prompt("selfie", "拍一張妳現在的照片", {}, answer)
        self.assertIn("耳根紅透", prompt)
        self.assertIn("咬著下唇", prompt)
        self.assertIn("撥弄凌亂的白金色長髮", prompt)
        self.assertNotIn("才不要", prompt)

    async def test_visual_sections_order_request_priority_and_no_duplication(self):
        snapshot={'identity_anchors':'成年白金髮女性','current_clothing':'白襯衫',
                  'current_accessories':'銀耳環','current_scene':'Manor Bedroom',
                  'visual_world':'星港幻想貴族莊園，石牆、四柱床、燭台，無現代電子設備',
                  'scene_facts':{'status':'active','facts':{'character.expression':'不耐煩'}}}
        prompt=self.app.visual_prompt('selfie','嘴巴張大然後自拍一張',
            {'expression':'羞怯無助','pose':'坐在床沿','camera':'近距離正面',
             'background':'深綠床幔與古老木雕'},snapshot=snapshot)
        labels=['固定外觀：','衣著：','表情：','姿勢：','背景：','鏡頭與構圖：']
        self.assertEqual([prompt.index(x) for x in labels],sorted(prompt.index(x) for x in labels))
        self.assertEqual(prompt.count('姿勢：'),1)
        self.assertIn('表情：嘴巴張大',prompt)
        self.assertIn('四柱床',prompt)
        self.assertNotIn('使用者要求',prompt)
        self.assertNotIn('無助',prompt)

    async def test_visual_planner_receives_completed_changes_from_previous_turns(self):
        self.store.add_message("user", "頭髮盤好了，我從後面給妳拍")
        self.store.add_message("assistant", "好，快拍。")
        self.llm.generate.return_value = json.dumps({
            "hair_state": "白金色長髮盤成髮髻", "camera": "由使用者從背後拍攝",
        }, ensure_ascii=False)
        plan = await self.app.plan_visual("selfie", "拍照片", "好，快拍。")
        payload = json.loads(self.llm.generate.call_args.args[0][1]["content"])
        self.assertIn("頭髮盤好了", payload["recent_dialogue"][0]["content"])
        prompt = self.app.visual_prompt("selfie", "拍照片", plan)
        self.assertIn("盤成髮髻", prompt)
        self.assertIn("由使用者從背後拍攝", prompt)
        self.assertNotIn("自拍構圖", prompt)

    async def test_shutter_followup_does_not_duplicate_pending_image(self):
        self.store.add_message("user", "拍一張照片給我")
        self.store.enqueue_image(1, 42, "角色照片")
        await self.app.handle(2, update(identifier=2, text="調好囉 快門給妳按")["message"])
        self.assertEqual(len(self.store.jobs()), 1)
        self.llm.generate.assert_not_awaited()
        self.telegram.send.assert_awaited_with(42, "照片還沒傳好，等我一下。")

    async def test_natural_chat_never_generates_images(self):
        self.llm.generate.return_value = "可以聊聊這個主題。"
        await self.app.handle(1, update(text="我想聊聊圖片")['message'])
        self.assertEqual(self.store.jobs(), [])
        self.images.generate.assert_not_called()
        self.assertEqual(self.store.recent()[-1]["role"], "assistant")
        self.assertEqual(self.store.pending_relationship_reviews(), 1)

    async def test_failed_delivery_does_not_create_shared_state(self):
        self.llm.generate.return_value = '我會在圖書館等你。'
        self.telegram.send.side_effect = ServiceError('uncertain delivery')
        with self.assertRaises(ServiceError):
            await self.app.handle(1, update(text='你在哪裡？')['message'])
        self.assertEqual(self.store.pending_relationship_reviews(), 0)
        self.assertTrue(all(not fact['disclosed'] for fact in self.store.engine.knowledge()))
        self.assertNotIn('圖書館', json.dumps(self.store.engine.knowledge(), ensure_ascii=False))
        self.assertNotIn('我會在圖書館等你', json.dumps(self.store.context(), ensure_ascii=False))

    async def test_asking_what_character_sees_queues_pov_after_text_reply(self):
        self.llm.generate.side_effect = [
            "窗外是剛亮起來的街道，還有一點薄霧。",
            json.dumps({
                "include_character": False,
                "scene": "窗外剛亮起來的街道與薄霧",
                "pose": "",
                "expression": "",
                "clothing": "",
                "accessories": "",
                "hair_state": "",
                "camera": "第一人稱視角",
                "identity_change": "",
                "identity_change_evidence": "",
            }, ensure_ascii=False),
        ]
        now = self.app.now()
        self.store.save_daily_plan(now.date().isoformat(), StoreTests.plan(), now.hour * 60 + now.minute)
        await self.app.handle(12, update(identifier=12, text="你現在看到什麼？")['message'])
        job = self.store.db.execute("SELECT prompt,status FROM jobs WHERE update_id=12").fetchone()
        self.assertIsNotNone(job)
        self.assertEqual(job["status"], "queued")
        self.assertIn("第一人稱視角", job["prompt"])
        self.assertNotIn("窗外剛亮起來的街道", job["prompt"])
        self.assertIn('背景：'+self.app.visual_snapshot()['current_scene'],job['prompt'])
        self.assertTrue(self.app.image_event.is_set())

    async def test_natural_selfie_uses_fixed_and_current_appearance(self):
        self.store.update_appearance("body", "白金長髮、灰綠眼睛的成年女性")
        self.store.update_appearance("clothing", "黑色長外套")
        self.llm.generate.return_value = "勉強拍一張給你，別太期待。"
        await self.app.handle(13, update(identifier=13, text="拍張你現在的照片給我")['message'])
        prompt = self.store.db.execute("SELECT prompt FROM jobs WHERE update_id=13").fetchone()[0]
        self.assertIn("白金長髮", prompt)
        self.assertIn("黑色長外套", prompt)

    async def test_bare_photo_request_queues_selfie_and_controls_writer(self):
        self.store.update_appearance("body", "白金長髮、灰綠眼睛的成年女性")
        self.llm.generate.return_value = "好，我拍一張；生成好之後再傳給你。"
        await self.app.handle(14, update(identifier=14, text="優雅。 我想看照片！")['message'])
        prompt = self.store.db.execute("SELECT prompt FROM jobs WHERE update_id=14").fetchone()[0]
        writer_messages = self.llm.generate.await_args_list[0].args[0]
        self.assertIn("白金長髮", prompt)
        self.assertIn("<visual_action>", writer_messages[-1]["content"])
        self.assertIn("will queue a character selfie", writer_messages[-1]["content"])

    async def test_visual_planner_output_replaces_full_character_reply_in_image_prompt(self):
        self.store.update_appearance("body", "白金長髮、灰綠眼睛的成年女性")
        self.llm.generate.side_effect = [
            "哼，我換好睡袍再拍給你，別催。",
            json.dumps({
                "include_character": True,
                "scene": "莊園臥室",
                "pose": "坐在床沿自拍",
                "expression": "克制的微笑",
                "clothing": "深綠色睡袍",
                "accessories": "銀戒指",
                "hair_state": "洗澡後略濕",
                "camera": "手機半身構圖",
                "identity_change": "",
                "identity_change_evidence": "",
            }, ensure_ascii=False),
        ]
        await self.app.handle(15, update(identifier=15, text="換睡袍拍張照片給我")['message'])
        prompt = self.store.db.execute("SELECT prompt FROM jobs WHERE update_id=15").fetchone()[0]
        self.assertIn("固定外觀：白金長髮、灰綠眼睛", prompt)
        self.assertIn("衣著：深綠色睡袍", prompt)
        self.assertIn("妝髮／髮型：洗澡後略濕", prompt)
        self.assertNotIn("哼，我換好睡袍再拍給你", prompt)
        self.assertNotIn("暫時外型變化", prompt)

    async def test_image_command_enqueues_without_llm(self):
        await self.app.handle(1, update(text="/image 一隻貓")['message'])
        self.assertEqual(self.store.jobs()[0]["status"], "queued")
        self.llm.generate.assert_not_called()
        self.assertTrue(self.app.image_event.is_set())

    async def test_empty_image_prompt_rejected(self):
        with self.assertRaises(ValueError):
            await self.app.handle(1, update(text="/image")['message'])
        self.assertEqual(self.store.jobs(), [])

    async def test_image_runtime_not_ready_does_not_enqueue(self):
        self.images.installed.return_value = False
        with self.assertRaises(ServiceError):
            await self.app.handle(1, update(text="/image 一隻貓")['message'])
        self.assertEqual(self.store.jobs(), [])

    async def test_voice_replies_text_before_synthesis(self):
        events = []
        self.voice.transcribe.return_value = "早安"
        self.llm.generate.return_value = "早安，今天想聊什麼？"

        async def send(chat_id, text):
            events.append("text")
            return {"message_id": 17}

        async def synthesize(text, directory):
            events.append("synthesis")
            return directory / "reply.ogg"

        self.telegram.send.side_effect = send
        self.voice.synthesize.side_effect = synthesize
        message = update()['message']
        message.pop("text")
        message["voice"] = {"file_id": "test", "duration": 2, "file_size": 1000}
        await self.app.handle(1, message)
        self.assertEqual(events[-2:], ["text", "synthesis"])
        self.telegram.upload.assert_awaited_once()

    async def test_oversized_voice_is_not_downloaded(self):
        message = update()['message']
        message["voice"] = {"file_id": "test", "duration": 121}
        with self.assertRaises(ServiceError):
            await self.app.handle(1, message)
        self.telegram.download_voice.assert_not_called()

    async def test_image_failure_is_terminal_and_not_retried(self):
        identifier = self.store.enqueue_image(1, 42, "貓")
        self.images.generate.side_effect = ServiceError("記憶體不足")
        worker = asyncio.create_task(self.app.image_worker())
        try:
            for _ in range(100):
                if self.store.jobs()[0]["status"] == "failed":
                    break
                await asyncio.sleep(0.001)
            self.assertEqual(self.store.jobs()[0]["status"], "failed")
            self.images.generate.assert_awaited_once()
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_slow_image_does_not_block_chat(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def slow_image(prompt, path):
            started.set()
            await release.wait()

        self.images.generate.side_effect = slow_image
        self.store.enqueue_image(1, 42, "風景")
        worker = asyncio.create_task(self.app.image_worker())
        try:
            await asyncio.wait_for(started.wait(), 1)
            self.llm.generate.return_value = "仍能聊天"
            await asyncio.wait_for(self.app.handle(2, update(text="你好")['message']), 1)
            self.assertEqual(self.store.recent()[-1]["content"], "仍能聊天")
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_new_chat_cancels_background_summary(self):
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def slow_summary():
            started.set()
            try:
                await asyncio.sleep(20)
            finally:
                cancelled.set()

        self.app.summarize = slow_summary
        task = asyncio.create_task(self.app.idle_summary())
        await started.wait()
        self.app.inbox_event.set()
        await asyncio.wait_for(task, 1)
        self.assertTrue(cancelled.is_set())

    async def test_idle_relationship_analysis_applies_bounded_json(self):
        self.store.queue_relationship_review("我有記得你的事", "我有點感動")
        self.llm.generate.return_value = json.dumps({
            "changes": {"trust": 1.5, "affection": 0.4, "tension": -0.2},
            "reason": "使用者展現可靠與關心",
            "profile_ops": [{"action": "add", "category": "互動偏好", "value": "重視被記得的事情"}],
        }, ensure_ascii=False)
        await self.app.analyze_relationship()
        state = self.store.relationship()
        self.assertEqual(state["trust"], 16.08)
        self.assertEqual(state["affection"], 15.4)
        self.assertEqual(self.store.pending_relationship_reviews(), 0)
        self.assertEqual(self.store.profile_facts()[0]["category"], "互動偏好")

    async def test_trait_and_relationship_commands(self):
        await self.app.handle(1, update(text="/settrait teasing 75")['message'])
        await self.app.handle(2, update(text="/setrelation trust 60")['message'])
        self.assertEqual(self.store.traits()["teasing"], 75)
        self.assertEqual(self.store.relationship()["trust"], 60)
        self.assertTrue(any("愛吐槽" in call.args[1] for call in self.telegram.send.await_args_list))

    async def test_adult_module_uses_named_sections_and_persistent_mode(self):
        await self.app.handle(1, update(text="/adult mode intimate")['message'])
        await self.app.handle(10, update(identifier=10, text="/adult initiate off")['message'])
        await self.app.handle(2, update(identifier=2, text="/adult traits flirtatiousness 72")['message'])
        await self.app.handle(3, update(identifier=3, text="/adult relationship sexual_comfort 48")['message'])
        await self.app.handle(4, update(identifier=4, text="/adult needs erotic_connection 1.4")['message'])
        self.assertEqual(self.store.adult_policy()["mode"], "intimate")
        self.assertFalse(self.store.adult_policy()["agent_may_initiate"])
        self.assertEqual(self.store.adult_intimacy()["flirtatiousness"], 72)
        self.assertEqual(self.store.adult_relationship()["sexual_comfort"], 48)
        self.assertEqual(self.store.adult_need_weights()["erotic_connection"], 1.4)
        await self.app.handle(5, update(identifier=5, text="/adult state")['message'])
        report = self.telegram.send.await_args.args[1]
        self.assertIn("heat", report)
        self.assertIn("mutuality", report)

    async def test_adult_module_rejects_editing_dynamic_state(self):
        with self.assertRaisesRegex(ValueError, "系統觀察值"):
            await self.app.handle(1, update(text="/adult state mutuality 100")['message'])

    async def test_adult_mode_downgrade_immediately_caps_or_clears_heat(self):
        self.store.set("intimacy_state", json.dumps({"heat": 90, "momentum": 70}, ensure_ascii=False))
        await self.app.handle(1, update(text="/adult mode flirt")['message'])
        self.assertEqual(self.store.intimacy_state()["heat"], 40)
        await self.app.handle(2, update(identifier=2, text="/adult mode off")['message'])
        self.assertEqual(self.store.intimacy_state()["heat"], 0)

    async def test_language_command_sets_and_reports_override(self):
        await self.app.handle(1, update(text="/language JP")['message'])
        self.assertEqual(self.store.output_language(), "Japanese")
        self.telegram.send.assert_awaited_with(42, "回答語言已切換為 JP（日文）")
        await self.app.handle(2, update(identifier=2, text="/language")['message'])
        self.assertIn("目前回答語言：JP（日文）", self.telegram.send.await_args.args[1])
        with self.assertRaisesRegex(ValueError, "/language CH"):
            await self.app.handle(3, update(identifier=3, text="/language FR")['message'])

    async def test_prompt_command_returns_exact_last_delivered_generation_context(self):
        self.llm.generate.return_value = "這是回答。"
        await self.app.handle(1, update(text="這一輪測試")['message'])
        saved = self.store.get("last_prompt")
        messages = json.loads(saved)
        self.assertEqual(messages, self.llm.generate.await_args.args[0])
        self.assertIn("<language_control>", messages[-1]["content"])
        await self.app.handle(2, update(identifier=2, text="/prompt")['message'])
        self.telegram.send.assert_awaited_with(42, "上一個成功文字回答實際送入 9B 的 messages：\n" + saved)

    async def test_gpt_review_and_ok_save_the_exact_reviewed_exchange(self):
        self.llm.generate.return_value = "這是角色回答。"
        self.app.gpt_reviewer.review.return_value = "結論：正常。"
        await self.app.handle(1, update(text="幫我看看這輪")['message'])
        exchange = self.store.last_delivered_exchange()

        await self.app.handle(2, update(identifier=2, text="/GPT")['message'])
        payload = self.app.gpt_reviewer.review.await_args.args[0]
        self.assertEqual(payload["user_message"], "幫我看看這輪")
        self.assertEqual(payload["assistant_reply"], "這是角色回答。")
        self.assertIsInstance(payload["writer_prompt"], list)
        candidate = self.store.gpt_review_candidate()
        self.assertEqual(candidate["assistant_message_id"], exchange["assistant_message_id"])
        self.assertEqual(candidate["review"], "結論：正常。")

        await self.app.handle(3, update(identifier=3, text="/GPT ok")['message'])
        saved = self.store.db.execute("SELECT payload FROM persona_policy_feedback").fetchone()
        self.assertIsNotNone(saved)
        sample = json.loads(saved["payload"])
        self.assertEqual(sample["assistant"], "這是角色回答。")
        self.assertTrue((self.directory / "persona-policy-feedback.jsonl").is_file())

        await self.app.handle(4, update(identifier=4, text="/GPT ok")['message'])
        count = self.store.db.execute("SELECT COUNT(*) FROM persona_policy_feedback").fetchone()[0]
        self.assertEqual(count, 1)

    async def test_prompt_and_gpt_use_fitted_request_after_history_trimming(self):
        fitted = [{"role": "system", "content": "kept facts"},
                  {"role": "user", "content": "current turn"}]

        async def generate(messages, on_text=None, **kwargs):
            self.assertNotEqual(messages, fitted)
            kwargs["on_request"](fitted)
            return "這是回答。"

        self.llm.generate.side_effect = generate
        await self.app.handle(1, update(text="這一輪測試")['message'])
        self.assertEqual(json.loads(self.store.get("last_prompt")), fitted)
        self.assertEqual(json.loads(self.store.get("last_text_prompt")), fitted)
        await self.app.handle(2, update(identifier=2, text="/prompt")['message'])
        self.assertIn(json.dumps(fitted, ensure_ascii=False, indent=2), self.telegram.send.await_args.args[1])
        self.app.gpt_reviewer.review.return_value = "檢查完成"
        await self.app.handle(3, update(identifier=3, text="/GPT")['message'])
        self.assertEqual(self.app.gpt_reviewer.review.await_args.args[0]["writer_prompt"], fitted)

    @mock_capacity("companion.context_plan.CAPACITY",4096)
    @mock_capacity("companion.pyramid_runner.CAPACITY",4096)
    @mock_capacity("companion.services.CONTEXT_CAPACITY",4096)
    async def test_llm_request_snapshot_matches_http_payload_after_trimming(self):
        messages = [{"role": "system", "content": "facts"},
                    {"role": "user", "content": "old"},
                    {"role": "assistant", "content": "old reply"},
                    {"role": "user", "content": "new"}]
        snapshots, sent = [], []

        def handler(request):
            body = json.loads(request.content)
            if request.url.path == "/apply-template":
                return httpx.Response(200, json={"prompt": str(len(body["messages"]))})
            if request.url.path == "/tokenize":
                count = 4000 if body["content"] == "4" else 20
                return httpx.Response(200, json={"tokens": [1] * count})
            sent.append(body["messages"])
            event = {"choices": [{"delta": {"content": "你好。"}, "finish_reason": "stop"}]}
            return httpx.Response(200, text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await LLM(self.config, client).generate(messages, max_tokens=384, on_request=snapshots.append)
        self.assertEqual(snapshots, sent)
        self.assertEqual(snapshots[0], [messages[0], messages[-1]])
        self.assertEqual(len(messages), 4)

    async def test_gpt_ok_requires_a_completed_review(self):
        with self.assertRaisesRegex(ValueError, "請先使用 /GPT"):
            await self.app.handle(1, update(text="/GPT ok")['message'])

    async def test_life_and_proactive_commands_change_settings(self):
        await self.app.handle(1, update(text="/life off")['message'])
        await self.app.handle(2, update(text="/proactive off")['message'])
        await self.app.handle(3, update(text="/quiet 22:30 07:30")['message'])
        await self.app.handle(4, update(text="/proactive-limit 3")['message'])
        self.assertEqual(self.store.get("life_enabled"), "false")
        self.assertEqual(self.store.get("proactive_enabled"), "false")
        self.assertEqual(self.store.get("quiet_start"), "22:30")
        self.assertEqual(self.store.get("proactive_daily_limit"), "3")

    async def test_profile_commands_allow_manual_correction(self):
        await self.app.handle(21, update(identifier=21, text="/profile set 稱呼 阿明")['message'])
        identifier = self.store.profile_facts()[0]["id"]
        await self.app.handle(22, update(identifier=22, text=f"/profile update {identifier} 稱呼 小明")['message'])
        self.assertEqual(self.store.profile_facts()[0]["value"], "小明")
        await self.app.handle(23, update(identifier=23, text=f"/profile delete {identifier}")['message'])
        self.assertEqual(self.store.profile_facts(), [])

    async def test_appearance_has_fixed_body_and_variable_current_outfit(self):
        await self.app.handle(1, update(text="/appearance body 黑色長髮、棕色眼睛、身高165公分")['message'])
        await self.app.handle(2, update(text="/appearance clothing 偏好黑色休閒服")['message'])
        await self.app.handle(3, update(text="/appearance accessories 銀色耳環")['message'])
        appearance = self.store.appearance()
        self.assertIn("黑色長髮", appearance["body"])
        self.assertEqual(appearance["clothing"], "偏好黑色休閒服")
        self.store.add_message("user", "你長什麼樣子？")
        prompt = self.store.context()[0]["content"]
        self.assertIn("<appearance_facts>", prompt)
        self.assertIn("黑色長髮", prompt)

    async def test_selfie_enqueues_prompt_with_current_appearance(self):
        self.store.update_appearance("body", "黑色長髮、棕色眼睛、成年女性")
        self.store.update_appearance("clothing", "黑色休閒服")
        self.store.update_appearance("accessories", "銀色耳環")
        await self.app.handle(8, update(text="/selfie 在咖啡廳看書")['message'])
        prompt = self.store.db.execute("SELECT prompt FROM jobs").fetchone()[0]
        self.assertIn("黑色長髮", prompt)
        self.assertIn("黑色休閒服", prompt)
        self.assertIn("在咖啡廳看書", prompt)

    async def test_daily_plan_generation_is_persisted(self):
        plan = StoreTests.plan()
        for item in plan:
            item.pop("start_minute")
        self.llm.generate.return_value = json.dumps({"activities": plan}, ensure_ascii=False)
        now = datetime(2026, 10, 13, 10, 0, tzinfo=ZoneInfo("Asia/Taipei"))
        self.assertTrue(await self.app.generate_daily_plan(now))
        snapshot = self.store.life_snapshot(now)
        self.assertEqual(len(snapshot["activities"]), 6)
        self.assertEqual(snapshot["current"]["activity"], "工作")

    async def test_quiet_hours_block_proactive_without_llm(self):
        now = datetime(2026, 10, 13, 23, 30, tzinfo=ZoneInfo("Asia/Taipei"))
        self.store.save_daily_plan(now.date().isoformat(), StoreTests.plan(), 18 * 60)
        await self.app.maybe_proactive(now)
        self.llm.generate.assert_not_called()
        self.telegram.send.assert_not_awaited()

    async def test_proactive_event_is_judged_sent_and_remembered(self):
        zone = ZoneInfo("Asia/Taipei")
        now = datetime(2026, 10, 13, 14, 30, tzinfo=zone)
        self.store.save_daily_plan(now.date().isoformat(), StoreTests.plan(), 14 * 60)
        self.store.set("last_user_at", (now - timedelta(hours=3)).isoformat())
        with self.store.db:
            self.store.db.execute(
                "INSERT INTO proactive_candidates(kind,source_key,due_at,context) VALUES('life_event','test',?,?)",
                (now.isoformat(), json.dumps({"activity": "看書", "outcome": "看到有趣章節"}, ensure_ascii=False)),
            )
        self.llm.generate.return_value = json.dumps({"send": True, "message": "剛看到一段很有意思的內容，突然想跟你分享。", "reason": "自然分享"}, ensure_ascii=False)
        await self.app.maybe_proactive(now)
        self.telegram.send.assert_awaited_once_with(42, "剛看到一段很有意思的內容，突然想跟你分享。")
        self.assertEqual(self.store.proactive_sent_today(now), 1)
        self.assertEqual(self.store.recent()[-1]["role"], "assistant")
        self.assertEqual(self.store.get("proactive_unanswered"), "1")

    async def test_llm_sse_ignores_reasoning_and_records_latency(self):
        seen = []
        events = [{"choices": [{"delta": {"reasoning_content": "hidden"}}]},
                  {"choices": [{"delta": {"content": "你好"}}]},
                  {"choices": [{"delta": {"content": "！"}}]}]
        stream = "".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n"

        def handler(request):
            if request.url.path == "/apply-template":
                return httpx.Response(200, json={"prompt": "formatted"})
            if request.url.path == "/tokenize":
                return httpx.Response(200, json={"tokens": [1, 2, 3]})
            self.assertFalse(json.loads(request.content)["chat_template_kwargs"]["enable_thinking"])
            return httpx.Response(200, text=stream)

        async def callback(text):
            seen.append(text)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            llm = LLM(self.config, client)
            self.assertEqual(await llm.generate([{"role": "user", "content": "hi"}], callback), "你好！")
            self.assertEqual(seen, ["你好", "你好！"])
            self.assertIn("first_text_seconds", llm.last_metrics)

    def test_token_limit_removes_only_a_dangling_tail(self):
        text = "I am reading in the library. I suppose I"
        self.assertEqual(complete_text(text, "length"), "I am reading in the library.")
        self.assertEqual(complete_text(text, "stop"), text)

    def test_token_limit_closes_quotes_and_preserves_completed_parenthesis(self):
        self.assertEqual(complete_text("「你好，Alex。」（她把書放回書架）「既然你", "length"),
                         "「你好，Alex。」（她把書放回書架）")
        self.assertEqual(complete_text("「你好。後面還沒寫", "length"), "「你好。」")
        self.assertEqual(complete_text("好。" + "後" * 80, "length"), "好。")

    async def test_telegram_error_never_exposes_token(self):
        def handler(request):
            raise httpx.ConnectError(str(request.url), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(ServiceError) as caught:
                await Telegram("secret-token", client).send(42, "hi")
            self.assertNotIn("secret-token", str(caught.exception))

    @mock_capacity("companion.context_plan.CAPACITY",4096)
    @mock_capacity("companion.pyramid_runner.CAPACITY",4096)
    @mock_capacity("companion.services.CONTEXT_CAPACITY",4096)
    async def test_context_prunes_old_pairs_with_actual_tokenizer(self):
        formatted_messages = []

        def handler(request):
            data = json.loads(request.content)
            if request.url.path == "/apply-template":
                formatted_messages.append(data["messages"])
                return httpx.Response(200, json={"prompt": str(len(data["messages"]))})
            count = 4000 if data["content"] == "4" else 500
            return httpx.Response(200, json={"tokens": [1] * count})

        messages = [{"role": "system", "content": "memory"}, {"role": "user", "content": "old"},
                    {"role": "assistant", "content": "old answer"}, {"role": "user", "content": "new"}]
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            fitted, count = await LLM(self.config, client).fit_context(messages, 384)
        self.assertEqual(fitted, [messages[0], messages[-1]])
        self.assertEqual(count, 500)
        self.assertEqual(len(messages), 4)

    @mock_capacity("companion.context_plan.CAPACITY",4096)
    @mock_capacity("companion.pyramid_runner.CAPACITY",4096)
    @mock_capacity("companion.services.CONTEXT_CAPACITY",4096)
    async def test_context_reports_oversized_explicit_memory(self):
        def handler(request):
            return httpx.Response(200, json={"prompt": "large"} if request.url.path == "/apply-template" else {"tokens": [1] * 4000})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(ServiceError):
                await LLM(self.config, client).fit_context([{"role": "system", "content": "memory"}, {"role": "user", "content": "new"}], 384)

    async def test_image_success_uploads_once_and_persists_path(self):
        async def generate(prompt, path):
            path.write_bytes(b"fake png")

        self.images.generate.side_effect = generate
        self.store.enqueue_image(1, 42, "貓")
        worker = asyncio.create_task(self.app.image_worker())
        try:
            for _ in range(100):
                if self.store.jobs()[0]["status"] == "done":
                    break
                await asyncio.sleep(0.001)
            self.assertEqual(self.store.jobs()[0]["status"], "done")
            self.telegram.upload.assert_awaited_once()
            self.assertEqual(self.store.get("last_prompt"), "貓")
            self.assertEqual(self.store.get("last_prompt_kind"), "image")
            self.assertEqual(self.store.get("last_prompt_image_id"), "1")
            await self.app.handle(2, update(identifier=2, text="/prompt")['message'])
            self.telegram.send.assert_awaited_with(42, "最近成功傳送的圖片 #1 實際送入 MFLUX 的 Prompt：\n貓")
            self.assertFalse((self.directory / "images/image-1.png").exists())
            self.assertIsNone(self.store.db.execute("SELECT path FROM jobs WHERE id=1").fetchone()[0])
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_voice_synthesis_failure_keeps_text_answer(self):
        message = update()['message']
        message.pop("text")
        message["voice"] = {"file_id": "test", "duration": 2}
        self.voice.transcribe.return_value = "早安"
        self.llm.generate.return_value = "早安！"
        self.voice.synthesize.side_effect = ServiceError("語音合成失敗")
        with self.assertRaises(ServiceError):
            await self.app.handle(1, message)
        self.assertEqual(self.store.recent()[-1]["content"], "早安！")
        self.assertTrue(any(call.args[1] == "早安！" for call in self.telegram.send.await_args_list))

    async def test_reset_command_preserves_role_and_memory(self):
        self.store.remember("喜歡紅茶")
        self.store.update_persona("name", "小茶")
        self.store.add_message("user", "hello")
        await self.app.handle(1, update(text="/reset")['message'])
        self.assertEqual(self.store.recent(), [])
        self.assertEqual(self.store.persona()["name"], "小茶")
        self.assertEqual(len(self.store.memories()), 1)

    async def test_telegram_voice_size_limited_during_stream(self):
        def handler(request):
            if request.method == "POST":
                return httpx.Response(200, json={"ok": True, "result": {"file_path": "voice/file.ogg"}})
            return httpx.Response(200, content=b"x" * 20_000_001)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(ServiceError):
                await Telegram("test", client).download_voice("file", self.directory / "voice.ogg")

    async def test_stream_failure_does_not_resend_uncertain_first_send(self):
        self.telegram.send.side_effect = ServiceError("uncertain")
        reply = StreamingReply(self.telegram, 42)
        with self.assertRaises(ServiceError):
            await reply.update("x" * 30)
        self.telegram.send.assert_awaited_once()

    async def test_process_timeout_kills_child(self):
        with self.assertRaises(ServiceError):
            await run_process([sys.executable, "-c", "import time; time.sleep(10)"], 0.05, "測試")

    async def test_prompt_is_passed_as_one_argv_without_shell(self):
        prompt = "a cat; $(touch /tmp/should-not-exist) `echo hello`"
        async def fake_run(args, timeout, label):
            self.assertEqual(args[args.index("--prompt") + 1], prompt)
            Path(args[args.index("--output") + 1]).write_bytes(b"image")
        with patch("companion.services.run_process", fake_run):
            await Images(self.config).generate(prompt, self.directory / "output.png")


class ConfigTests(unittest.TestCase):
    def test_rejects_remote_llm(self):
        with patch.dict(os.environ, {"LLM_BASE_URL": "https://example.com/v1"}):
            with self.assertRaises(ValueError):
                Config.load(require_credentials=False)

    def test_hides_incomplete_thinking(self):
        self.assertEqual(visible_text("<think>secret"), "")
        self.assertEqual(visible_text("<think>secret</think>你好"), "你好")

    def test_relationship_parser_accepts_fenced_json(self):
        changes, reason, profile_ops = parse_relationship_analysis(
            '```json\n{"changes":{"trust":1},"reason":"可靠"}\n```'
        )
        self.assertEqual(changes, {"trust": 1})
        self.assertEqual(reason, "可靠")
        self.assertEqual(profile_ops, [])


if __name__ == "__main__":
    unittest.main()
