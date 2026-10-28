"""Concrete appearance projection and local-morning reset, without model calls."""
from copy import deepcopy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def reset_morning_appearance(state, at):
    defaults = state.get('appearance_defaults')
    if not defaults:
        return False
    # Do not redress or groom a sleeping character at a wall-clock boundary.
    if state.get('availability')=='sleep':return False
    local = at.astimezone(ZoneInfo('Asia/Taipei'))
    day = (local - timedelta(hours=6)).date().isoformat()
    previous = state.get('appearance_day')
    if previous is None and state.get('now'):
        previous = (datetime.fromisoformat(state['now']).astimezone(ZoneInfo('Asia/Taipei')) - timedelta(hours=6)).date().isoformat()
    state['appearance_day'] = day
    if previous is None or previous == day:
        return False
    appearance = state['appearance']
    from .character_baseline import default_clothing
    appearance.update(hair=defaults.get('hair', ''), makeup=defaults.get('makeup', ''),
                      accessories=defaults.get('accessories', ''), jewelry=defaults.get('jewelry', '無飾品'))
    # Scene-managed clothing belongs to the current activity, not global defaults.
    if not state.get('life_current'):
        appearance.update(clothing=default_clothing(defaults.get('clothing','')), legacy_outfit='', wardrobe_version=2)
    # A prior pose/expression is a moment, not a morning appearance default.
    state['presentation'] = {}
    state['appearance_reset'] = {'day': day, 'reason': 'local_morning', 'defaults': deepcopy(defaults)}
    return True


# Legacy text is accepted only when a clause names one unambiguous garment.
# Unknown/alternative preference prose is never asserted as current clothing.
SLOT_NAMES = {'outerwear':'外衣', 'top':'上衣', 'bottom':'下裝', 'dress':'連身衣裙',
              'underwear':'內衣', 'socks':'襪子', 'shoes':'鞋子', 'accessories':'衣著配件'}
GARMENTS = {
    'outerwear': r'外套|大衣|長袍|實驗袍|巫師袍|斗篷|夾克|睡袍|浴袍|晨袍|coat|jacket|robe|cloak',
    'top': r'襯衫|背心|上衣|毛衣|針織衫|T恤|shirt|blouse|sweater|vest',
    'bottom': r'長褲|短褲|褲子|百褶裙|短裙|長裙|trousers|pants|shorts|skirt',
    'dress': r'連身裙|洋裝|連衣裙|dress|gown',
    'underwear': r'內衣|內褲|胸罩|underwear|bra|panties',
    'socks': r'襪|絲襪|stockings|socks|tights',
    'shoes': r'靴|鞋|boots|shoes|sneakers|heels',
    'accessories': r'領帶|圍巾|腰帶|手套|tie|scarf|belt|gloves',
}


def clothing_baseline(text, *, allow_layers=False):
    """Conservative, inspectable migration; never choose between alternatives."""
    import re
    result={};unknown=[];ambiguous=set()
    if re.search(r'偏好|常見|依場合|依活動|合理變化|prefer|usually',text or '',re.I):
        return {}, [text]
    for clause in re.split(r'[；;、，,。\n]+',text or ''):
        clause=clause.strip()
        if not clause:continue
        matches=[k for k,pattern in GARMENTS.items() if re.search(pattern,clause,re.I)]
        speculative=bool(re.search(r'偏好|常見|可以|可選|依場合|依活動|合理變化|或|可能|prefer|usually|\bor\b',clause,re.I))
        if len(matches)!=1 or speculative:
            unknown.append(clause)
            # No current assertion from an ambiguous baseline, even if another
            # clause happens to mention the same slot.
            ambiguous.update(matches)
            continue
        slot=matches[0]
        if slot in result:
            if result[slot]!=clause:
                # A concrete schedule may name a shirt plus one knit layer.
                # Two shirts/alternatives remain ambiguous; no arbitrary choice.
                pair=(result[slot],clause)
                shirt=lambda v: bool(re.search(r'襯衫|shirt|blouse',v,re.I))
                knit=lambda v: bool(re.search(r'背心|毛衣|針織衫|sweater|vest',v,re.I))
                if allow_layers and slot=='top' and ((shirt(pair[0]) and knit(pair[1])) or (knit(pair[0]) and shirt(pair[1]))) and '、' not in pair[0]:
                    result[slot]='、'.join(pair)
                else:ambiguous.add(slot);unknown.append(clause)
        else:result[slot]='未穿著' if re.search(r'未穿著|已脫下|沒有穿|沒穿|not wearing',clause,re.I) else clause
    for slot in ambiguous:result.pop(slot,None)
    return result,unknown


def is_removed(value):
    return isinstance(value,str) and value.strip().casefold() in {
        'off','removed','未穿著','未穿','已脫下','沒穿','沒有穿','無','not worn','not wearing'}


def current_appearance(appearance):
    """Single model-facing projection: explicit changes replace the baseline.

    Source state stays intact for provenance. Unknown never means undressed.
    No old outfit paragraph, source ids, or overlapping patches reach the model.
    """
    baseline,_=clothing_baseline(appearance.get('legacy_outfit',''),allow_layers=True)
    clothing={k:baseline.get(k,'未確認') for k in SLOT_NAMES}
    for slot,item in appearance.get('clothing',{}).items():
        if slot not in clothing:continue
        if isinstance(item,dict):
            description=item.get('description','').strip()
            if item.get('worn') is False:
                clothing[slot]='未穿著'
            elif item.get('worn') is True and description:
                clothing[slot]=description
            else:clothing[slot]='未確認'
        elif isinstance(item,str) and item.strip():
            clothing[slot]='未穿著' if is_removed(item) else item
    result={k:deepcopy(appearance[k]) for k in ('body','hair','makeup','jewelry','accessories') if k in appearance}
    result['clothing']=clothing
    # Identical ornament text was sometimes stored in both administrative slots.
    # Do not duplicate the same full value as handheld gear; retain raw data.
    if result.get('jewelry','').strip() and result.get('accessories','').strip()==result['jewelry'].strip():
        result['accessories']='無'
    return result


def clothing_description(appearance):
    clothing=current_appearance(appearance)['clothing']
    parts=[f'{SLOT_NAMES[slot]}：{value}' for slot,value in clothing.items() if value!='未確認']
    return '；'.join(parts) or '衣著尚未確認'
