"""Neutral environment catalog used by daily planning and image backgrounds.

Exact names/aliases only. A planned destination never replaces current location.
"""
from pathlib import Path
import json
import unicodedata

CATALOG_PATH = Path(__file__).with_name('data') / 'scene_backgrounds.json'


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def load_catalog(path=CATALOG_PATH):
    data = json.loads(Path(path).read_text())
    if data.get('version') != 1:
        raise ValueError('不支援的地點範本版本')
    for world in data['worlds'].values():
        seen_ids, seen_names = set(), set()
        for place in world['places']:
            if place['id'] in seen_ids:
                raise ValueError('地點代號重複')
            seen_ids.add(place['id'])
            if place['environment'] not in {'indoor', 'outdoor', 'air', 'travel'} or not place['background'].strip():
                raise ValueError('背景範本無效')
            for name in [place['name'], *place['aliases']]:
                key = normalized(name)
                if not key or key in seen_names:
                    raise ValueError('地點名稱或別名衝突')
                seen_names.add(key)
    return data


def location_options(world_id, catalog=None):
    world = (catalog or load_catalog())['worlds'].get(world_id)
    return [{'id': p['id'], 'name': p['name']} for p in world['places']] if world else []


def resolve_background(location, world_id, catalog=None):
    """No language model, fuzzy match, persona, dialogue or image content input."""
    world = (catalog or load_catalog())['worlds'].get(world_id)
    result = {'location': location or '', 'place_id': None, 'environment': 'unknown',
              'matched': False, 'background': location.strip() if location else '地點未確認'}
    if not world or not location:
        return result
    key = normalized(location)
    for place in world['places']:
        if key == normalized(place['id']) or key in {normalized(n) for n in [place['name'], *place['aliases']]}:
            result.update(place_id=place['id'], location=place['name'], environment=place['environment'],
                          matched=True, background=place['background'])
            if world.get('style'):
                result['background'] += '；' + world['style']
            break
    return result


def canonical_location(state, value):
    """Registry names/aliases, or a unique shortened name of a known context.

    Unknown text is not a new registered character scene. User self-reported
    locations may still be outside the character's fictional world.
    """
    if not isinstance(value,str) or not value.strip():return None
    value=value.strip();world=state.get('scene_background_world')
    if not world:return value  # Unconfigured sandbox/legacy contract.
    exact=resolve_background(value,world)
    if exact['matched']:return exact['location']
    dynamic=state.get('scene_wardrobe_locations',{})
    for name in dynamic:
        if normalized(name)==normalized(value):return name
    scene=state['scene']
    contexts=[scene.get('character_location') or scene.get('location'),scene.get('user_location')]
    contexts += [trip.get('destination') for trip in scene.get('journeys',{}).values()]
    candidates=set();key=normalized(value).replace('卧','臥')
    for name in contexts:
        if not name:continue
        place=resolve_background(name,world)
        full=place['location'] if place['matched'] else name if name in dynamic else None
        if full and len(key)>=2 and normalized(full).replace('卧','臥').endswith(key):candidates.add(full)
    if candidates:return next(iter(candidates)) if len(candidates)==1 else None
    # A unique exact suffix within the registered world is a short name, not
    # fuzzy spelling correction. Multiple bedrooms require explicit context.
    names=[p['name'] for p in location_options(world)]+list(dynamic)
    matches={name for name in names if len(key)>=2 and normalized(name).replace('卧','臥').endswith(key)}
    return next(iter(matches)) if len(matches)==1 else None


def same_location(state, left, right):
    if not left or not right:return False
    if normalized(left)==normalized(right):return True
    a,b=canonical_location(state,left),canonical_location(state,right)
    return bool(a and b and a==b)
