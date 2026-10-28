"""Evidence-backed conversational world, separate from psychological state.

The model proposes observations; this reducer owns entity IDs, scene lifetime,
provenance, ordering and commit. Old prose is never migrated into facts.
"""
import json

EVENT_SCHEMA = {'type': 'object', 'additionalProperties': False,
    'required': ['transition', 'transition_evidence', 'events'],
    'properties': {
        'transition': {'enum': ['keep', 'open', 'close']},
        'transition_evidence': {'type': 'string'},
        'events': {'type': 'array', 'maxItems': 3, 'items': {
        'type': 'object', 'additionalProperties': False,
        'required': ['operation', 'subject', 'field', 'value', 'evidence'],
        'properties': {
            'operation': {'enum': ['set', 'unset']},
            'subject': {'enum': ['user', 'character', 'scene']},
            'field': {'enum': ['location', 'activity', 'hair', 'clothing', 'accessories',
                               'pose', 'expression', 'camera_holder', 'camera_angle', 'interaction']},
            'value': {'type': 'string'}, 'evidence': {'type': 'string'},
        }}}}}

FIELDS = set(EVENT_SCHEMA['properties']['events']['items']['properties']['field']['enum'])


class SceneState:
    def __init__(self, db):
        self.db = db
        db.executescript('''
            CREATE TABLE IF NOT EXISTS scene_events(
                id INTEGER PRIMARY KEY, message_id INTEGER NOT NULL, ordinal INTEGER NOT NULL,
                payload TEXT NOT NULL, UNIQUE(message_id, ordinal));
            CREATE TABLE IF NOT EXISTS scene_projection(
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), payload TEXT NOT NULL);
        ''')

    def snapshot(self):
        row = self.db.execute('SELECT payload FROM scene_projection WHERE singleton=1').fetchone()
        return json.loads(row[0]) if row else {
            'revision': 0, 'scene_id': 0, 'status': 'none', 'mode': 'remote_chat', 'facts': {},
        }

    def apply(self, message_id, proposals, expected_revision):
        current = self.snapshot()
        if current['revision'] != expected_revision:
            return False
        source = self.db.execute('SELECT role,content FROM messages WHERE id=?', (message_id,)).fetchone()
        if not source or source['role'] != 'user' or not isinstance(proposals, list):
            return False
        latest = self.db.execute('SELECT MAX(message_id) FROM scene_events').fetchone()[0]
        if latest is not None and message_id < latest:
            return False
        for ordinal, event in enumerate(proposals[:4]):
            if not isinstance(event, dict):
                continue
            op, subject, field = (event.get(k) for k in ('operation', 'subject', 'field'))
            value, evidence = event.get('value'), event.get('evidence')
            if (op not in {'open', 'close', 'set', 'unset'} or subject not in {'user', 'character', 'scene'}
                    or field not in FIELDS or not isinstance(value, str) or len(value) > 180
                    or not isinstance(evidence, str) or not evidence.strip() or evidence not in source['content']):
                continue
            if self.db.execute('SELECT 1 FROM scene_events WHERE message_id=? AND ordinal=?',
                               (message_id, ordinal)).fetchone():
                continue
            if op in {'open', 'close'} and subject != 'scene':
                continue
            if field in {'camera_holder', 'camera_angle', 'interaction'} and subject != 'scene':
                continue
            if field in {'hair', 'clothing', 'accessories', 'pose', 'expression'} and subject == 'scene':
                continue
            if field == 'camera_holder' and op == 'set' and value not in {'user', 'character'}:
                continue
            if op == 'set' and not value.strip():
                continue
            if op == 'open':
                current.update(scene_id=current['scene_id'] + 1, status='active',
                               mode='shared_fiction', facts={})
            elif op == 'close':
                current.update(status='closed', mode='remote_chat', facts={})
            elif current['status'] != 'active':
                # Mutable scene facts require an explicit active fictional scene.
                continue
            key = subject + '.' + field
            if op in {'open', 'set'}:
                current['facts'][key] = {'value': value, 'source_message_id': message_id,
                    'speaker': 'user', 'evidence': evidence, 'kind': 'user_established_fiction'}
            elif op == 'unset':
                current['facts'].pop(key, None)
            current['revision'] += 1
            self.db.execute('INSERT INTO scene_events(message_id,ordinal,payload) VALUES(?,?,?)',
                            (message_id, ordinal, json.dumps(event, ensure_ascii=False)))
        self.db.execute('INSERT INTO scene_projection VALUES(1,?) ON CONFLICT(singleton) '
                        'DO UPDATE SET payload=excluded.payload', (json.dumps(current, ensure_ascii=False),))
        return True

    def writer_view(self):
        state = self.snapshot()
        return {**{k: state[k] for k in ('revision', 'scene_id', 'status', 'mode')},
                'facts': {k: v['value'] for k, v in state['facts'].items()},
                'authority': 'User-established fictional facts, not real-world events. '
                             'Unlisted actor actions and locations are unknown. '
                             'Character dialogue and summaries do not establish completed events.'}

    def clear(self):
        self.db.execute('DELETE FROM scene_events')
        self.db.execute('DELETE FROM scene_projection')
