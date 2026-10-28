"""Authored present-tense startup sample, never a reconstructed past event."""
from copy import deepcopy
from .visual_state import clothing_baseline

DEFAULT_CLOTHING={
    'outerwear':{'description':'未穿著','worn':False},
    'top':{'description':'素色棉質上衣','worn':True},
    'bottom':{'description':'舒適長褲','worn':True},
    'dress':{'description':'未穿著','worn':False},
    'underwear':{'description':'日常內著','worn':True},
    'socks':{'description':'棉襪','worn':True},
    'shoes':{'description':'室內拖鞋','worn':True},
    'accessories':{'description':'未穿著','worn':False},
}


def default_clothing(text=''):
    clothes=deepcopy(DEFAULT_CLOTHING)
    configured,_=clothing_baseline(text,allow_layers=True)
    for slot,value in configured.items():clothes[slot]={'description':value,'worn':value!='未穿著'}
    if clothes['dress']['worn']:
        for slot in ('top','bottom'):
            if slot not in configured:clothes[slot]={'description':'未穿著','worn':False}
    return clothes


def initialize_character(state):
    """Only an absent formal location gets the sample; existing facts win.

    Applies after reset/import, and once to legacy uninitialized states. Does
    not move the user, claim sleep occurred, or erase confirmed clothing edits.
    """
    scene=state['scene']
    location=scene.get('character_location') or scene.get('location','')
    if location not in {'','未確認','unknown'} or scene.get('pending') or scene.get('status') not in {'none',None}:
        return False
    from .memory_metadata import scene_snapshot,record_scene
    before=scene_snapshot(state)
    location='青葉莊園臥室' if state.get('scene_background_world')=='original_world' else '住處房間'
    scene.update(location=location,character_location=location,status='active',pending=None,positions={})
    state.update(activity='休息',availability='free')
    appearance=state['appearance'];defaults=state.get('appearance_defaults',{})
    clothes=default_clothing(appearance.get('legacy_outfit') or defaults.get('clothing',''))
    clothes.update(deepcopy(appearance.get('clothing',{})))
    for item in clothes.values():
        if isinstance(item,dict):item.setdefault('source','startup_sample')
    appearance.update(clothing=clothes,legacy_outfit='',wardrobe_version=2)
    for field,fallback in {'hair':'自然整理的髮型','makeup':'無妝','jewelry':'無飾品','accessories':'無配件'}.items():
        if not appearance.get(field):appearance[field]=defaults.get(field) or fallback
    state['character_baseline']={'at':state['now'],'source':'startup_sample','location':location,'past_events_inferred':False}
    record_scene(state,'initialized',before,'startup_sample')
    return True
