"""Authored arrival wardrobe. No model call and no implicit user movement.

Catalog location/activity profiles and registered environments select authored
clothing. Same-turn explicit changes win; unregistered places preserve facts.
"""
from copy import deepcopy
import re
from .scene_background import resolve_background

import json
from pathlib import Path
WARDROBES=json.loads((Path(__file__).parent/'data/scene_wardrobes.json').read_text())['worlds']


def restyle_arrival(state, item=None, protected=()):
    """Call only after confirmed arrival/activity start; return provenance.

    Sleep, wash and training use complete ensembles; ordinary concrete daily
    overrides remain supported. Confirmed changes take priority over ensembles.
    """
    item=item or {}; appearance=state['appearance']; old=deepcopy(appearance)
    protected=set(protected)
    location=state['scene'].get('character_location') or state['scene'].get('location','')
    wardrobe,source=arrival_clothing(state,item)
    if not wardrobe:
        return {'applied':False,'reason':'目的地尚無明確穿搭；保留現況','location':location}
    from .character_baseline import DEFAULT_CLOTHING
    supplied=set(wardrobe)
    wardrobe={**deepcopy(DEFAULT_CLOTHING),**wardrobe}
    if wardrobe.get('dress',{}).get('worn'):
        for slot in ('top','bottom'):
            if slot not in supplied:wardrobe[slot]={'description':'未穿著','worn':False}
    appearance.update(clothing=wardrobe,legacy_outfit='',wardrobe_version=2)
    defaults=state.get('appearance_defaults',{})
    for field in ('hair','makeup','jewelry','accessories'):
        if field in protected:continue
        value=item.get(field,defaults.get(field))
        if value is not None:appearance[field]=value
    # Explicit confirmed clothing changes in this same turn outrank arrival.
    for field in protected:
        if field in old.get('clothing',{}):appearance['clothing'][field]=deepcopy(old['clothing'][field])
        elif field in old:appearance[field]=deepcopy(old[field])
    state['appearance_transition']={'location':location,'at':state.get('now',''),'source':source,'protected':sorted(protected)}
    return {'applied':True,**state['appearance_transition']}


def arrival_clothing(state, item=None):
    """Destination preset plus concrete schedule overrides, never preferences."""
    from .visual_state import clothing_baseline
    item=item or {}
    location=state['scene'].get('character_location') or state['scene'].get('location','')
    world=state.get('scene_background_world','original_world')
    place=resolve_background(location,world)
    catalog=state.get('scene_wardrobe',WARDROBES.get(world,{}))
    look=catalog.get('places',{}).get(place.get('place_id') or location)
    if not look:
        environment=state.get('scene_wardrobe_locations',{}).get(location)
        look=catalog.get('environment_defaults',{}).get(environment)
    activity=item.get('arrival_activity') or item.get('activity') or state.get('activity','')
    uniform=False
    if item.get('availability',state.get('availability'))=='sleep' and 'sleep' in catalog.get('looks',{}):
        look='sleep';uniform=True
    elif place.get('place_id')=='manor_bathroom' and re.search(r'盥洗|洗臉|刷牙|冷水|晨間整理|沐浴|洗澡',activity) and 'wash' in catalog.get('looks',{}):
        look='wash';uniform=True
    elif (look=='practice' or re.search(r'飛行競技訓練|決鬥練習|飛行訓練|體能訓練',activity)) and 'practice' in catalog.get('looks',{}):
        look='practice';uniform=True
    baseline=catalog.get('looks',{}).get(look,{})
    clothes={k:{'description':v,'worn':v!='未穿著','source':'arrival_wardrobe'} for k,v in baseline.items()}
    concrete,_=clothing_baseline(item.get('clothing',''),allow_layers=True)
    # Authored sleep / wash / training ensembles replace incompatible legacy
    # daily prose as a whole. Explicit confirmed events still win in protected.
    if uniform:concrete={}
    # A dress replaces the separate top/bottom baseline, and vice versa.
    if 'dress' in concrete and concrete['dress']!='未穿著':
        for slot in ('top','bottom'):clothes.pop(slot,None)
    elif any(k in concrete and concrete[k]!='未穿著' for k in ('top','bottom')):
        clothes.pop('dress',None)
    for slot,value in concrete.items():
        clothes[slot]={'description':value,'worn':value!='未穿著','source':'daily_schedule'}
    return clothes, 'daily_schedule' if concrete else 'place_preset:'+str(look)


def migrate_arrival_clothing(state):
    """Promote an old confirmed arrival's concrete baseline; preserve overrides.

    This does not move actors, complete pending travel, or rewrite dialogue.
    """
    appearance=state['appearance']
    if appearance.get('wardrobe_version')==2 or state.get('appearance_transition',{}).get('source')!='daily_schedule':return False
    text=appearance.get('legacy_outfit','')
    if not text:return False
    # Only the location recorded by that confirmed arrival may supply its preset.
    transition=state['appearance_transition']
    location=state['scene'].get('character_location') or state['scene'].get('location','')
    if transition.get('location')!=location:return False
    clothes,_=arrival_clothing(state,{'clothing':text})
    if not clothes:return False
    clothes.update(deepcopy(appearance.get('clothing',{})))
    appearance.update(clothing=clothes,legacy_outfit='',wardrobe_version=2)
    return True
