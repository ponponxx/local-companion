"""Source-bounded photo staging, separate from persistent world facts.

Finite object/constraint vocabulary, like existing framing controls. This is not
an unrestricted parser, nor a claim that a prop was really picked up or used.
"""
import re

OBJECTS={
    'teacup':{'name':'茶杯','pattern':r'茶|\btea\b|teacup','activity':r'泡茶|喝茶|茶會|\btea time\b|\bbrewing tea\b',
              'held':'角色一隻手持一只茶杯，杯中內容不指定','nearby':'角色身旁放著一只茶杯，杯中內容不指定'},
    'book':{'name':'書本','pattern':r'書|閱讀|\bbook\b|\breading\b','activity':r'閱讀|看書|\breading\b',
            'held':'角色手持一本書','nearby':'角色身旁放著一本書'},
    'flower':{'name':'花朵','pattern':r'花束|摘花|一朵花|\bflower\b|\bbouquet\b','activity':r'摘花|\bpicking flowers\b',
              'held':'角色手持一朵花','nearby':'角色身旁放著一朵花'},
}
SCHEMA={'type':'array','maxItems':2,'items':{'type':'object','additionalProperties':False,
    'required':['object','placement','source'], 'properties':{'object':{'enum':list(OBJECTS)},
    'placement':{'enum':['held','nearby']},'source':{'type':'integer','minimum':0,'maximum':6}}}}
RULES=('photo_objects是本次拍照擺設，不是已完成的故事動作。只能從photo_object_options挑有來源的道具，最多兩種；'
       'photo_object_defaults是場景提供的擺設預設，可選合理擺放位置。茶杯不指定杯中內容，不描述已泡好。'
       'source填來源號碼：0是本次要求，1起對應recent_dialogue順序。'
       '道具只寫在photo_objects，pose只寫身體姿勢並與拿物相容，不在pose重複寫物品。')


def clauses(text):return re.split(r'[，,。；;！!？?\n]',text)


def blocked(request):
    """Explicit literal photo constraints, not inferred emotional intent."""
    if re.search(r'不要(?:任何)?道具|不用道具|雙手空著|不拿任何東西|no props|empty hands',request,re.I):return set(OBJECTS)
    result=set()
    for key,info in OBJECTS.items():
        for clause in clauses(request):
            if not re.search(info['pattern'],clause,re.I):continue
            if re.search(r'不要|別拿|不准|不可以|不能|do not|don.t|without|no ',clause,re.I):result.add(key)
        # A unique item explicitly still held by the user cannot be staged in
        # the character's hand. Unknown ownership is not inferred.
        if (re.search(info['pattern'],request,re.I) and re.search(r'唯一|only (?:one|cup|book|flower)',request,re.I)
                and re.search(r'在我手上|我拿著|我手中|in my hand',request,re.I)):
            result.add(key)
    return result


def sources(request,dialogue):
    return [{'speaker':'user','text':request}]+[{'speaker':r['role'],'text':r['content']} for r in dialogue[-6:]]


def options(request,dialogue):
    denied=blocked(request);result=[]
    for i,row in enumerate(sources(request,dialogue)):
        objects=[]
        for key,info in OBJECTS.items():
            positive=[c for c in clauses(row['text']) if re.search(info['pattern'],c,re.I)
                      and not re.search(r'不要|不想|沒有|還沒|別拿|不准|don.t|do not|without',c,re.I)]
            if key not in denied and positive:objects.append(key)
        if objects:result.append({'source':i,'speaker':row['speaker'],'objects':objects})
    return result


def defaults(request,dialogue):
    """Authored rendering defaults from a fresh, explicit activity mention.

    Not a persistent fact or a model result. A tea plan permits an empty prop,
    not a completed cup of tea. Explicit shot constraints take priority.
    """
    available={row['source']:row['objects'] for row in options(request,dialogue)}
    if available.get(0) and re.search(r'拿|捧|握|持|放|hold|with',request,re.I):return []
    for i,row in reversed(list(enumerate(sources(request,dialogue)))):
        if row['speaker']!='user':continue
        for key in available.get(i,[]):
            if any(re.search(OBJECTS[key]['activity'],c,re.I) and not re.search(r'不|沒|別|don.t|do not',c,re.I) for c in clauses(row['text'])):
                return [{'object':key,'placement':'held','source':i}]
    return []


def render(plan,request,dialogue):
    choices={row['source']:row['objects'] for row in options(request,dialogue)};result=[];seen=set()
    rows=plan.get('photo_objects',[])
    if not isinstance(rows,list):rows=[]
    for row in rows[:2]+defaults(request,dialogue):
        if not isinstance(row,dict):continue
        key=row.get('object');placement=row.get('placement');source=row.get('source')
        if type(source) is not int or key not in choices.get(source,[]) or placement not in {'held','nearby'} or key in seen:continue
        seen.add(key);result.append(OBJECTS[key][placement])
        if len(result)>=2:break
    return '；'.join(result)


POSTURES={'standing':'站立','seated':'坐著','kneeling':'跪坐','leaning':'倚靠著','lying':'躺著','walking':'行走中','unknown':''}
LEANS={'forward':'上半身微微前傾','back':'上半身微微後仰','none':'','unknown':''}
HANDS={'sides':'雙手自然放在身側','folded':'雙手交疊','hips':'雙手置於腰側','resting':'雙手自然放鬆','gesturing':'以手勢示意','unknown':''}


def pose(plan,objects):
    # No free model prose enters the posture channel. Props own holding actions.
    parts=[POSTURES.get(plan.get('posture'),''),LEANS.get(plan.get('body_lean'),'')]
    if not objects:parts.append(HANDS.get(plan.get('hand_pose'),''))
    return '，'.join(part for part in parts if part)
