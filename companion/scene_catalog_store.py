"""Persistent, character-scoped neutral locations learned from daily plans.

This stores environment definitions, never arrival facts or image requests.
"""
import hashlib
import json
from .scene_background import load_catalog, normalized, location_options, resolve_background

NEW_LOCATION_SCHEMA = {'type': 'object', 'additionalProperties': False,
    'required': ['name', 'environment', 'background'], 'properties': {
        'name': {'type': 'string', 'minLength': 1, 'maxLength': 120},
        'environment': {'enum': ['indoor', 'outdoor', 'air', 'travel']},
        'background': {'type': 'string', 'minLength': 12, 'maxLength': 500}}}


class SceneCatalogStore:
    @classmethod
    def from_state(cls, store, state):
        return cls(store.db, store.get('scene_background_world', 'original_world'),
                   json.dumps([state.get('story', {}).get('name', ''), state.get('visual_world', '')], ensure_ascii=False))

    def __init__(self, db, world_id, character_key):
        self.db, self.world_id = db, world_id
        self.scope = world_id + ':' + hashlib.sha256(character_key.encode()).hexdigest()[:24]
        self.catalog = load_catalog()
        with db:
            db.execute('''CREATE TABLE IF NOT EXISTS scene_background_catalog(
                scope TEXT NOT NULL, location_key TEXT NOT NULL, name TEXT NOT NULL,
                environment TEXT NOT NULL, background TEXT NOT NULL, created_at TEXT NOT NULL,
                origin TEXT NOT NULL, PRIMARY KEY(scope, location_key))''')

    def resolve(self, location):
        row = self.db.execute('SELECT name,environment,background,origin FROM scene_background_catalog WHERE scope=? AND location_key=?',
                              (self.scope, normalized(location))).fetchone()
        if row:
            return {'matched': True, 'location': row[0], 'environment': row[1], 'background': row[2], 'origin': row[3],
                    'semantically_verified': False}
        return resolve_background(location, self.world_id, self.catalog)

    def options(self):
        # Names only: the model need not reread every cached paragraph.
        names = [p['name'] for p in location_options(self.world_id, self.catalog)]
        names += [r[0] for r in self.db.execute('SELECT name FROM scene_background_catalog WHERE scope=? ORDER BY created_at DESC,name LIMIT 50', (self.scope,))]
        return names

    def wardrobe_locations(self):
        """Persisted environment selects an authored fallback, never prose parsing."""
        return {r[0]:r[1] for r in self.db.execute(
            'SELECT name,environment FROM scene_background_catalog WHERE scope=?',(self.scope,))}

    def register_plan(self, items, new_locations, stamp):
        if not isinstance(new_locations, list) or len(new_locations) > 9:
            raise ValueError('新地點資料格式或數量無效')
        unknown = {normalized(i['location']): i['location'] for i in items if not self.resolve(i['location'])['matched']}
        staged = {}
        for place in new_locations:
            if not isinstance(place, dict) or set(place) != {'name', 'environment', 'background'}:
                raise ValueError('新地點必須包含名稱、環境類型與背景細節')
            name, environment, background = (place[k] for k in ('name', 'environment', 'background'))
            if not all(isinstance(v, str) for v in (name, environment, background)):
                raise ValueError('地點欄位必須為文字')
            if not name.strip() or len(name) > 120 or environment not in {'indoor', 'outdoor', 'air', 'travel'} or not 12 <= len(background.strip()) <= 500:
                raise ValueError('地點環境或細節不完整')
            key = normalized(name)
            # A model may repeat a known definition; do not overwrite it.
            if self.resolve(name)['matched']:
                continue
            if key not in unknown:
                raise ValueError('新地點不在本次有效日程')
            value = (unknown[key], environment, background.strip())
            if key in staged and staged[key] != value:
                raise ValueError('同一新地點出現矛盾定義')
            staged[key] = value
        if set(unknown) != set(staged):
            raise ValueError('日程含未知地點，但缺少對應背景細節')
        # Validate the entire batch first, then commit together, no awaits.
        with self.db:
            for key, (name, environment, background) in staged.items():
                self.db.execute('INSERT INTO scene_background_catalog VALUES(?,?,?,?,?,?,?)',
                                (self.scope, key, name, environment, background, stamp, 'daily_plan_unverified'))
        return len(staged)
