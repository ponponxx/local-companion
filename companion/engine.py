"""Persistent character state. Events are evidence; projections are rebuildable.

The LLM proposes observations. This module validates and commits them, and owns
time progression, delivery evidence, knowledge visibility and behavior policy.
All writes run inside Store's SQLite transaction.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from .emotions import EMOTION_LABELS, normalize_emotions, emotion_mood


def utcnow():
    return datetime.now(timezone.utc)


def clean(value, limit=240):
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def behavior_policy(traits, relationship, state):
    """Joint policy, with conflict and current distress taking precedence."""
    tension = relationship["tension"] / 100
    stress = state["stress"] / 100
    energy = state["energy"] / 100
    closeness = min(relationship["trust"], relationship["comfort"]) / 100
    emotions = normalize_emotions(state.get('emotions'))
    distress = max(emotions[key] for key in ('sadness', 'anger', 'fear', 'disgust')) / 100
    teasing = traits["teasing"] * closeness * (1 - tension) * (1 - stress) * (1 - distress)
    warmth = relationship["affection"] * (1 - tension * .6)
    initiative = relationship["initiative"] * (.3 + .7 * energy) * (1 - stress * .5)
    erotic_readiness = (
        relationship.get("erotic_interest", 0) * .4
        + relationship.get("sexual_comfort", 0) * .35
        + relationship.get("intimate_safety", 0) * .25
    ) * (1 - tension) * (1 - stress * .5)
    return {
        "teasing": round(teasing, 1), "warmth": round(warmth, 1),
        "initiative": round(initiative, 1),
        "erotic_readiness": round(erotic_readiness, 1),
        "intimate_safety": relationship.get("intimate_safety", 0),
        "directness": round((traits["assertiveness"] + traits["impulsiveness"]) / 2, 1),
        "expression": round(traits["expressiveness"] * (.4 + .6 * energy), 1),
        "curiosity": round(traits["openness"] * (.4 + .6 * energy), 1),
        "deliberation": round((traits["conscientiousness"] + 100 - traits["impulsiveness"]) / 2, 1),
        "gentleness": round((traits["agreeableness"] + traits["emotional_stability"] + warmth) / 3, 1),
        "personal_disclosure": round(min(relationship["intimacy"], relationship["trust"]) * (1 - tension), 1),
        "respect": relationship["respect"],
        "familiarity": relationship["familiarity"],
        "sociability": round(traits["extraversion"] * energy, 1),
        "priority": "repair" if tension >= .6 else "gentle" if max(stress, distress) >= .6 else "engage",
        "instructions": (
            "先處理衝突，暫停吐槽與曖昧。" if tension >= .6 else
            "角色壓力或負面情緒高，降低刺激與吐槽；不把角色感受當成使用者感受。" if max(stress, distress) >= .6 else
            "直接承接當前話題；關心不等於討好，吐槽不是固定開場。"
        ) + ("角色自己精力低：只回一到兩句，不提出活動邀約、不連續追問；不要把自己的疲倦說成使用者疲倦。" if energy < .3 else ""),
    }


class CharacterEngine:
    def __init__(self, db):
        self.db = db
        db.executescript("""
            CREATE TABLE IF NOT EXISTS character_events(
              id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, kind TEXT NOT NULL,
              source TEXT NOT NULL UNIQUE, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS character_projection(
              key TEXT PRIMARY KEY, payload TEXT NOT NULL, event_id INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS character_knowledge(
              id INTEGER PRIMARY KEY, fact_key TEXT NOT NULL, value TEXT NOT NULL,
              valid_from TEXT NOT NULL, valid_to TEXT, source_event INTEGER NOT NULL,
              FOREIGN KEY(source_event) REFERENCES character_events(id));
            CREATE TABLE IF NOT EXISTS character_disclosures(
              fact_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
              status TEXT NOT NULL, evidence TEXT NOT NULL, event_id INTEGER NOT NULL,
              PRIMARY KEY(fact_id,message_id,status));
        """)
        if not self._projection("state"):
            with db:
                self.emit("initialized", "engine:v1", {"state": {
                    "energy": 70.0, "stress": 20.0, "mood": "平靜",
                    "emotions": normalize_emotions({}),
                    "location": "未確認", "activity": "未確認", "availability": "free",
                    "updated_at": utcnow().isoformat(), "override_until": "",
                }})
        elif 'emotions' not in self._projection('state'):
            # Persist the schema addition once. Do not infer intensities from a
            # legacy free-text mood, because that would manufacture history.
            with db:
                self.emit('migrated', 'engine:v2:emotions', {'state': {
                    'emotions': normalize_emotions({}), 'mood': '平靜',
                }})

    def _projection(self, key):
        row = self.db.execute("SELECT payload FROM character_projection WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def emit(self, kind, source, payload, now=None):
        now = now or utcnow()
        cursor = self.db.execute(
            "INSERT OR IGNORE INTO character_events(created_at,kind,source,payload) VALUES(?,?,?,?)",
            (now.isoformat(), kind, source, json.dumps(payload, ensure_ascii=False)),
        )
        if cursor.rowcount:
            self._reduce(cursor.lastrowid, kind, payload, now.isoformat())
        return self.db.execute("SELECT id FROM character_events WHERE source=?", (source,)).fetchone()[0]

    def _reduce(self, event_id, kind, payload, timestamp):
        if "state" in payload:
            current = self._projection("state") or {}
            current.update(payload["state"])
            self.db.execute(
                "INSERT INTO character_projection VALUES('state',?,?) ON CONFLICT(key) "
                "DO UPDATE SET payload=excluded.payload,event_id=excluded.event_id",
                (json.dumps(current, ensure_ascii=False), event_id),
            )
        for fact in payload.get("facts", []):
            current = self.db.execute(
                "SELECT id,value FROM character_knowledge WHERE fact_key=? AND valid_to IS NULL", (fact["key"],)
            ).fetchone()
            if current and current["value"] == fact["value"]:
                continue
            self.db.execute("UPDATE character_knowledge SET valid_to=? WHERE fact_key=? AND valid_to IS NULL",
                            (timestamp, fact["key"]))
            self.db.execute(
                "INSERT INTO character_knowledge(fact_key,value,valid_from,source_event) VALUES(?,?,?,?)",
                (fact["key"], fact["value"], timestamp, event_id),
            )
        if kind in {"disclosed", "acknowledged"}:
            self.db.execute("INSERT OR IGNORE INTO character_disclosures VALUES(?,?,?,?,?)",
                            (payload["fact_id"], payload["message_id"], kind, payload["evidence"], event_id))

    def rebuild(self):
        """Rebuild all projections without inference or new model calls."""
        with self.db:
            self.db.execute("DELETE FROM character_projection")
            self.db.execute("DELETE FROM character_disclosures")
            self.db.execute("DELETE FROM character_knowledge")
            for row in self.db.execute("SELECT * FROM character_events ORDER BY id").fetchall():
                self._reduce(row["id"], row["kind"], json.loads(row["payload"]), row["created_at"])

    def state(self, now=None):
        state = dict(self._projection("state"))
        now = now or utcnow()
        elapsed = max(0, (now - datetime.fromisoformat(state["updated_at"])).total_seconds() / 3600)
        # Backward-compatible projection: legacy databases start with unknown
        # intensity (=0); free-text mood is not reverse-inferred into numbers.
        state['emotions'] = {key: round(value * math.exp(-elapsed / 2), 2)
                             for key, value in normalize_emotions(state.get('emotions')).items()}
        state['mood'] = emotion_mood(state['emotions'])
        # Mood/stress relax toward baseline; energy follows the actual simulated
        # activity, not an unrelated planned calendar slot.
        state["stress"] = round(20 + (state["stress"] - 20) * math.exp(-elapsed / 4), 2)
        rate = 12 if state["availability"] == "sleep" else -3 if state["availability"] == "busy" else 2
        state["energy"] = round(max(0, min(100, state["energy"] + rate * elapsed)), 2)
        if elapsed >= 4:
            state["mood"] = "平靜"
        return state

    def tick(self, now, planned=None):
        bucket = int(now.timestamp()) // 300
        source = f"tick:{bucket}:{planned.get('id', 'none') if planned else 'none'}"
        if self.db.execute("SELECT 1 FROM character_events WHERE source=?", (source,)).fetchone():
            return
        state = self.state(now)
        override = state.get("override_until")
        if planned and (not override or now >= datetime.fromisoformat(override)):
            state.update({key: planned[key] for key in ("activity", "location", "availability")})
            state["override_until"] = ""
        state["updated_at"] = now.isoformat()
        facts = [{"key": key, "value": state[key]} for key in ("activity", "location", "mood")]
        self.emit("time_advanced", source, {"state": state, "facts": facts}, now)

    def note_message(self, identifier, role):
        self.emit("message_recorded", f"message:{identifier}", {"message_id": identifier, "role": role})

    def delivered(self, identifier):
        self.emit("message_delivered", f"delivered:{identifier}", {"message_id": identifier})

    def knowledge(self, limit=12):
        rows = self.db.execute("""
            SELECT k.*, EXISTS(SELECT 1 FROM character_disclosures d WHERE d.fact_id=k.id
              AND d.status='disclosed') AS disclosed,
              EXISTS(SELECT 1 FROM character_disclosures d WHERE d.fact_id=k.id
              AND d.status='acknowledged') AS acknowledged
            FROM character_knowledge k WHERE valid_to IS NULL ORDER BY k.id DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(row) for row in rows]

    def analyze(self, review_id, user_id, assistant_id, proposed):
        """Validate proposed state/disclosure operations against source messages."""
        if not isinstance(proposed, dict) or not user_id or not assistant_id:
            return
        user = self.db.execute("SELECT content FROM messages WHERE id=? AND role='user'", (user_id,)).fetchone()
        answer = self.db.execute("SELECT content FROM messages WHERE id=? AND role='assistant'", (assistant_id,)).fetchone()
        if not user or not answer:
            return
        now = utcnow()
        latest = self.db.execute("SELECT max(id) FROM messages").fetchone()[0] == assistant_id
        evidence = clean(proposed.get("evidence"))
        # State changes need an explicit user statement, never assistant prose
        # that can invent a location or action. Old reviews cannot overwrite now.
        patch = proposed.get("state", {})
        if latest and evidence and evidence in user[0] and isinstance(patch, dict):
            state = self.state(now)
            for key in ("energy", "stress"):
                delta = patch.get(key + "_delta")
                if isinstance(delta, (int, float)) and not isinstance(delta, bool) and math.isfinite(delta):
                    state[key] = round(max(0, min(100, state[key] + max(-10, min(10, delta)))), 2)
            deltas = patch.get('emotion_deltas', {})
            if isinstance(deltas, dict):
                for key in EMOTION_LABELS:
                    delta = deltas.get(key)
                    if isinstance(delta, (int, float)) and not isinstance(delta, bool) and math.isfinite(delta):
                        state['emotions'][key] = round(max(0, min(100, state['emotions'][key] + max(-10, min(10, delta)))), 2)
            state['mood'] = emotion_mood(state['emotions'])
            for key in ("activity", "location"):
                if clean(patch.get(key)):
                    state[key] = clean(patch[key], 100)
            if patch.get("availability") in {"free", "busy", "sleep"}:
                state["availability"] = patch["availability"]
            state["updated_at"] = now.isoformat()
            if any(key in patch for key in ("activity", "location", "availability")):
                state["override_until"] = (now + timedelta(hours=2)).isoformat()
            facts = [{"key": key, "value": state[key]} for key in ("mood", "activity", "location")]
            self.emit("interaction_state", f"review:{review_id}:state", {"state": state, "facts": facts, "evidence": evidence}, now)
        ops = proposed.get("knowledge_ops", [])
        facts = proposed.get("facts", [])
        accepted = []
        for fact in facts[:3] if isinstance(facts, list) else []:
            if not isinstance(fact, dict):
                continue
            quote = clean(fact.get('evidence'))
            key, value = clean(fact.get('key'), 80), clean(fact.get('value'))
            # Additional character facts are sourced from explicit user-authored
            # character observations, never generated autobiography.
            if key and value and quote and quote in user[0]:
                accepted.append({'key': 'character.' + key, 'value': value})
        if accepted and latest:
            self.emit('character_observation', f'review:{review_id}:facts', {'facts': accepted, 'message_id': user_id}, now)
        for index, op in enumerate(ops[:4] if isinstance(ops, list) else []):
            if not isinstance(op, dict) or type(op.get("fact_id")) is not int:
                continue
            fact = self.db.execute("SELECT id FROM character_knowledge WHERE id=?", (op["fact_id"],)).fetchone()
            quote = clean(op.get("evidence"))
            if not fact or not quote:
                continue
            action = op.get("action")
            if action == "disclosed":
                delivered = self.db.execute("SELECT 1 FROM character_events WHERE source=?", (f"delivered:{assistant_id}",)).fetchone()
                if not delivered or quote not in answer[0]:
                    continue
                message_id = assistant_id
            elif action == "acknowledged":
                prior = self.db.execute("SELECT 1 FROM character_disclosures WHERE fact_id=? AND status='disclosed' AND message_id<?",
                                        (fact[0], user_id)).fetchone()
                if not prior or quote not in user[0] or len(quote) < 4:
                    continue
                message_id = user_id
            else:
                continue
            self.emit(action, f"review:{review_id}:knowledge:{index}", {
                "fact_id": fact[0], "message_id": message_id, "evidence": quote,
            }, now)

    def clear_dialogue(self):
        # Privacy operations must remove derived evidence as well as transcripts.
        self.db.execute("DELETE FROM character_disclosures")
        self.db.execute("DELETE FROM character_knowledge")
        self.db.execute("DELETE FROM character_projection")
        self.db.execute("DELETE FROM character_events")
        self.emit("initialized", "engine:v1", {"state": {
            "energy": 70.0, "stress": 20.0, "mood": "平靜", "location": "未確認",
            "emotions": normalize_emotions({}),
            "activity": "未確認", "availability": "free", "updated_at": utcnow().isoformat(), "override_until": "",
        }})
