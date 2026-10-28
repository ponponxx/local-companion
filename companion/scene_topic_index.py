"""Rebuildable source links, never another world state or model classifier."""
from datetime import datetime
from hashlib import sha256
import re
import unicodedata
from .context_plan import dialogue_groups
from .story_events import digest,claims

MODE='scene-topic-v1'
def enabled(state):return state.get('memory_index_mode')==MODE

def stamp(value):
 try:return datetime.fromisoformat(value).timestamp()
 except (ValueError,TypeError):return None

def key(value):return re.sub(r'[\s，。！？、：:;；]+','',unicodedata.normalize('NFKC',value).casefold())

def location(scene):return scene.get('character_location') or scene.get('location') or ''

def build(state):
 history=state.get('history',[]);visits=[]
 for event in state.get('scene_history',[]):
  if event.get('actor','character')!='character' or event.get('phase') not in {'initialized','arrived','moved'}:continue
  after=location(event.get('after',{}));before=location(event.get('before',{}))
  if not event.get('id') or not after or (event['phase']!='initialized' and before==after):continue
  if stamp(event.get('at')) is None:continue
  visits.append({'id':event['id'],'location':after,'at':event['at'],'until':'','sources':[]})
 visits.sort(key=lambda v:stamp(v['at']))
 for a,b in zip(visits,visits[1:]):a['until']=b['at']
 for v in visits:
  closed=[e['at'] for e in state.get('scene_history',[]) if e.get('phase')=='closed' and e.get('actor','character')=='character' and stamp(e.get('at')) is not None and stamp(e['at'])>=stamp(v['at']) and (not v['until'] or stamp(e['at'])<stamp(v['until']))]
  if closed:v['until']=min(closed,key=stamp)
 links={};visit_map={v['id']:v for v in visits}
 for index,row in enumerate(history):
  ctx=row.get('context',{});scene=ctx.get('scene',{});loc=location(scene);at=stamp(row.get('at'))
  latest=next((v for v in reversed(visits) if at is not None and stamp(v['at'])<=at),None)
  visit=latest if latest and latest['location']==loc and (not latest['until'] or at<stamp(latest['until'])) else None
  # A movement utterance can be timestamped at its transition while its pre-state
  # still belongs to the previous visit. scene_after is explicit confirmation.
  if not visit and latest and at==stamp(latest['at']) and location(ctx.get('scene_after',{}))==latest['location']:
   previous=next((v for v in reversed(visits) if stamp(v['at'])<at and v['location']==loc and v['until']==latest['at']),None)
   visit=previous
  # The source snapshot is required. A latest state alone cannot place old speech.
  record={'source_index':index,'source_digest':digest(row),'visit':visit['id'] if visit else '',
          'character_location':loc,'user_location':scene.get('user_location',''),'at':row.get('at',''),'thread':'',
          'reason':'confirmed_visit' if visit else 'unresolved_visit'}
  if visit:visit['sources'].append(index)
  after=ctx.get('scene_after',{})
  if after and location(after)!=loc:record['character_location_after']=location(after)
  links[index]=record
 threads=[];last=None
 for group in dialogue_groups(history):
  first=group[0];meta=first.get('_story',{})
  topic=meta.get('topic','') if meta.get('digest')==digest(first) else ''
  if key(topic) in {'','當前內容','當前具體主題','none','unknown','聊天','工作','生活'}:last=None;continue
  actors={c['actor'] for c in claims(first) if c['actor']!='unknown'}
  subject=next(iter(actors)) if len(actors)==1 else 'mixed' if actors else 'unresolved'
  norm=key(topic);indices=[r['_index'] for r in group]
  thread=None;reason='new_topic'
  # This joins a discussion, not assertions into one alleged real-world event.
  if last and last['topic_key']==norm and last['subject']==subject:
   thread=last;reason='adjacent_explicit_topic'
  elif re.search(r'剛才|上次|那件事|接著|繼續|後來',first['content']):
   candidates=[t for t in threads if t['topic_key']==norm and t['subject']==subject]
   if len(candidates)==1:thread=candidates[0];reason='unique_explicit_reference'
   elif len(candidates)>1:reason='ambiguous_reference_kept_separate'
  if thread is None:
   identity=sha256((str(first.get('turn_id') or first['_index'])+digest(first)+norm+subject).encode()).hexdigest()[:20]
   thread={'id':identity,'topic':topic,'topic_key':norm,'subject':subject,'sources':[],'visits':[]}
   threads.append(thread)
  for index in indices:
   links[index].update(thread=thread['id'],thread_reason=reason)
   thread['sources'].append(index)
   if links[index]['visit'] and links[index]['visit'] not in thread['visits']:thread['visits'].append(links[index]['visit'])
  last=thread
 return {'version':MODE,'visits':visits,'threads':threads,'messages':links}

def query_scene(index,query,state):
 # Only explicit location names constrain the transcript. Bare "that time" is unresolved.
 names={v['location'] for v in index['visits']}|{m['character_location'] for m in index['messages'].values() if m['character_location']}
 matched={name for name in names if len(name)>1 and name in query}
 if not matched:return {'explicit':False,'visits':[],'sources':[],'ambiguous':False}
 visits=[v for v in index['visits'] if v['location'] in matched]
 if re.search(r'上次|上一回|上一次',query):
  current=location(state.get('scene',{}));active=index['visits'][-1]['id'] if index['visits'] and current==index['visits'][-1]['location'] else ''
  visits=[v for v in visits if v['id']!=active]
  visits=visits[-1:]
 # Legacy unassigned snapshots may match a scene but remain explicitly unassigned.
 sources=[i for i,m in index['messages'].items() if m['visit'] in {v['id'] for v in visits}]
 if not re.search(r'上次|上一回|上一次',query):sources+= [i for i,m in index['messages'].items() if not m['visit'] and m['character_location'] in matched]
 sources=sorted({r['_index'] for g in dialogue_groups(state['history']) if any(r['_index'] in sources for r in g) for r in g})
 return {'explicit':True,'visits':[v['id'] for v in visits],'sources':sorted(set(sources)),
         'ambiguous':len(visits)>1,'locations':sorted(matched)}
