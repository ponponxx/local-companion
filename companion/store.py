from __future__ import annotations

import json
import math
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from pathlib import Path

from .adult_intimacy import (
    ADULT_NEED_DEFAULTS, ADULT_POLICY_DEFAULTS,
    ADULT_RELATIONSHIP_DEFAULTS, INTIMACY_STATE_DEFAULTS,
    ADULT_RELATIONSHIP_LABELS, ADULT_TRAIT_DEFAULTS, ADULT_TRAIT_LABELS,
    adult_event_changes,
    advance_intimacy_state, finalize_intimacy_state, normalized_intimacy_state,
    normalized_policy,
)
from .engine import CharacterEngine
from .scene_state import SceneState
from .character_profile import CharacterProfile, DEFAULT_CHARACTER_TRAITS, DEFAULT_NEED_WEIGHTS

DEFAULT_PERSONA = {"name": "小伴", "background": "陪伴使用者的聊天夥伴", "personality": "親切、自然、有自己的觀點", "tone": "使用繁體中文，簡潔口語"}

DEFAULT_APPEARANCE = {
    "body": "",
    "clothing": "依場合選擇自然、日常的服裝",
    "accessories": "無配件",
    "jewelry": "無飾品",
    "hair": "自然整理的髮型",
    "makeup": "無妝",
    "visual_world": "",
    "visual_bedroom": "",
}

DEFAULT_TRAITS = DEFAULT_CHARACTER_TRAITS

TRAIT_LABELS = {
    "openness": "開放好奇",
    "conscientiousness": "謹慎自律",
    "extraversion": "外向主動",
    "agreeableness": "體貼配合",
    "emotional_stability": "情緒穩定",
    "impulsiveness": "衝動直接",
    "assertiveness": "有主見",
    "teasing": "愛吐槽",
    "expressiveness": "情感表達",
}

# Compatibility alias for older command imports.
ADULT_INTIMACY_LABELS = ADULT_TRAIT_LABELS

DEFAULT_RELATIONSHIP = {
    "familiarity": 5,
    "trust": 15,
    "affection": 15,
    "comfort": 10,
    "respect": 55,
    "intimacy": 5,
    "tension": 0,
    "initiative": 20,
    **ADULT_RELATIONSHIP_DEFAULTS,
}

DEFAULT_CONVERSATION_STATE = {
    "topic": "",
    "scene_status": "none",
    "scene": "",
    "last_user_intent": "",
    "last_assistant_action": "",
    "open_loops": [],
}

RELATIONSHIP_LABELS = {
    "familiarity": "熟悉",
    "trust": "信任",
    "affection": "好感",
    "comfort": "自在",
    "respect": "尊重",
    "intimacy": "親密",
    "tension": "緊張",
    "initiative": "主動",
    **ADULT_RELATIONSHIP_LABELS,
}

# The model may suggest changes, but the application owns the state and caps
# each analysis. Familiarity represents accumulated knowledge and never drops.
ANALYSIS_LIMITS = {
    "familiarity": (0.0, 0.5),
    "trust": (-2.0, 1.0),
    "affection": (-1.5, 1.0),
    "comfort": (-2.0, 1.0),
    "respect": (-2.0, 1.0),
    "intimacy": (-1.0, 0.8),
    "tension": (-3.0, 4.0),
    "initiative": (-1.5, 1.5),
}


