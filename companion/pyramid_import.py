"""Shared read-only legacy snapshot import for Web and Telegram migration."""
import json
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from .config import Config
from .pyramid import initial_state, date, validate_state, PyramidCore


def import_legacy_state(path, now=None, full_history=False):
    if not path.is_file(): raise ValueError('正式資料庫不存在；可以先用乾淨角色')
    # SQLite backup provides a consistent read snapshot; only allowlisted fields are extracted.
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as source, sqlite3.connect(':memory:') as copy:
        source.backup(copy)
        settings = {r[0]: r[1] for r in copy.execute('SELECT key,value FROM settings')}
        def saved(key, default):
            try: return json.loads(settings.get(key, json.dumps(default)))
            except ValueError: return default
        state = initial_state(now)
        state['story'] = saved('persona', state['story'])
        state['traits'].update(saved('traits', {}))
        state['relationship'].update(saved('relationship', {}))
        state['need_weights'].update(saved('need_weights', {}))
        state['adult_traits'].update(saved('adult_intimacy', {}))
        state['adult_policy'].update(saved('adult_policy', {}))
        appearance = saved('appearance', {})
        state['appearance'].update({'body': appearance.get('body', ''), 'accessories': appearance.get('accessories', '')})
        if appearance.get('clothing'):
            state['appearance']['legacy_outfit'] = appearance['clothing']
        for key in ('heat', 'tension'):
            state['intimacy'][key] = saved('intimacy_state', {}).get(key, 0)
        try:
            rows = copy.execute('SELECT key,payload FROM character_projection').fetchall()
            for key, payload in rows:
                if key == 'state':
                    current = json.loads(payload)
                    for field in ('energy', 'emotions', 'availability', 'activity'):
                        if field in current: state[field] = current[field]
                    state['acute_stress'] = current.get('stress', 20)
                    state['scene']['location'] = current.get('location', '')
        except sqlite3.OperationalError: pass
        try:
            row = copy.execute('SELECT payload FROM scene_projection WHERE singleton=1').fetchone()
            if row:
                scene = json.loads(row[0]); facts = scene.get('facts', {})
                def fact(key):
                    value = facts.get(key, {})
                    return value.get('value', '') if isinstance(value, dict) else value
                state['scene'].update(status=scene.get('status', 'none'))
                state['scene']['location'] = fact('scene.location') or fact('character.location') or state['scene']['location']
                state['appearance']['hair'] = fact('character.hair')
                if fact('character.clothing'): state['appearance']['legacy_outfit'] = fact('character.clothing')
                state['scene']['user_location'] = fact('user.location')
                for attr in ('pose', 'expression'):
                    if fact('character.' + attr): state['presentation'][attr] = {'value': fact('character.' + attr), 'source': 'legacy_import'}
        except sqlite3.OperationalError: pass
        try:
            zone = ZoneInfo(Config.load(require_credentials=False).timezone)
            day = date(state['now']).astimezone(zone).date().isoformat()
            rows = copy.execute("SELECT day,start_minute,activity,location,availability,clothing FROM life_activities WHERE day=? AND status IN ('active','planned') ORDER BY start_minute", (day,)).fetchall()
            state['schedule'] = [{'at': (datetime.fromisoformat(r[0]).replace(tzinfo=zone) + timedelta(minutes=r[1])).isoformat(),
                                  'activity': r[2], 'location': r[3], 'availability': r[4]} for r in rows]
        except sqlite3.OperationalError: pass
        try:
            categories = {'稱呼': 'name', '興趣': 'interest', '習慣': 'habit', '偏好': 'preference', '目標': 'goal', '界線': 'boundary'}
            state['user_profile'] = [{'id': str(r[0]), 'category': categories.get(r[1], r[1]), 'value': r[2], 'confidence': r[3]}
                                     for r in copy.execute('SELECT id,category,value,confidence FROM user_profile_facts')]
        except sqlite3.OperationalError: pass
        through = int(settings.get('summary_through', '0'))
        state['legacy_summary'] = settings.get('summary', '')
        columns={r[1] for r in copy.execute('PRAGMA table_info(messages)')}
        scope_filter=" AND memory_scope='chat'" if 'memory_scope' in columns else ''
        columns={r[1] for r in copy.execute('PRAGMA table_info(messages)')}
        recorded='recorded_at' if 'recorded_at' in columns else "''"
        context='context' if 'context' in columns else "'{}'"
        rows = copy.execute(f"SELECT id,role,content,{recorded},{context} FROM messages WHERE id>? AND delivery_state!='generated'"+scope_filter+" ORDER BY id", (0 if full_history else through,)).fetchall()
        state['history'] = [{'role':r[1], 'content':r[2], 'at':r[3], 'context':json.loads(r[4] or '{}'), 'turn_id':'legacy:'+str(r[0]), 'message_id':r[0]} for r in rows]
        if full_history:
            state['_legacy_raw_complete'] = True
            state['summary_through'] = sum(r[0] <= through for r in rows)
        state['_last_message_id'] = max((r[0] for r in rows), default=0)
        # Existing event interpretations and queued jobs are NOT imported as trustworthy new-core events.
        state['import_note'] = '唯讀快照：舊摘要為可錯背景；不推論舊事件，未匯入 inbox/jobs/token。'
    validate_state(state)
    PyramidCore()._stress(state)
    return state

