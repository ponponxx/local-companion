"""Durable response annotation jobs; foreground messages never depend on them."""
import json
from copy import deepcopy
from datetime import datetime,timedelta

class MemoryTasks:
 def __init__(self,db):
  self.db=db
  with db:db.execute('''CREATE TABLE IF NOT EXISTS memory_tasks(task_id TEXT PRIMARY KEY,source_id INTEGER,source_at TEXT NOT NULL,status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,retry_at TEXT,error TEXT,payload TEXT NOT NULL)''')
 def sync(self,pending):
  with self.db:
   for p in pending:
    if not p.get('turn_id'):continue
    attempts=int(p.get('background_attempts',0));status='stalled' if attempts>=3 else 'pending'
    self.db.execute('INSERT OR IGNORE INTO memory_tasks VALUES(?,?,?,?,?,?,?,?)',(p['turn_id'],p.get('message_id'),p.get('at',''),status,attempts,p.get('retry_at'),'',json.dumps(p,ensure_ascii=False)))
 def eligible(self,pending,now):
  self.sync(pending)
  for p in pending:
   if not p.get('turn_id'):continue
   row=self.db.execute('SELECT status,attempts,retry_at FROM memory_tasks WHERE task_id=?',(p['turn_id'],)).fetchone()
   if row[0] in {'done','annotation_only','stalled'} or row[1]>=3:continue
   if row[2] and datetime.fromisoformat(row[2])>now:continue
   return p
 def finish(self,p,status):
  with self.db:self.db.execute('UPDATE memory_tasks SET status=?,error=? WHERE task_id=?',(status,'',p['turn_id']))
 def fail(self,p,now,message):
  self.sync([p]);row=self.db.execute('SELECT attempts FROM memory_tasks WHERE task_id=?',(p['turn_id'],)).fetchone();attempts=row[0]+1
  with self.db:self.db.execute('UPDATE memory_tasks SET status=?,attempts=?,retry_at=?,error=? WHERE task_id=?',('stalled' if attempts>=3 else 'pending',attempts,(now+timedelta(minutes=2)).isoformat(),message,p['turn_id']))
  return attempts
 def overview(self):return {r[0]:r[1] for r in self.db.execute('SELECT status,count(*) FROM memory_tasks GROUP BY status')}

# Baseline fields deliberately omit transcript, prompts, diagnostic archive and model output.
def baseline(state):
 return deepcopy({k:state.get(k) for k in ['scene','appearance','user_appearance','presentation','activity','availability','invitations','activities','issues','promises','loads','adult_policy','_clothing_edits','relationship','emotions','energy','stress','intimacy']})

def rebase(observation,pending,state,trace):
 """Late extraction cannot overwrite intervening domain updates or replay old affect."""
 result=deepcopy(observation);base=pending.get('baseline');accepted=[];rejected=[]
 delayed=not base or state.get('_last_message_id')!=pending.get('message_id') or baseline(state)!=base
 for e in result['events']:
  kind=e.get('kind');allowed=True
  if delayed:
   if not base:allowed=False
   elif kind=='move':allowed=all(state.get(k)==base.get(k) for k in ['scene','activity'])
   elif kind=='appearance':
    owner='appearance' if e.get('actor')=='character' else 'user_appearance';slot=e.get('field')
    if slot in ('hair','makeup','jewelry','accessories'):old=(base.get(owner) or {}).get(slot);new=state.get(owner,{}).get(slot)
    else:old=(base.get(owner) or {}).get('clothing',{}).get(slot);new=state.get(owner,{}).get('clothing',{}).get(slot)
    allowed=old==new and base.get('scene')==state.get('scene') and base.get('_clothing_edits')==state.get('_clothing_edits')
   elif e.get('ref'):
    ref=e['ref'];bucket=next((k for k in ['invitations','activities','issues','promises','loads'] if ref in (base.get(k) or {})),None)
    allowed=bool(bucket and (base.get(bucket) or {}).get(ref)==state.get(bucket,{}).get(ref))
   else:allowed=False
  if allowed:accepted.append(e)
  else:rejected.append({'kind':kind,'field':e.get('field'),'reason':'舊來源沒有基準，或較新的狀態已改變；僅補歷史註記'})
 result['events']=accepted
 if delayed:result['appraisals']=[]
 result['_late_annotation']=delayed
 if trace:trace.add(5,'memory.response_rebase',{}, {'delayed':delayed,'accepted':len(accepted),'historical_only':rejected},'以原始來源時間及基準比對；不覆寫較新現況，不重播過期情緒／關係評估。')
 return result