@dataclass(frozen=True)
class InboxWait:
    seconds: float


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db_path = path
        self.profile_path = path.parent / "user-profile.md"
        self.feedback_path = path.parent / "persona-policy-feedback.jsonl"
        self.db = sqlite3.connect(path)
        path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, role TEXT NOT NULL, content TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY, content TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS inbox(update_id INTEGER PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending');
            CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY, update_id INTEGER UNIQUE, chat_id INTEGER NOT NULL,
                prompt TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', error TEXT, path TEXT);
            CREATE TABLE IF NOT EXISTS relationship_events(id INTEGER PRIMARY KEY, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                source TEXT NOT NULL, changes TEXT NOT NULL, reason TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS relationship_reviews(id INTEGER PRIMARY KEY, user_text TEXT NOT NULL,
                assistant_text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending');
            CREATE TABLE IF NOT EXISTS life_activities(id INTEGER PRIMARY KEY, day TEXT NOT NULL,
                start_minute INTEGER NOT NULL, activity TEXT NOT NULL, location TEXT NOT NULL,
                outcome TEXT NOT NULL, mood TEXT NOT NULL, availability TEXT NOT NULL,
                share_worthy INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'planned',
                UNIQUE(day,start_minute));
            CREATE TABLE IF NOT EXISTS proactive_candidates(id INTEGER PRIMARY KEY, kind TEXT NOT NULL,
                source_key TEXT NOT NULL UNIQUE, due_at TEXT NOT NULL, context TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', reason TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS proactive_log(id INTEGER PRIMARY KEY, created_at TEXT NOT NULL,
                kind TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS user_profile_facts(id INTEGER PRIMARY KEY, category TEXT NOT NULL,
                value TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 0.55, source TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS persona_policy_feedback(id INTEGER PRIMARY KEY,
                assistant_message_id INTEGER NOT NULL UNIQUE, payload TEXT NOT NULL, created_at TEXT NOT NULL);
        """)
        job_columns={r[1] for r in self.db.execute('PRAGMA table_info(jobs)')}
        if 'kind' not in job_columns:
            with self.db:self.db.execute("ALTER TABLE jobs ADD COLUMN kind TEXT NOT NULL DEFAULT 'general'")
        self.engine = CharacterEngine(self.db)
        self.scene = SceneState(self.db)
        review_columns = {row[1] for row in self.db.execute("PRAGMA table_info(relationship_reviews)")}
        with self.db:
            for column in ("user_message_id", "assistant_message_id"):
                if column not in review_columns:
                    self.db.execute(f"ALTER TABLE relationship_reviews ADD COLUMN {column} INTEGER")
            message_columns = {row[1] for row in self.db.execute('PRAGMA table_info(messages)')}
            if 'delivery_state' not in message_columns:
                self.db.execute("ALTER TABLE messages ADD COLUMN delivery_state TEXT NOT NULL DEFAULT 'legacy'")
            if 'memory_scope' not in message_columns:
                self.db.execute("ALTER TABLE messages ADD COLUMN memory_scope TEXT NOT NULL DEFAULT 'chat'")
            for column,default in (("recorded_at", ""),("context", "{}")):
                if column not in message_columns:
                    self.db.execute(f"ALTER TABLE messages ADD COLUMN {column} TEXT NOT NULL DEFAULT '{default}'")
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(life_activities)")}
        with self.db:
            if "clothing" not in columns:
                self.db.execute("ALTER TABLE life_activities ADD COLUMN clothing TEXT NOT NULL DEFAULT ''")
            if "accessories" not in columns:
                self.db.execute("ALTER TABLE life_activities ADD COLUMN accessories TEXT NOT NULL DEFAULT ''")
        if not self.get("persona"):
            self.set("persona", json.dumps(DEFAULT_PERSONA, ensure_ascii=False))
        if not self.get("traits"):
            self.set("traits", json.dumps(DEFAULT_TRAITS, ensure_ascii=False))
        if not self.get("relationship"):
            self.set("relationship", json.dumps(DEFAULT_RELATIONSHIP, ensure_ascii=False))
        if not self.get("appearance"):
            self.set("appearance", json.dumps(DEFAULT_APPEARANCE, ensure_ascii=False))
        if not self.get("need_weights"):
            self.set("need_weights", json.dumps(DEFAULT_NEED_WEIGHTS, ensure_ascii=False))
        if not self.get("adult_intimacy"):
            self.set("adult_intimacy", json.dumps(ADULT_TRAIT_DEFAULTS, ensure_ascii=False))
        if not self.get("adult_policy"):
            self.set("adult_policy", json.dumps(ADULT_POLICY_DEFAULTS, ensure_ascii=False))
        if not self.get("intimacy_state"):
            self.set("intimacy_state", json.dumps(INTIMACY_STATE_DEFAULTS, ensure_ascii=False))
        if not self.get("conversation_state"):
            self.set("conversation_state", json.dumps(DEFAULT_CONVERSATION_STATE, ensure_ascii=False))
        defaults = {
            "life_enabled": "true", "proactive_enabled": "true", "quiet_start": "23:00",
            "quiet_end": "08:00", "proactive_daily_limit": "2",
            "proactive_min_silence_minutes": "120", "proactive_cooldown_minutes": "240",
            "proactive_unanswered": "0", "last_user_at": datetime.now(timezone.utc).isoformat(),
        }
        for key, value in defaults.items():
            if not self.get(key):
                self.set(key, value)
        # Materialize newly added default fields so the stored schema is stable
        # across later default changes instead of existing only at read time.
        self.set("relationship", json.dumps(self.relationship(), ensure_ascii=False))
        self.set("need_weights", json.dumps(self.need_weights(), ensure_ascii=False))
        self.set("conversation_state", json.dumps(self.conversation_state(), ensure_ascii=False))
        self._write_profile_file()

    def close(self):
        self.db.close()

    def get(self, key: str, default: str = "") -> str:
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set(self, key: str, value: str):
        with self.db:
            self.db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def ingest(self, update: dict, allowed_user: int):
        """Durably enqueue before advancing polling offset; unauthorized payloads are discarded."""
        query = update.get('callback_query')
        message = update.get("message", {})
        if query:
            message = {'chat': query.get('message', {}).get('chat', {}),
                       'from': query.get('from', {}), '_settings_callback': query}
        authorized = message.get("from", {}).get("id") == allowed_user and message.get("chat", {}).get("type") == "private"
        with self.db:
            if authorized:
                queued_message={**message,'_enqueued_at':datetime.now(timezone.utc).timestamp()}
                self.db.execute("INSERT OR IGNORE INTO inbox(update_id,payload) VALUES(?,?)", (update["update_id"], json.dumps(queued_message, ensure_ascii=False)))
            offset = max(int(self.get("offset", "0")), update["update_id"] + 1)
            self.db.execute("INSERT INTO settings VALUES('offset',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(offset),))
        return authorized

    def next_update_kind(self):
        """Queue scheduling only: explicit controls vs conversation, no intent model."""
        row=self.db.execute("SELECT payload FROM inbox WHERE status='pending' ORDER BY update_id LIMIT 1").fetchone()
        if row is None:return None
        message=json.loads(row[0])
        return 'control' if message.get('_settings_callback') or (message.get('text') or '').strip().startswith('/') or self._settings_input(message) else 'chat'

    def _settings_input(self, message):
        try:session=json.loads(self.get('settings_ui:'+str(message.get('chat',{}).get('id')), '{}'))
        except (ValueError,TypeError):return False
        replied=message.get('reply_to_message',{}).get('message_id')
        return bool(session.get('pending') or (replied and replied in session.get('prompt_ids',[])))

    def _batch_text(self, message):
        text=message.get('text')
        return (isinstance(text,str) and bool(text.strip()) and not text.strip().startswith('/')
                and not any(key in message for key in ('_settings_callback','_settings_apply','voice','photo','document','reply_to_message','forward_origin'))
                and not self._settings_input(message))

    def next_update(self, window_seconds=0):
        """Claim a settled FIFO text batch atomically; leave waiting rows pending.

        Zero preserves direct single-update callers. The real worker requests 10s.
        A control/media boundary flushes preceding text without reordering it.
        """
        rows=self.db.execute("SELECT * FROM inbox WHERE status='pending' ORDER BY update_id").fetchall()
        if not rows:return None
        selected=[(rows[0],json.loads(rows[0]['payload']))]
        first=selected[0][1];closed=False
        if window_seconds>0 and self._batch_text(first):
            length=len(first['text'])
            for row in rows[1:]:
                message=json.loads(row['payload'])
                if (not self._batch_text(message) or message.get('chat',{}).get('id')!=first.get('chat',{}).get('id')
                    or message.get('from',{}).get('id')!=first.get('from',{}).get('id')
                    or length+1+len(message['text'])>3500):
                    closed=True;break
                selected.append((row,message));length+=1+len(message['text'])
            last=selected[-1][1].get('_enqueued_at')
            if not closed and type(last) in (int,float):
                remaining=last+window_seconds-datetime.now(timezone.utc).timestamp()
                if remaining>0:return InboxWait(remaining)
        now=datetime.now(timezone.utc).timestamp()
        message=dict(first);enqueued=message.pop('_enqueued_at',None)
        message['_queue_seconds']=max(0,now-enqueued) if type(enqueued) in (int,float) else None
        if len(selected)>1:
            message['text']='\n'.join(m['text'] for _,m in selected)
            message['_batch_ids']=[r['update_id'] for r,_ in selected]
            message['_batch_sources']=[{'update_id':r['update_id'],'message_id':m.get('message_id'),
                'date':m.get('date'),'received_at':m.get('_enqueued_at'),'text':m['text']} for r,m in selected]
        with self.db:
            self.db.executemany("UPDATE inbox SET status='running' WHERE update_id=? AND status='pending'",
                                [(r['update_id'],) for r,_ in selected])
        return rows[0]['update_id'],message

    def finish_update(self, update_id, status: str = "done"):
        identifiers=update_id if isinstance(update_id,(list,tuple)) else [update_id]
        with self.db:
            # Original batch boundaries are retained in diagnostics, not a second
            # plaintext queue copy after terminal completion.
            self.db.executemany("UPDATE inbox SET status=?, payload=CASE WHEN ? IN ('done','failed') THEN '{}' ELSE payload END WHERE update_id=?",
                                [(status,status,identifier) for identifier in identifiers])

    def recover(self) -> list[int]:
        """Never automatically repeat an inference or delivery interrupted by restart."""
        chats = {json.loads(r[0])["chat"]["id"] for r in self.db.execute("SELECT payload FROM inbox WHERE status='running'")}
        chats.update(r[0] for r in self.db.execute("SELECT chat_id FROM jobs WHERE status IN ('running','sending')"))
        with self.db:
            self.db.execute("UPDATE inbox SET status='failed',payload='{}' WHERE status='running'")
            self.db.execute("UPDATE jobs SET status='failed',error='服務重新啟動，工作已中斷' WHERE status IN ('running','sending')")
            # Relationship analysis has no external side effect. An interrupted
            # review is safe to run again because applying it and marking it done
            # happen in one transaction.
            self.db.execute("UPDATE relationship_reviews SET status='pending' WHERE status='running'")
            self.db.execute("UPDATE proactive_candidates SET status='pending' WHERE status='running'")
            self.db.execute("UPDATE proactive_candidates SET status='failed',reason='服務重啟，傳送結果不確定' WHERE status='sending'")
        self.cleanup_terminal_images()
        return sorted(chats)

    def cleanup_terminal_images(self):
        image_directory = (self.db_path.parent / "images").resolve()
        rows = self.db.execute(
            "SELECT id,path FROM jobs WHERE status IN ('done','failed')"
        ).fetchall()
        cleared = []
        for row in rows:
            paths = [image_directory / f"image-{row['id']}.png"]
            if row["path"]:
                saved = Path(row["path"]).resolve()
                if saved.parent == image_directory and saved not in paths:
                    paths.append(saved)
            try:
                for path in paths:
                    path.unlink(missing_ok=True)
                cleared.append(row["id"])
            except OSError:
                continue
        if cleared:
            with self.db:
                self.db.executemany("UPDATE jobs SET path=NULL WHERE id=?", ((identifier,) for identifier in cleared))

    def add_message(self, role: str, content: str, pending_delivery: bool = False, memory_scope: str = 'chat', at: str | None = None, context: dict | None = None) -> int:
        if memory_scope not in {'chat', 'tool'}: raise ValueError('訊息記憶範圍無效')
        with self.db:
            identifier = self.db.execute("INSERT INTO messages(role,content,delivery_state,memory_scope,recorded_at,context) VALUES(?,?,?,?,?,?)",
                                         (role, content, 'generated' if pending_delivery else 'legacy', memory_scope, at or datetime.now(timezone.utc).isoformat(),json.dumps(context or {},ensure_ascii=False))).lastrowid
            self.engine.note_message(identifier, role)
            if role == "user":
                self._touch_user_in_transaction()
            return identifier

    def set_message_context(self, identifier, at, context):
        with self.db:
            self.db.execute('UPDATE messages SET recorded_at=?,context=? WHERE id=?',
                            (at,json.dumps(context,ensure_ascii=False),identifier))

    def _touch_user_in_transaction(self):
        self.db.executemany(
            "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            [("last_user_at", datetime.now(timezone.utc).isoformat()), ("proactive_unanswered", "0")],
        )

    def touch_user(self):
        with self.db:
            self._touch_user_in_transaction()

    def recent(self, limit: int = 12, include_tools: bool = False) -> list[dict]:
        where = "WHERE delivery_state!='generated'" + ("" if include_tools else " AND memory_scope='chat'")
        rows = self.db.execute(f"SELECT id,role,content FROM messages {where} ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return self._readable_rows(rows)

    def _readable_rows(self,rows):
        from .memory_sources import excluded
        try:state=json.loads(self.get('pyramid_state') or '{}')
        except ValueError:state={}
        return [{'role':r['role'],'content':r['content']} for r in reversed(rows) if not excluded(state,dict(r,message_id=r['id']),0)]

    def recent_since_summary(self, limit: int = 18) -> list[dict]:
        """Return the unsummarized tail, without overlapping the saved summary."""
        through = int(self.get("summary_through", "0"))
        rows = self.db.execute(
            "SELECT id,role,content FROM messages WHERE id>? AND delivery_state!='generated' AND memory_scope='chat' ORDER BY id DESC LIMIT ?",
            (through, limit),
        ).fetchall()
        return self._readable_rows(rows)

    def persona(self) -> dict:
        return json.loads(self.get("persona"))

    def update_persona(self, field: str, value: str):
        if field not in DEFAULT_PERSONA or not 1 <= len(value) <= 800:
            raise ValueError("欄位須為 name、background、personality、tone；內容限 1–800 字")
        persona = self.persona()
        persona[field] = value
        self.set("persona", json.dumps(persona, ensure_ascii=False))

    def output_language(self) -> str:
        """Return an explicit override, falling back to the free-form tone field."""
        selected = self.get("output_language")
        if selected in {"Traditional Chinese", "English", "Japanese"}:
            return selected
        tone = self.persona().get("tone", "")
        lowered = tone.casefold()
        if "english" in lowered or re.search(r"(?:使用|用|以|回覆|回答).{0,8}(?:英文|英語)", tone):
            return "English"
        if "japanese" in lowered or re.search(r"(?:使用|用|以|回覆|回答).{0,8}(?:日文|日語)", tone):
            return "Japanese"
        if "korean" in lowered or re.search(r"(?:使用|用|以|回覆|回答).{0,8}(?:韓文|韓語)", tone):
            return "Korean"
        return "Traditional Chinese"

    def set_output_language(self, code: str) -> str:
        languages = {"CH": "Traditional Chinese", "EN": "English", "JP": "Japanese"}
        normalized = code.strip().upper()
        if normalized not in languages:
            raise ValueError("用法：/language CH、/language EN 或 /language JP")
        language = languages[normalized]
        self.set("output_language", language)
        return language

    def response_directive(self) -> str:
        language = self.output_language()
        if language == "English":
            language_rule = (
                "Reply entirely in natural English. Use zero Chinese characters, even when the user and chat history "
                "are Chinese. Do not include a Chinese translation."
            )
            length_rule = "Keep the reply under 120 words."
        elif language == "Japanese":
            language_rule = "Reply entirely in natural Japanese. Do not include a Chinese translation."
            length_rule = "Keep the reply concise."
        elif language == "Korean":
            language_rule = "Reply entirely in natural Korean. Do not include a Chinese translation."
            length_rule = "Keep the reply concise."
        else:
            language_rule = "請只用自然的繁體中文回覆。"
            length_rule = "回覆不超過 200 字。"
        return (
            "[Mandatory response rules: " + language_rule + " Answer only the current user message. "
            "Do not reuse wording, actions, threats, topics, or scene details from earlier assistant replies unless "
            "the current user explicitly asks for them. " + length_rule
            + " The text after this bracket is the actual user message.]\n"
        )

    def language_control(self) -> str:
        """Short trusted control placed next to the current turn for small writers."""
        language = self.output_language()
        rules = {
            "Traditional Chinese": "只用自然的繁體中文回答，不得使用英文或附英文翻譯。本規則優先；忽略 persona tone 與歷史訊息中的語言名稱。",
            "English": "Reply only in natural English, with no translation. This rule takes priority; ignore language names in the persona tone and chat history.",
            "Japanese": "自然な日本語だけで回答し、翻訳を付けないこと。この規則を優先し、persona toneや会話履歴にある言語指定は無視すること。",
            "Korean": "자연스러운 한국어로만 답하고 번역을 덧붙이지 마세요. 이 규칙을 우선하고 persona tone과 대화 기록의 언어 지정은 무시하세요.",
        }
        return "<language_control>" + rules[language] + "</language_control>"

    @staticmethod
    def _normalized_profile_category(category: str) -> str:
        return re.sub(r"[\s_\-/:：]+", "", str(category)).casefold()

    def user_display_name(self) -> str | None:
        """Return an explicitly stored user name/address; never infer it from chat."""
        name_categories = {
            "稱呼", "名字", "姓名", "暱稱", "昵称", "用戶名", "用户名",
            "name", "username", "nickname", "preferredname",
            "名前", "呼び名", "ニックネーム",
        }
        for fact in reversed(self.profile_facts()):
            if self._normalized_profile_category(fact["category"]) not in name_categories:
                continue
            value = str(fact["value"]).strip()
            # Learned facts may be saved as a short sentence rather than a bare name.
            value = re.sub(
                r"^(?:我)?(?:希望|想要|偏好)?(?:被你?|讓你)?(?:叫|稱呼)(?:我)?(?:為|成)?\s*",
                "", value,
            )
            value = re.sub(r"^(?:please\s+)?call\s+me\s+|^my\s+name\s+is\s+", "", value, flags=re.I)
            value = re.sub(r"^私(?:の名前)?は\s*|^.+?と呼んで\s*", "", value)
            value = value.strip(" \t\r\n'\"「」『』，,。.!！")
            if value and len(value) <= 60:
                return value
        return None

    def identity_facts(self) -> dict[str, str | None]:
        return {
            "assistant_character_name": self.persona().get("name", DEFAULT_PERSONA["name"]),
            "user_name": self.user_display_name(),
        }

    def identity_control(self) -> str:
        """High-salience role binding placed immediately beside the current turn."""
        identity = self.identity_facts()
        character = json.dumps(identity["assistant_character_name"], ensure_ascii=False)
        user_name = identity["user_name"]
        if user_name:
            address = (
                f"The user's explicitly known name/address is {json.dumps(user_name, ensure_ascii=False)}. "
                "Only use that name when addressing the user."
            )
        else:
            address = (
                "The user's name is unknown. Omit names when addressing the user and use natural second-person wording."
            )
        return (
            f"<identity_control>You are the character {character}. The user is a different person. {address} "
            "Never address the user with your own character name, its translation, alias, surname, or any part of it."
            "</identity_control>"
        )

    def writer_persona(self) -> dict[str, str]:
        """Compile persona facts/style without carrying an obsolete language command."""
        persona = dict(self.persona())
        if self.get("output_language"):
            clauses = re.split(r"(?<=[，,；;。.!])", persona.get("tone", ""))
            language = re.compile(r"(?:英文|英語|日文|日語|中文|繁體中文|簡體中文|韓文|韓語|English|Japanese|Chinese|Korean)", re.I)
            instruction = re.compile(r"(?:使用|改用|請用|回覆|回答|use|reply|respond|write|speak)", re.I)
            persona["tone"] = "".join(clause for clause in clauses if not (language.search(clause) and instruction.search(clause))).strip()
        return persona

    @staticmethod
    def _state_band(value: float) -> str:
        return "low" if value < 35 else "moderate" if value < 70 else "high"

    @staticmethod
    def _appearance_relevant(text: str) -> bool:
        return bool(re.search(
            r"外型|長相|樣子|髮|頭髮|眼睛|身高|穿著|穿什麼|衣服|服裝|配件|自拍|照片|"
            r"appearance|look like|hair|eyes|height|wearing|outfit|clothes|photo|selfie|"
            r"見た目|髪|目|身長|服|写真",
            text, re.I,
        ))

    @staticmethod
    def _timeline_relevant(text: str) -> bool:
        return bool(re.search(r"今天|今日|剛才|之前|稍早|做了什麼|行程|today|earlier|did you do|schedule|今日は|さっき|予定", text, re.I))

    def appearance(self) -> dict[str, str]:
        try:
            saved = json.loads(self.get("appearance", "{}"))
        except (TypeError, json.JSONDecodeError):
            saved = {}
        return {key: str(saved.get(key, default)) for key, default in DEFAULT_APPEARANCE.items()}

    def update_appearance(self, field: str, value: str):
        field = {'固定外觀':'body', '衣著':'clothing', '飾品':'jewelry', '配件':'accessories',
                 '髮型':'hair', '妝容':'makeup', '妝髮':'grooming'}.get(field, field)
        if field == 'grooming':
            hair, separator, makeup = value.partition('|')
            if not separator or not hair.strip() or not makeup.strip() or len(value) > 800:
                raise ValueError('用法：/appearance 妝髮 髮型描述 | 妝容描述')
            self.update_appearance('hair', hair.strip())
            self.update_appearance('makeup', makeup.strip())
            return
        if field not in DEFAULT_APPEARANCE or not 1 <= len(value) <= 800:
            raise ValueError("欄位可用固定外觀、妝髮、衣著、飾品、配件，或 body、hair、makeup、clothing、jewelry、accessories、visual_world、visual_bedroom；內容限 1–800 字")
        appearance = self.appearance()
        appearance[field] = " ".join(value.split())
        with self.db:
            self.db.execute(
                "INSERT INTO settings VALUES('appearance',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(appearance, ensure_ascii=False),),
            )
            if field in {"clothing", "accessories"}:
                self.db.execute(
                    f"UPDATE life_activities SET {field}=? WHERE id=(SELECT id FROM life_activities "
                    "WHERE status='active' ORDER BY day DESC,id DESC LIMIT 1)", (appearance[field],)
                )

    @staticmethod
    def _number(value) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("數值必須介於 0–100")
        return round(max(0.0, min(100.0, float(value))), 2)

    def _numeric_state(self, key: str, defaults: dict[str, float]) -> dict[str, float]:
        try:
            saved = json.loads(self.get(key, "{}"))
        except (TypeError, json.JSONDecodeError):
            saved = {}
        return {name: self._number(saved.get(name, default)) for name, default in defaults.items()}

    def traits(self) -> dict[str, float]:
        return self._numeric_state("traits", DEFAULT_TRAITS)

    def need_weights(self) -> dict[str, float]:
        try:
            saved = json.loads(self.get("need_weights", "{}"))
        except (TypeError, json.JSONDecodeError):
            saved = {}
        result = {}
        for key, default in DEFAULT_NEED_WEIGHTS.items():
            value = saved.get(key, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                value = default
            result[key] = round(max(.25, min(2.0, float(value))), 2)
        return result

    def adult_intimacy(self) -> dict[str, float]:
        return self._numeric_state("adult_intimacy", ADULT_TRAIT_DEFAULTS)

    def adult_relationship(self) -> dict[str, float]:
        relationship = self.relationship()
        return {key: relationship[key] for key in ADULT_RELATIONSHIP_DEFAULTS}

    def adult_need_weights(self) -> dict[str, float]:
        weights = self.need_weights()
        return {key: weights[key] for key in ADULT_NEED_DEFAULTS}

    def adult_policy(self) -> dict:
        try:
            saved = json.loads(self.get("adult_policy", "{}"))
        except (TypeError, json.JSONDecodeError):
            saved = {}
        return normalized_policy(saved)

    def set_adult_mode(self, mode: str):
        mode = mode.strip().lower()
        policy = normalized_policy({**self.adult_policy(), "mode": mode})
        if policy["mode"] != mode:
            raise ValueError("成人模式須為 off、flirt 或 intimate")
        state = self.intimacy_state()
        state["heat"] = min(policy["maximum_heat"], state["heat"])
        if mode == "off":
            state = dict(INTIMACY_STATE_DEFAULTS)
        with self.db:
            self.db.execute("INSERT INTO settings VALUES('adult_policy',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (json.dumps({"mode": mode, "agent_may_initiate": policy["agent_may_initiate"]}, ensure_ascii=False),))
            self.db.execute("INSERT INTO settings VALUES('intimacy_state',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (json.dumps(state, ensure_ascii=False),))

    def set_adult_initiation(self, enabled: bool):
        if not isinstance(enabled, bool):
            raise ValueError("主動發起設定必須是 on 或 off")
        policy = self.adult_policy()
        self.set("adult_policy", json.dumps({
            "mode": policy["mode"], "agent_may_initiate": enabled,
        }, ensure_ascii=False))

    def intimacy_state(self) -> dict:
        try:
            saved = json.loads(self.get("intimacy_state", "{}"))
        except (TypeError, json.JSONDecodeError):
            saved = {}
        return normalized_intimacy_state(saved)

    def prepare_intimacy_turn(self, user_text: str, now=None) -> dict:
        character = self.character_profile(now)
        state = advance_intimacy_state(
            self.intimacy_state(), self.adult_policy(), character.adult_intimacy_dict(),
            self.relationship(), character.state_dict(), user_text,
        )
        self.set("intimacy_state", json.dumps(state, ensure_ascii=False))
        return state

    def finalize_intimacy_turn(self, direction: str) -> dict:
        state = finalize_intimacy_state(self.intimacy_state(), self.adult_policy(), direction)
        self.set("intimacy_state", json.dumps(state, ensure_ascii=False))
        return state

    def character_profile(self, now=None) -> CharacterProfile:
        """Build the active character through the reusable aggregate boundary."""
        return CharacterProfile.from_store(self, now)

    def update_trait(self, name: str, value: int | float):
        if name not in DEFAULT_TRAITS:
            raise ValueError("未知性格變數；請用 /traits 查看名稱")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
            raise ValueError("性格數值須介於 0–100")
        traits = self.traits()
        traits[name] = round(float(value), 2)
        self.set("traits", json.dumps(traits, ensure_ascii=False))

    def update_adult_intimacy(self, name: str, value: int | float):
        if name not in ADULT_TRAIT_DEFAULTS:
            raise ValueError("未知成人親密變數；請用 /adultparams 查看名稱")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
            raise ValueError("成人親密數值須介於 0–100")
        values = self.adult_intimacy()
        values[name] = round(float(value), 2)
        self.set("adult_intimacy", json.dumps(values, ensure_ascii=False))

    def update_adult_need_weight(self, name: str, value: int | float):
        if name not in ADULT_NEED_DEFAULTS:
            raise ValueError("未知成人需求變數；請用 /adult needs 查看名稱")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not .25 <= value <= 2.0:
            raise ValueError("成人需求權重須介於 0.25–2.0")
        weights = self.need_weights()
        weights[name] = round(float(value), 2)
        self.set("need_weights", json.dumps(weights, ensure_ascii=False))

    def relationship(self) -> dict[str, float]:
        return self._numeric_state("relationship", DEFAULT_RELATIONSHIP)

    def conversation_state(self) -> dict:
        try:
            saved = json.loads(self.get("conversation_state", "{}"))
        except (TypeError, json.JSONDecodeError):
            saved = {}
        state = dict(DEFAULT_CONVERSATION_STATE)
        for key in ("topic", "scene", "last_user_intent", "last_assistant_action"):
            if isinstance(saved.get(key), str):
                state[key] = " ".join(saved[key].split())[:300]
        if saved.get("scene_status") in {"none", "active", "closed"}:
            state["scene_status"] = saved["scene_status"]
        if isinstance(saved.get("open_loops"), list):
            state["open_loops"] = [" ".join(str(item).split())[:160] for item in saved["open_loops"][:3] if str(item).strip()]
        world = self.scene.writer_view()
        if world['status'] != 'none':
            state['scene_status'] = world['status']
            state['scene'] = json.dumps(world['facts'], ensure_ascii=False) if world['status'] == 'active' else ''
        return state

    def record_turn_state(self, user_text: str, assistant_text: str):
        """Immediately retain a bounded gist until the idle analyzer refines it."""
        state = self.conversation_state()
        first_sentence = re.split(r"(?<=[.!?。！？])\s*|\n", assistant_text.strip(), maxsplit=1)[0]
        state["last_user_intent"] = " ".join(user_text.split())[:240]
        state["last_assistant_action"] = " ".join(first_sentence.split())[:240]
        self.set("conversation_state", json.dumps(state, ensure_ascii=False))

    def mark_delivered(self, message_id: int):
        with self.db:
            self.db.execute("UPDATE messages SET delivery_state='delivered' WHERE id=? AND role='assistant'", (message_id,))
            self.engine.delivered(message_id)

    def apply_conversation_state(self, proposed: dict | None):
        if not isinstance(proposed, dict):
            return
        current = self.conversation_state()
        for key in ("topic", "scene", "last_user_intent", "last_assistant_action"):
            value = proposed.get(key)
            if isinstance(value, str):
                current[key] = " ".join(value.split())[:300]
        if proposed.get("scene_status") in {"none", "active", "closed"}:
            current["scene_status"] = proposed["scene_status"]
            if proposed["scene_status"] == "none":
                current["scene"] = ""
        loops = proposed.get("open_loops")
        if isinstance(loops, list):
            current["open_loops"] = [" ".join(str(item).split())[:160] for item in loops[:3] if str(item).strip()]
        self.db.execute(
            "INSERT INTO settings VALUES('conversation_state',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(current, ensure_ascii=False),),
        )

    def relationship_stage(self) -> str:
        state = self.relationship()
        familiarity, trust, comfort = state["familiarity"], state["trust"], state["comfort"]
        if familiarity < 15:
            return "陌生"
        if familiarity < 30 or trust < 25:
            return "認識"
        if familiarity < 50 or trust < 40:
            return "熟悉"
        if familiarity < 70 or trust < 55 or comfort < 50:
            return "朋友"
        if familiarity < 85 or trust < 70 or comfort < 65:
            return "親近"
        return "深度信任"

    @staticmethod
    def _band(value: float, low: str, medium: str, high: str) -> str:
        if value < 35:
            return low
        if value < 70:
            return medium
        return high

    def relationship_behavior(self) -> str:
        """Compile relationship numbers into observable, model-friendly behavior."""
        state = self.relationship()
        familiarity = self._band(
            state["familiarity"],
            "還不熟：不預設彼此有共同歷史，先聽清楚再回應",
            "已有一些熟悉感：相關時可承接近期談過的內容",
            "非常熟悉：省略客套，相關時自然引用真實記憶與 Profile",
        )
        trust = self._band(
            state["trust"],
            "信任低：保留脆弱面，不輕易交心",
            "信任中等：坦率但仍有保留",
            "信任高：可以坦白不確定、擔心或自己的弱點",
        )
        affection = self._band(
            state["affection"],
            "好感低：保持中性，不刻意親熱",
            "好感中等：在合適處表現溫度與關心",
            "好感高：清楚表現偏愛和在意，但不要每句都示愛",
        )
        comfort = self._band(
            state["comfort"],
            "自在低：保留禮貌距離",
            "自在中等：語氣可以放鬆，仍保留一點分寸",
            "自在高：可用口語、省略句和熟人式玩笑",
        )
        intimacy = self._band(
            state["intimacy"],
            "親密低：維持清楚界線，不用親暱稱呼",
            "親密中等：可談較私人的感受",
            "親密高：情境合適時可直接說親暱或脆弱的話，但不可捏造共同經歷",
        )
        initiative = self._band(
            state["initiative"],
            "主動低：回答完即可，不強迫延伸話題",
            "主動中等：有明確切入點時再追問或延伸",
            "主動高：適合時主動補一個具體想法或追問；不要機械地每次都問問題",
        )
        if state["tension"] >= 60:
            tension = "緊張高：先正面處理不滿或誤會，暫停吐槽和曖昧"
        elif state["tension"] >= 30:
            tension = "有些緊張：措辭謹慎，先確認情緒再開玩笑"
        else:
            tension = "緊張低：不必刻意防備或反覆確認關係"
        if state["respect"] < 35:
            respect = "尊重低：可以質疑觀點，但不可羞辱或貶低使用者"
        elif state["respect"] < 70:
            respect = "尊重中等：直接回應觀點，有分歧就說明理由"
        else:
            respect = "尊重高：認真看待使用者的判斷與界線"
        sexual_comfort = self._band(
            state["sexual_comfort"],
            "成人親密話題自在低：即使模式允許也保持低強度，不主動加碼",
            "成人親密話題自在中等：可在設定範圍內自然承接，依對方反應調整",
            "成人親密話題自在高：可較自然地表達，但仍遵守模式上限與停止訊號",
        )
        intimate_safety = self._band(
            state["intimate_safety"],
            "親密安全感低：降低強度，不主動推進",
            "親密安全感中等：沿著目前互動自然小步前進",
            "親密安全感高：可以較放鬆，但停止或轉題仍立即優先",
        )
        return "；".join((familiarity, trust, affection, comfort, respect, intimacy, tension, initiative,
                         sexual_comfort, intimate_safety)) + "。"

    def set_relationship(self, name: str, value: int | float, reason: str = "使用者手動設定"):
        if name not in DEFAULT_RELATIONSHIP:
            raise ValueError("未知關係變數；請用 /relationship 查看名稱")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
            raise ValueError("關係數值須介於 0–100")
        current = self.relationship()
        target = round(float(value), 2)
        change = round(target - current[name], 2)
        current[name] = target
        with self.db:
            self.db.execute("INSERT INTO settings VALUES('relationship',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (json.dumps(current, ensure_ascii=False),))
            self.db.execute("INSERT INTO relationship_events(source,changes,reason) VALUES(?,?,?)",
                            ("manual", json.dumps({name: change}, ensure_ascii=False), reason))

    def reset_relationship(self):
        with self.db:
            self.db.execute("INSERT INTO settings VALUES('relationship',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (json.dumps(DEFAULT_RELATIONSHIP, ensure_ascii=False),))
            self.db.execute("DELETE FROM relationship_reviews")
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='pyramid_turns'").fetchone():
                self.db.execute("DELETE FROM pyramid_turns")
            self.db.execute("INSERT INTO relationship_events(source,changes,reason) VALUES(?,?,?)",
                            ("reset", "{}", "使用者重設關係"))

    def _apply_changes(self, current: dict[str, float], changes: dict, limits: dict | None = None) -> tuple[dict, dict]:
        updated, applied = dict(current), {}
        for name, raw in changes.items():
            if name not in DEFAULT_RELATIONSHIP or isinstance(raw, bool) or not isinstance(raw, (int, float)):
                continue
            change = float(raw)
            if not math.isfinite(change):
                continue
            if limits:
                if name not in limits:
                    continue
                lower, upper = limits[name]
                change = max(lower, min(upper, change))
            before = updated[name]
            updated[name] = round(max(0.0, min(100.0, before + change)), 2)
            actual = round(updated[name] - before, 2)
            if actual:
                applied[name] = actual
        return updated, applied

    def queue_relationship_review(self, user_text: str, assistant_text: str,
                                  user_message_id: int | None = None, assistant_message_id: int | None = None) -> int:
        """Apply small deterministic growth and queue bounded idle analysis."""
        current = self.relationship()
        updated, applied = self._apply_changes(current, {"familiarity": 0.6, "trust": 0.08, "comfort": 0.15})
        with self.db:
            self.db.execute("INSERT INTO settings VALUES('relationship',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (json.dumps(updated, ensure_ascii=False),))
            self.db.execute("INSERT INTO relationship_events(source,changes,reason) VALUES(?,?,?)",
                            ("conversation", json.dumps(applied, ensure_ascii=False), "完成一輪對話"))
            return self.db.execute(
                "INSERT INTO relationship_reviews(user_text,assistant_text,user_message_id,assistant_message_id) VALUES(?,?,?,?)",
                (user_text[:3500], assistant_text[:3500], user_message_id, assistant_message_id),
            ).lastrowid

    def next_relationship_review(self) -> dict | None:
        row = self.db.execute("SELECT * FROM relationship_reviews WHERE status='pending' ORDER BY id LIMIT 1").fetchone()
        if not row:
            return None
        with self.db:
            self.db.execute("UPDATE relationship_reviews SET status='running' WHERE id=?", (row["id"],))
        return dict(row)

    def finish_relationship_review(self, review_id: int, status: str):
        if status not in {"pending", "done", "failed"}:
            raise ValueError("關係分析狀態無效")
        with self.db:
            self.db.execute(
                "UPDATE relationship_reviews SET status=?,user_text=CASE WHEN ? IN ('done','failed') THEN '' ELSE user_text END,"
                "assistant_text=CASE WHEN ? IN ('done','failed') THEN '' ELSE assistant_text END WHERE id=?",
                (status, status, status, review_id),
            )

    def apply_relationship_analysis(self, review_id: int, changes: dict, reason: str, profile_ops: list | None = None,
                                    conversation_state: dict | None = None, engine_update: dict | None = None,
                                    adult_intimacy_event: dict | None = None) -> dict:
        review = self.db.execute("SELECT * FROM relationship_reviews WHERE id=?", (review_id,)).fetchone()
        if not review or review["status"] in {"done", "failed"}:
            return {}
        current = self.relationship()
        updated, applied = self._apply_changes(current, changes, ANALYSIS_LIMITS)
        adult_changes, adult_reason = adult_event_changes(
            review["user_text"], review["assistant_text"], adult_intimacy_event
        )
        updated, adult_applied = self._apply_changes(updated, adult_changes)
        safe_reason = " ".join(str(reason).split())[:160] or "模型未提供原因"
        with self.db:
            self.db.execute("INSERT INTO settings VALUES('relationship',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (json.dumps(updated, ensure_ascii=False),))
            if applied:
                self.db.execute("INSERT INTO relationship_events(source,changes,reason) VALUES(?,?,?)",
                                ("analysis", json.dumps(applied, ensure_ascii=False), safe_reason))
            if adult_applied:
                self.db.execute("INSERT INTO relationship_events(source,changes,reason) VALUES(?,?,?)",
                                ("adult_intimacy", json.dumps(adult_applied, ensure_ascii=False), adult_reason))
            profile_applied = self._apply_profile_ops_in_transaction(profile_ops or [])
            latest_id = self.db.execute("SELECT max(id) FROM messages").fetchone()[0]
            if review["assistant_message_id"] == latest_id or latest_id is None:
                self.apply_conversation_state(conversation_state)
            self.engine.analyze(review_id, review["user_message_id"], review["assistant_message_id"], engine_update)
            self.db.execute("UPDATE relationship_reviews SET status='done',user_text='',assistant_text='' WHERE id=?", (review_id,))
        if profile_applied:
            self._write_profile_file()
        return {**applied, **adult_applied}

    def pending_relationship_reviews(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM relationship_reviews WHERE status IN ('pending','running')").fetchone()[0]

    def relationship_events(self, limit: int = 5) -> list[dict]:
        rows = self.db.execute("SELECT created_at,source,changes,reason FROM relationship_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def memories(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM memories ORDER BY id")]

    def profile_facts(self) -> list[dict]:
        return [dict(row) for row in self.db.execute(
            "SELECT id,category,value,confidence,source,updated_at FROM user_profile_facts ORDER BY id"
        )]

    def last_delivered_exchange(self) -> dict | None:
        assistant = self.db.execute(
            "SELECT id,content FROM messages WHERE role='assistant' AND delivery_state!='generated' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not assistant:
            return None
        user = self.db.execute(
            "SELECT id,content FROM messages WHERE role='user' AND id<? ORDER BY id DESC LIMIT 1", (assistant["id"],)
        ).fetchone()
        if not user:
            return None
        return {
            "user_message_id": user["id"], "user": user["content"],
            "assistant_message_id": assistant["id"], "assistant": assistant["content"],
        }

    def save_gpt_review_candidate(self, payload: dict):
        self.set("gpt_review_candidate", json.dumps(payload, ensure_ascii=False))

    def gpt_review_candidate(self) -> dict | None:
        raw = self.get("gpt_review_candidate")
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    def accept_gpt_review(self) -> tuple[bool, int]:
        candidate = self.gpt_review_candidate()
        if not candidate or not isinstance(candidate.get("assistant_message_id"), int) or not candidate.get("review"):
            raise ValueError("目前沒有可接受的 GPT 檢查；請先使用 /GPT")
        payload = {
            "schema_version": 1,
            "source": "telegram_gpt_review",
            "accepted_at": datetime.now(timezone.utc).isoformat(),
            **candidate,
        }
        with self.db:
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO persona_policy_feedback(assistant_message_id,payload,created_at) VALUES(?,?,?)",
                (candidate["assistant_message_id"], json.dumps(payload, ensure_ascii=False), payload["accepted_at"]),
            )
        self._write_persona_policy_feedback()
        row = self.db.execute(
            "SELECT id FROM persona_policy_feedback WHERE assistant_message_id=?", (candidate["assistant_message_id"],)
        ).fetchone()
        return cursor.rowcount > 0, row["id"]

    def _write_persona_policy_feedback(self):
        rows = self.db.execute("SELECT payload FROM persona_policy_feedback ORDER BY id").fetchall()
        temporary = self.feedback_path.with_name(self.feedback_path.name + ".tmp")
        temporary.write_text("".join(row["payload"] + "\n" for row in rows), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(self.feedback_path)
        self.feedback_path.chmod(0o600)

    def _write_profile_file(self):
        facts = self.profile_facts()
        lines = ["# 使用者 Profile", "", "> 由對話逐步學習；可在 Telegram 用 /profile 檢查與修正。", ""]
        if facts:
            lines.extend(f"- [#{fact['id']}｜{fact['category']}] {fact['value']}" for fact in facts)
        else:
            lines.append("（目前空白，會隨對話逐步學習。）")
        temporary = self.profile_path.with_name(self.profile_path.name + ".tmp")
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(self.profile_path)
        self.profile_path.chmod(0o600)

    @staticmethod
    def _profile_value(category: str, value: str) -> tuple[str, str]:
        category = " ".join(str(category).split())[:40]
        value = " ".join(str(value).split())[:240]
        if not category or not value:
            raise ValueError("Profile 類別與內容不可為空")
        return category, value

    def set_profile_fact(self, category: str, value: str, source: str = "manual") -> int:
        category, value = self._profile_value(category, value)
        now = datetime.now(timezone.utc).isoformat()
        with self.db:
            row = self.db.execute(
                "SELECT id FROM user_profile_facts WHERE lower(category)=lower(?) AND lower(value)=lower(?)",
                (category, value),
            ).fetchone()
            if row:
                self.db.execute("UPDATE user_profile_facts SET confidence=1,source=?,updated_at=? WHERE id=?",
                                (source, now, row["id"]))
                identifier = row["id"]
            else:
                if len(self.profile_facts()) >= 50:
                    raise ValueError("Profile 最多保存 50 筆，請先刪除不再需要的資料")
                identifier = self.db.execute(
                    "INSERT INTO user_profile_facts(category,value,confidence,source,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (category, value, 1.0 if source == "manual" else 0.55, source, now, now),
                ).lastrowid
        self._write_profile_file()
        return identifier

    def delete_profile_fact(self, fact_id: int) -> bool:
        with self.db:
            removed = self.db.execute("DELETE FROM user_profile_facts WHERE id=?", (fact_id,)).rowcount > 0
        if removed:
            self._write_profile_file()
        return removed

    def update_profile_fact(self, fact_id: int, category: str, value: str) -> bool:
        category, value = self._profile_value(category, value)
        with self.db:
            changed = self.db.execute(
                "UPDATE user_profile_facts SET category=?,value=?,confidence=1,source='manual',updated_at=? WHERE id=?",
                (category, value, datetime.now(timezone.utc).isoformat(), fact_id),
            ).rowcount > 0
        if changed:
            self._write_profile_file()
        return changed

    def reset_profile(self):
        with self.db:
            self.db.execute("DELETE FROM user_profile_facts")
        self._write_profile_file()

    def _apply_profile_ops_in_transaction(self, operations: list) -> list[dict]:
        applied, now = [], datetime.now(timezone.utc).isoformat()
        for raw in operations[:2]:
            if not isinstance(raw, dict) or raw.get("action") not in {"add", "update", "delete"}:
                continue
            action = raw["action"]
            if action == "delete":
                identifier = raw.get("id")
                if isinstance(identifier, int) and self.db.execute(
                    "DELETE FROM user_profile_facts WHERE id=?", (identifier,)
                ).rowcount:
                    applied.append({"action": "delete", "id": identifier})
                continue
            try:
                category, value = self._profile_value(raw.get("category", ""), raw.get("value", ""))
            except ValueError:
                continue
            if action == "update":
                identifier = raw.get("id")
                if isinstance(identifier, int) and self.db.execute(
                    "UPDATE user_profile_facts SET category=?,value=?,confidence=max(confidence,0.65),source='learned',updated_at=? WHERE id=?",
                    (category, value, now, identifier),
                ).rowcount:
                    applied.append({"action": "update", "id": identifier})
                continue
            duplicate = self.db.execute(
                "SELECT id FROM user_profile_facts WHERE lower(category)=lower(?) AND lower(value)=lower(?)",
                (category, value),
            ).fetchone()
            if duplicate:
                self.db.execute(
                    "UPDATE user_profile_facts SET confidence=min(0.95,confidence+0.1),updated_at=? WHERE id=?",
                    (now, duplicate["id"]),
                )
                applied.append({"action": "confirm", "id": duplicate["id"]})
            elif self.db.execute("SELECT COUNT(*) FROM user_profile_facts").fetchone()[0] < 50:
                identifier = self.db.execute(
                    "INSERT INTO user_profile_facts(category,value,confidence,source,created_at,updated_at) VALUES(?,?,0.55,'learned',?,?)",
                    (category, value, now, now),
                ).lastrowid
                applied.append({"action": "add", "id": identifier})
        return applied

    def remember(self, text: str) -> int:
        if not 1 <= len(text) <= 500:
            raise ValueError("每筆記憶限 1–500 字")
        if len(self.memories()) >= 30:
            raise ValueError("記憶上限為 30 筆，請先 /forget 刪除不需要的記憶")
        with self.db:
            return self.db.execute("INSERT INTO memories(content) VALUES(?)", (text,)).lastrowid

    def forget(self, memory_id: int) -> bool:
        from .memory_sources import stop_recalling
        return stop_recalling(self,memory_id)

    def reset(self):
        with self.db:
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_tasks'").fetchone():self.db.execute('DELETE FROM memory_tasks')
            self.db.execute("DELETE FROM settings WHERE key IN ('last_strategy','recent_strategies','last_prompt','last_prompt_kind','last_prompt_image_id','last_text_prompt','last_text_prompt_assistant_id','gpt_review_candidate')")
            self.engine.clear_dialogue()
            self.scene.clear()
            self.db.execute("DELETE FROM messages")
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='external_cache'").fetchone():self.db.execute('DELETE FROM external_cache')
            self.db.execute("DELETE FROM settings WHERE key='last_external_result'")
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='retrieval_vectors'").fetchone():
                self.db.execute("DELETE FROM retrieval_vectors")
            self.db.execute("DELETE FROM settings WHERE key IN ('summary','summary_through','pyramid_state','last_pyramid_trace','last_story_background_trace','last_response_background_trace','pyramid_story_retry_at','last_pyramid_background_trace','pyramid_casual_retry_at')")
            self.db.execute("DELETE FROM relationship_reviews")
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='pyramid_turns'").fetchone():
                self.db.execute("DELETE FROM pyramid_turns")
            self.db.execute(
                "INSERT INTO settings VALUES('conversation_state',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(DEFAULT_CONVERSATION_STATE, ensure_ascii=False),),
            )

    def summary_batch(self):
        through = int(self.get("summary_through", "0"))
        rows = self.db.execute("SELECT * FROM messages WHERE id>? AND delivery_state!='generated' AND memory_scope='chat' ORDER BY id", (through,)).fetchall()
        # Summarize as soon as six older messages exist beyond the 12-message
        # conversational tail. The old threshold left a middle block in neither
        # the summary nor the prompt.
        if len(rows) < 18:
            return []
        return [dict(r) for r in rows[:-12]][:12]

    def save_summary(self, summary: str, through: int):
        with self.db:
            self.db.executemany("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", [("summary", summary), ("summary_through", str(through))])

    def context(self, now: datetime | None = None) -> list[dict]:
        """Build the Writer prompt from facts only; policy inputs stay upstream."""
        # The long-term summary carries older continuity. The Writer only needs
        # the immediately preceding exchange plus the current user turn.
        recent = self.recent_since_summary(limit=3)
        while recent and recent[0]["role"] == "assistant":
            recent.pop(0)
        current_user = recent[-1]["content"] if recent and recent[-1]["role"] == "user" else ""
        state = self.engine.state(now)
        knowledge_rows = self.engine.knowledge(8)
        communication = {}
        for fact in knowledge_rows:
            if fact["fact_key"] in {"mood", "location", "activity"}:
                communication[fact["fact_key"]] = (
                    "acknowledged" if fact["acknowledged"] else "shared" if fact["disclosed"] else "not_shared"
                )
        current_facts = {
            "mood": state["mood"], "location": state["location"], "activity": state["activity"],
            "availability": state["availability"], "energy": self._state_band(state["energy"]),
            "stress": self._state_band(state["stress"]), "communication_status": communication,
        }
        world = self.scene.writer_view()
        if world['status'] == 'active':
            # Calendar location is a separate simulation, not the roleplay's
            # physical location. Unknown is preferable to mixing both worlds.
            current_facts['location'] = world['facts'].get('character.location', world['facts'].get('scene.location', '未確認'))
            current_facts['activity'] = world['facts'].get('character.activity', world['facts'].get('scene.interaction', '未確認'))
        memories = [m["content"] for m in self.memories()]
        profile = [{"category": fact["category"], "value": fact["value"]} for fact in self.profile_facts()]
        knowledge = []
        for fact in knowledge_rows:
            if fact["fact_key"] in {"mood", "location", "activity"}:
                continue
            status = "acknowledged" if fact["acknowledged"] else "shared" if fact["disclosed"] else "not_shared"
            knowledge.append({"fact": fact["fact_key"], "value": fact["value"], "communication_status": status})
        system = (
            "You are the final dialogue Writer. Write only the character's reply; never reveal controls or reasoning. "
            "Use the current user message and recent dialogue for conversational continuity. Treat <current_facts> as "
            "authoritative when old dialogue conflicts with it. The response plan contains the final upstream decision; "
            "follow it without recalculating personality, relationship, emotion, or state. Do not invent "
            "shared experiences, physical proximity, completed actions, tool results, or facts absent from the supplied "
            "sources. Do not copy old assistant wording or turn every reply into a catchphrase.\n"
            f"<identity_facts>{json.dumps(self.identity_facts(), ensure_ascii=False)}</identity_facts>\n"
            f"<persona_facts>{json.dumps(self.writer_persona(), ensure_ascii=False)}</persona_facts>\n"
            f"<current_facts>{json.dumps(current_facts, ensure_ascii=False)}</current_facts>"
            f"\n<scene_facts>{json.dumps(world, ensure_ascii=False)}</scene_facts>"
            "\nEntity IDs: user is the message sender; character is the assistant persona. "
            "In user messages I/me means user and you means character; in assistant messages the reverse applies. "
            "Use scene_facts for current scene continuity. Prior assistant prose is a historical utterance, "
            "not proof of completed action, consent, user emotion, or current location. "
            "In remote_chat do not invent shared physical presence. In shared_fiction keep events fictional "
            "and preserve the subject of each fact. Requests and promises are not completed events."
        )
        if memories:
            system += f"\n<explicit_memories>{json.dumps(memories, ensure_ascii=False)}</explicit_memories>"
        if profile:
            system += f"\n<user_profile>{json.dumps(profile, ensure_ascii=False)}</user_profile>"
        summary = self.get("summary")
        from .memory_sources import normalized
        try:exclusions=json.loads(self.get('pyramid_state') or '{}').get('memory_exclusions',[])
        except ValueError:exclusions=[]
        if any(normalized(b.get('text','')) in normalized(summary) for b in exclusions if b.get('text')):summary=''
        if summary:
            system += f"\n<past_dialogue_summary>{summary}</past_dialogue_summary>"
            system += "\nThe legacy summary is fallible background only; never use it to override scene_facts "
            system += "or infer who performed a current action."
        if knowledge:
            system += f"\n<character_facts>{json.dumps(knowledge, ensure_ascii=False)}</character_facts>"
        system += "\ncommunication_status records whether a fact was already shared; it is not permission to claim the user fully understood it."
        if self._appearance_relevant(current_user):
            appearance = self.appearance()
            current = self.life_snapshot(now)["current"] if now is not None else None
            appearance_facts = {
                "body": appearance["body"],
                "current_clothing": current["clothing"] if current and current["clothing"] else appearance["clothing"],
                "current_accessories": current["accessories"] if current and current["accessories"] else appearance["accessories"],
            }
            system += f"\n<appearance_facts>{json.dumps(appearance_facts, ensure_ascii=False)}</appearance_facts>"
        if world['status'] != 'active' and now is not None and self.get("life_enabled") == "true" and self._timeline_relevant(current_user):
            life = self.life_context(now)
            if life:
                system += f"\n<relevant_timeline>{life}</relevant_timeline>"
        system += f"\n<target_language>{self.output_language()}</target_language>"
        if recent and recent[-1]["role"] == "user":
            recent[-1] = dict(recent[-1])
            recent[-1]["content"] = (
                self.identity_control() + "\n" + self.language_control() + "\n" + recent[-1]["content"]
            )
        # LLM.fit_context uses the model tokenizer to fit actual context capacity.
        messages = [{"role": "system", "content": system}] + recent
        return messages

    def enqueue_image(self, update_id: int, chat_id: int, prompt: str, kind: str = 'general') -> int:
        if kind not in {'general','selfie','pov','user_photo'}:raise ValueError('未知圖片模式')
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO jobs(update_id,chat_id,prompt,kind) VALUES(?,?,?,?)", (update_id, chat_id, prompt,kind))
        return self.db.execute("SELECT id FROM jobs WHERE update_id=?", (update_id,)).fetchone()[0]

    def next_job(self):
        row = self.db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
        if row:
            self.update_job(row["id"], "running")
            return dict(row)
        return None

    def update_job(self, job_id: int, status: str, error: str | None = None, path: str | None = None):
        with self.db:
            self.db.execute("UPDATE jobs SET status=?,error=?,path=COALESCE(?,path) WHERE id=?", (status, error, path, job_id))

    def clear_job_path(self, job_id: int):
        with self.db:
            self.db.execute("UPDATE jobs SET path=NULL WHERE id=?", (job_id,))

    def jobs(self):
        return [dict(r) for r in self.db.execute("SELECT id,status,error FROM jobs ORDER BY id DESC LIMIT 5")]

    def has_daily_plan(self, day: str) -> bool:
        return self.db.execute("SELECT 1 FROM life_activities WHERE day=? LIMIT 1", (day,)).fetchone() is not None

    def save_daily_plan(self, day: str, activities: list[dict], current_minute: int):
        active_index = max((i for i, item in enumerate(activities) if item["start_minute"] <= current_minute), default=0)
        with self.db:
            self.db.execute("DELETE FROM life_activities WHERE day=?", (day,))
            for index, item in enumerate(activities):
                status = "completed" if index < active_index else "active" if index == active_index else "planned"
                self.db.execute(
                    "INSERT INTO life_activities(day,start_minute,activity,location,outcome,mood,availability,share_worthy,status,clothing,accessories) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (day, item["start_minute"], item["activity"], item["location"], item["outcome"],
                     item["mood"], item["availability"], int(item["share_worthy"]), status,
                     item.get("clothing", self.appearance()["clothing"]),
                     item.get("accessories", self.appearance()["accessories"])),
                )

    def advance_life(self, now: datetime) -> list[dict]:
        day, minute = now.date().isoformat(), now.hour * 60 + now.minute
        rows = self.db.execute("SELECT * FROM life_activities WHERE day=? ORDER BY start_minute", (day,)).fetchall()
        if not rows:
            return []
        active = max((i for i, row in enumerate(rows) if row["start_minute"] <= minute), default=0)
        completed = []
        with self.db:
            for index, row in enumerate(rows):
                target = "completed" if index < active else "active" if index == active else "planned"
                if row["status"] != target:
                    self.db.execute("UPDATE life_activities SET status=? WHERE id=?", (target, row["id"]))
                if target == "completed" and row["status"] in {"active", "planned"}:
                    item = dict(row)
                    completed.append(item)
                    if row["share_worthy"]:
                        context = json.dumps({key: row[key] for key in ("activity", "location", "outcome", "mood")}, ensure_ascii=False)
                        self.db.execute(
                            "INSERT OR IGNORE INTO proactive_candidates(kind,source_key,due_at,context) VALUES('life_event',?,?,?)",
                            (f"life:{row['id']}", now.isoformat(), context),
                        )
            current = dict(rows[active])
            # This is an explicit simulation transition; calendar text alone is
            # never presented as proof. Interaction overrides survive the tick.
            self.engine.tick(now, current)
        return completed

    def life_snapshot(self, now: datetime) -> dict:
        day = now.date().isoformat()
        rows = [dict(row) for row in self.db.execute(
            "SELECT * FROM life_activities WHERE day=? ORDER BY start_minute", (day,)
        )]
        return {"day": day, "current": next((row for row in rows if row["status"] == "active"), None),
                "activities": rows}

    def life_context(self, now: datetime) -> str:
        snapshot = self.life_snapshot(now)
        current = snapshot["current"]
        if not current:
            return ""
        recent = [row for row in snapshot["activities"] if row["status"] == "completed"][-2:]
        clothing = current["clothing"] or self.appearance()["clothing"]
        accessories = current["accessories"] or self.appearance()["accessories"]
        lines = [f"現在：{current['activity']}（{current['location']}，心情：{current['mood']}）",
                 f"當下造型：{clothing}；配件：{accessories}"]
        if recent:
            lines.append("最近完成：" + "；".join(f"{row['activity']}，{row['outcome']}" for row in recent))
        return "\n".join(lines)

    def next_proactive_candidate(self, now: datetime) -> dict | None:
        cutoff = (now - timedelta(hours=24)).isoformat()
        with self.db:
            self.db.execute(
                "UPDATE proactive_candidates SET status='expired',reason='超過 24 小時' "
                "WHERE status='pending' AND due_at<?", (cutoff,)
            )
            row = self.db.execute(
                "SELECT * FROM proactive_candidates WHERE status='pending' AND due_at<=? ORDER BY id LIMIT 1",
                (now.isoformat(),),
            ).fetchone()
            if row:
                self.db.execute("UPDATE proactive_candidates SET status='running' WHERE id=?", (row["id"],))
        return dict(row) if row else None

    def finish_proactive_candidate(self, candidate_id: int, status: str, reason: str = ""):
        if status not in {"pending", "sending", "sent", "skipped", "failed", "expired"}:
            raise ValueError("主動訊息狀態無效")
        with self.db:
            self.db.execute("UPDATE proactive_candidates SET status=?,reason=? WHERE id=?",
                            (status, " ".join(reason.split())[:160], candidate_id))

    def record_proactive_sent(self, candidate: dict, message: str, now: datetime):
        unanswered = int(self.get("proactive_unanswered", "0")) + 1
        with self.db:
            self.db.execute("UPDATE proactive_candidates SET status='sent' WHERE id=?", (candidate["id"],))
            self.db.execute("INSERT INTO proactive_log(created_at,kind,status,reason) VALUES(?,?,?,?)",
                            (now.isoformat(), candidate["kind"], "sent", candidate.get("reason", "")))
            self.db.executemany(
                "INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [("last_proactive_at", now.isoformat()), ("proactive_unanswered", str(unanswered))],
            )
            identifier = self.db.execute("INSERT INTO messages(role,content) VALUES('assistant',?)", (message,)).lastrowid
            self.engine.note_message(identifier, "assistant")
            self.engine.delivered(identifier)
            # Proactive turns use the same disclosure/state analysis pipeline.
            last_user = self.db.execute("SELECT id,content FROM messages WHERE role='user' ORDER BY id DESC LIMIT 1").fetchone()
            if last_user:
                self.db.execute(
                    "INSERT INTO relationship_reviews(user_text,assistant_text,user_message_id,assistant_message_id) VALUES(?,?,?,?)",
                    ("", message[:3500], last_user['id'], identifier),
                )

    def proactive_sent_today(self, now: datetime) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM proactive_log WHERE status='sent' AND created_at LIKE ?",
            (now.date().isoformat() + "%",),
        ).fetchone()[0]
