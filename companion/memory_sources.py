"""Stable source identity and reversible recall exclusions. Never a world-state owner."""
from copy import deepcopy
import hashlib,json,re

def fingerprint(row):
 return hashlib.sha256(json.dumps([row.get('role'),row.get('content')],ensure_ascii=False).encode()).hexdigest()

def source_ref(row,index=0):
 return row.get('source_ref') or ('message:'+str(row['message_id']) if row.get('message_id') is not None else 'legacy:'+hashlib.sha256(json.dumps([row.get('role'),row.get('content'),row.get('at'),index],ensure_ascii=False).encode()).hexdigest()[:24])

def bind(state):
 for i,row in enumerate(state.get('history',[])):
  row.setdefault('source_ref',source_ref(row,i))
 return state

def normalized(text):return re.sub(r'\s+','',str(text)).casefold()

def excluded(state,row,index):
 if row.get('_memory_hidden'):return True
 for ban in state.get('memory_exclusions',[]):
  ref=source_ref(row,index)
  if ref in ban.get('sources',[]):return True
  before=row.get('message_id',0)<=ban.get('before_message_id',0) if row.get('message_id') else index<=ban.get('before_index',-1)
  if before and ban.get('text') and normalized(ban['text']) in normalized(row.get('content','')):return True
 return False

def readable(state):
 if not state.get('memory_exclusions') and not any(h.get('_memory_hidden') for h in state.get('history',[])):return state
 view=deepcopy(state);hidden=set()
 for i,row in enumerate(view.get('history',[])):
  if excluded(state,row,i):
   hidden.add(i);row.update(content='',_memory_hidden=True);row.pop('_story',None);row['context']={}
 hidden_turns={(state['history'][i].get('turn_id'),'user' if state['history'][i]['role']=='user' else 'assistant') for i in hidden}
 derived_profile_sources={e['id'] for e in state.get('events',[]) if e.get('id') and (e.get('turn_id'),e.get('source')) in hidden_turns}
 view['user_profile']=[f for f in view.get('user_profile',[]) if f.get('source') not in derived_profile_sources]
 mem=view.get('memory',{})
 mem['notes']=[n for n in mem.get('notes',[]) if n.get('source_index') not in hidden and not any(normalized(b['text']) in normalized(n.get('text','')) for b in state.get('memory_exclusions',[]) if b.get('text') and n.get('source_index',-1)<=b.get('before_index',-1))]
 # Unattributed legacy summaries cannot safely resurrect an excluded statement.
 for key in ['summary','legacy_summary']:view[key]=''
 mem['previous_summary']=''
 return view

def stop_recalling(store,memory_id):
 row=store.db.execute('SELECT content FROM memories WHERE id=?',(memory_id,)).fetchone()
 if row is None:return False
 with store.db:
  raw=store.get('pyramid_state');state=json.loads(raw) if raw else None
  if state is None:
   from .pyramid_import import import_legacy_state
   state=import_legacy_state(store.db_path, __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),full_history=True)
  bind(state);text=row[0];history=state.get('history',[])
  cutoff=store.db.execute('SELECT COALESCE(max(id),0) FROM messages').fetchone()[0]
  matched=[i for i,h in enumerate(history) if normalized(text) in normalized(h.get('content',''))]
  turns={history[i]['turn_id'] for i in matched if history[i].get('turn_id')}
  # A reply derived from the forgotten utterance must not reintroduce it,
  # including through an unfinished response-analysis task. No adjacent turn
  # is inferred when old data lacks a recorded turn identity.
  sources=[source_ref(h,i) for i,h in enumerate(history) if i in matched or h.get('turn_id') in turns]
  ban={'manual_ref':'manual:'+str(memory_id),'text':text,'sources':sources,'before_message_id':cutoff,'before_index':len(history)-1}
  state.setdefault('memory_exclusions',[]).append(ban)
  state['manual_memories']=[m for m in state.get('manual_memories',[]) if str(m['id'])!=str(memory_id)]
  store.db.execute('DELETE FROM memories WHERE id=?',(memory_id,))
  store.db.execute("INSERT INTO settings VALUES('pyramid_state',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(state,ensure_ascii=False),))
  # Cached prompt displays are invalidated, never the conversation itself.
  store.db.execute("DELETE FROM settings WHERE key IN ('last_prompt','last_text_prompt','gpt_review_candidate')")
 return True
