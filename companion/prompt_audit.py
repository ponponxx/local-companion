"""Deterministic diagnostics for compiled Writer input; never rewrites replies."""
from collections import defaultdict
import json
import re
from .writer_projection import INTERNAL_KEYS
from .scene_activity import is_travel


def audit_writer(messages):
    try:contract=json.loads(messages[0]['content'].rsplit('\n',1)[-1])
    except (ValueError,IndexError):return {'issues':[{'kind':'invalid_contract'}]}
    issues=[];strings=defaultdict(list)
    def walk(value,path):
        if isinstance(value,dict):
            for key,child in value.items():
                if key in INTERNAL_KEYS:issues.append({'kind':'internal_field','path':path+'.'+key})
                walk(child,path+'.'+key)
        elif isinstance(value,list):
            for i,child in enumerate(value):walk(child,f'{path}[{i}]')
        elif isinstance(value,str) and len(value.strip())>=16:
            strings[value.strip()].append(path)
    walk(contract,'system')
    for paths in strings.values():
        if len(paths)>1:issues.append({'kind':'repeated_structured_text','paths':paths})
    clauses=defaultdict(set)
    for text,paths in strings.items():
        for clause in re.split(r'[。！？!?\n]+',text):
            clause=clause.strip()
            if len(clause)>=16:
                clauses[clause].update(paths)
    for paths in clauses.values():
        if len(paths)>1:issues.append({'kind':'repeated_clause','paths':sorted(paths)})
    facts=contract.get('current_facts',{})
    if is_travel(facts.get('activity','')) and not facts.get('scene',{}).get('movement'):
        issues.append({'kind':'travel_activity_without_transition','path':'current_facts.activity'})
    if contract.get('identity',{}).get('character')==contract.get('identity',{}).get('user'):
        issues.append({'kind':'identity_collision'})
    past=[m['content'] for m in messages[1:-1] if m['role']=='assistant']
    repeats=sum(past.count(text)-1 for text in set(past))
    if repeats:issues.append({'kind':'repeated_assistant_message','count':repeats})
    return {'issues':issues,'structured_duplicate_count':sum(i['kind']=='repeated_structured_text' for i in issues),
            'prior_assistant_repeats':repeats,
            'limitations':'只檢查結構、完全相同的長文字及已知移動不變量；不等於自然語言語義驗證。'}
