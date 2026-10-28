"""Local append-only diagnostic archive. Never a memory/retrieval source."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime,timezone
from functools import wraps
import inspect,json,logging,os,sqlite3,time,traceback
from pathlib import Path

ACTIVE=ContextVar('companion_diagnostic_run',default=None)
STAGE=ContextVar('companion_diagnostic_stage',default='其他模型呼叫（用途見Prompt）')

def stamp():return datetime.now(timezone.utc).isoformat()
def encode(value):return json.dumps(value,ensure_ascii=False,default=str,separators=(',',':'))

def event(kind,data):
 active=ACTIVE.get()
 if active and not SUPPRESS.get():active[0].event(active[1],kind,data)

def link(kind,value):
 active=ACTIVE.get()
 if active and value is not None:active[0].link(active[1],kind,str(value))

class Diagnostics:
 def __init__(self,path):
  self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
  self.db=sqlite3.connect(self.path,timeout=.2);self.db.row_factory=sqlite3.Row
  self.path.chmod(0o600);self.errors=0
  self.db.executescript('''PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY,kind TEXT NOT NULL,started_at TEXT NOT NULL,ended_at TEXT,status TEXT NOT NULL,elapsed_seconds REAL,parent_id INTEGER,metadata TEXT NOT NULL,external_key TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,run_id INTEGER,at TEXT NOT NULL,kind TEXT NOT NULL,data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS events_run ON events(run_id,id);
CREATE TABLE IF NOT EXISTS model_calls(id INTEGER PRIMARY KEY,run_id INTEGER,stage TEXT NOT NULL,started_at TEXT NOT NULL,ended_at TEXT,status TEXT NOT NULL,requested TEXT NOT NULL,prepared TEXT,output TEXT,metrics TEXT,error TEXT);
CREATE INDEX IF NOT EXISTS calls_run ON model_calls(run_id,id);
CREATE TABLE IF NOT EXISTS links(run_id INTEGER,kind TEXT NOT NULL,value TEXT NOT NULL,UNIQUE(run_id,kind,value));
CREATE INDEX IF NOT EXISTS link_value ON links(kind,value);''')
  # WAL sidecars contain the same private diagnostics; don't depend on umask.
  for p in self.path.parent.glob(self.path.name+'-*'):
   if p.is_file():p.chmod(0o600)
 def execute(self,sql,args=()):
  try:
   with self.db:return self.db.execute(sql,args)
  except Exception:
   self.errors+=1
   return None  # A broken diagnostic disk must not break character delivery.
 def event(self,run_id,kind,data):return self.execute('INSERT INTO events(run_id,at,kind,data) VALUES(?,?,?,?)',(run_id,stamp(),kind,encode(data)))
 def link(self,run_id,kind,value):self.execute('INSERT OR IGNORE INTO links VALUES(?,?,?)',(run_id,kind,str(value)))
 @contextmanager
 def run(self,kind,metadata=None,external_key=None):
  parent=ACTIVE.get();start=time.monotonic()
  row=self.execute('INSERT INTO runs(kind,started_at,status,parent_id,metadata,external_key) VALUES(?,?,?,?,?,?)',
      (kind,stamp(),'running',parent[1] if parent and parent[0] is self else None,encode(metadata or {}),external_key))
  run_id=row.lastrowid if row else None
  token=ACTIVE.set((self,run_id)) if run_id else None
  status='completed'
  try:yield run_id
  except BaseException as exc:
   status='cancelled' if isinstance(exc,asyncio.CancelledError) else 'failed'
   self.event(run_id,'run.exception',{'type':type(exc).__name__,'message':str(exc),'stack':traceback.format_exc()})
   raise
  finally:
   self.execute('UPDATE runs SET ended_at=?,status=?,elapsed_seconds=? WHERE id=?',(stamp(),status,round(time.monotonic()-start,6),run_id))
   if token is not None:ACTIVE.reset(token)
 def recover_interrupted(self):
  self.execute("UPDATE runs SET status='interrupted',ended_at=? WHERE status='running'",(stamp(),))
  self.execute("UPDATE model_calls SET status='interrupted',ended_at=? WHERE status IN ('started','prepared')",(stamp(),))
 def recent(self,limit=8):return [dict(r) for r in self.db.execute('SELECT id,kind,started_at,status,elapsed_seconds FROM runs WHERE EXISTS (SELECT 1 FROM events e WHERE e.run_id=runs.id) OR EXISTS (SELECT 1 FROM model_calls c WHERE c.run_id=runs.id) ORDER BY id DESC LIMIT ?',(limit,))]
 def report(self,identifier):
  from .flow_debug import human,content
  from .services import visible_text
  row=self.db.execute('SELECT * FROM runs WHERE id=?',(identifier,)).fetchone()
  if not row:return '找不到這筆診斷記錄。'
  lines=[f'診斷 #{identifier}｜{row["kind"]}｜{row["status"]}',row['started_at'],'紀錄來源：'+human(json.loads(row['metadata'])),f'耗時：{row["elapsed_seconds"]} 秒','關聯：'+human([dict(x) for x in self.db.execute('SELECT kind,value FROM links WHERE run_id=?',(identifier,))])]
  related=[dict(r) for r in self.db.execute('''SELECT DISTINCT r.id,r.kind,r.status FROM runs r
   JOIN links other ON other.run_id=r.id JOIN links own ON own.kind=other.kind AND own.value=other.value
   WHERE own.run_id=? AND r.id!=? ORDER BY r.id''',(identifier,identifier))]
  if related:lines.append('相關輪次／延後分析：'+human(related))
  # Merge trace and model boundaries by archive timestamps; raw source retained.
  timeline=[(r['at'],r['id'],'event',dict(r)) for r in self.db.execute('SELECT * FROM events WHERE run_id=?',(identifier,))]
  timeline += [(r['started_at'],r['id'],'model',dict(r)) for r in self.db.execute('SELECT * FROM model_calls WHERE run_id=?',(identifier,))]
  for _,_,kind,r in sorted(timeline,key=lambda x:(x[0],x[1])):
   if kind=='event':
    d=json.loads(r['data'])
    if r['kind']=='trace':
     label=d.get('rule','')
     # Full trace remains in SQLite. Avoid printing the entire old history or
     # repeating every Prompt again when model_calls already supplies it.
     if label=='turn.snapshot':
      state=d.get('output',{});d={'rule':label,'input':d.get('inputs'),
       'state':{k:state[k] for k in ('now','scene','appearance','activity','availability') if k in state}}
     elif label in {'events.validate_reduce','debug.finished','background.delivered','context.capacity_failed'}:
      pass
     else:d={'rule':label,'reason':d.get('reason'),'seconds':d.get('seconds')}
    if r['kind']=='model.stream_complete':continue
    if r['kind']=='model.partial' and isinstance(d.get('raw'),str):d['raw']=visible_text(d['raw'])
    lines.append(r['at']+'｜'+r['kind']+'\n'+human(d));continue
   lines.append(f'模型 #{r["id"]}｜{r["stage"]}｜{r["status"]}｜{r["started_at"]} → {r["ended_at"]}')
   intended=json.loads(r['requested']);prepared=json.loads(r['prepared']) if r['prepared'] else None
   lines.append('請求設定：'+human({k:v for k,v in intended.items() if k!='messages'}))
   for label,packet in [('實際組好（不代表已送達模型）',prepared or intended)]:
    for m in packet.get('messages',[]):lines.append(label+'｜'+m['role']+'\n'+content(m['content']))
   if prepared and prepared.get('messages')!=intended.get('messages'):
    lines.append('組裝前原始messages：'+human(intended['messages']))
   if r['output'] is not None:lines.append('實際模型輸出：\n'+visible_text(r['output']))
   if r['metrics']:lines.append('模型統計：'+human(json.loads(r['metrics'])))
   if r['error']:lines.append('錯誤：'+human(json.loads(r['error'])))
  return '\n\n'.join(lines)
 def import_available(self,source):
  """Only already-retained traces; never replay or invent absent requests."""
  table=source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pyramid_turns'").fetchone()
  rows=source.execute('SELECT update_id,status,trace,assistant_id FROM pyramid_turns ORDER BY update_id') if table else []
  for row in rows:
   key='legacy:turn:'+str(row['update_id'])
   if self.db.execute('SELECT 1 FROM runs WHERE external_key=?',(key,)).fetchone():continue
   try:nodes=json.loads(row['trace'])
   except (TypeError,ValueError):continue
   if not isinstance(nodes,list):continue
   with self.run('legacy_trace',{'source':'pyramid_turns','saved_status':row['status'],'historical_times_unavailable':True},key) as rid:
    self.link(rid,'turn_id','tg:'+str(row['update_id']))
    if row['assistant_id'] is not None:self.link(rid,'assistant_id',row['assistant_id'])
    for n in nodes:self.event(rid,'legacy.trace',n)
  # Last background traces only: earlier overwritten data cannot be recovered.
  for row in source.execute("SELECT key,value FROM settings WHERE key IN ('last_pyramid_background_trace','last_response_background_trace','last_story_background_trace')"):
   import hashlib
   key='legacy:setting:'+row['key']+':'+hashlib.sha256(row['value'].encode()).hexdigest()
   if self.db.execute('SELECT 1 FROM runs WHERE external_key=?',(key,)).fetchone():continue
   try:nodes=json.loads(row['value'])
   except (TypeError,ValueError):continue
   if not isinstance(nodes,list):continue
   with self.run('legacy_background',{'source':row['key'],'historical_times_unavailable':True},key) as rid:
    for n in nodes:self.event(rid,'legacy.trace',n)
 @classmethod
 def readonly(cls,path):
  obj=object.__new__(cls);obj.path=Path(path);obj.errors=0
  obj.db=sqlite3.connect(obj.path.resolve().as_uri()+'?mode=ro',uri=True);obj.db.row_factory=sqlite3.Row
  return obj
 def close(self):self.db.close()

def diagnosed(kind):
 def decorate(fn):
  signature=inspect.signature(fn)
  @wraps(fn)
  async def wrapped(self,*args,**kwargs):
   app=getattr(self,'app',self);archive=getattr(app,'diagnostics',None)
   raw=(args[1].get('text','') if kind=='telegram_input' and len(args)>1 and isinstance(args[1],dict) else (kwargs.get('message') or {}).get('text',''))
   command,_,argument=raw.lower().partition(' ')
   if command.split('@')[0]=='/debug' and (argument.strip()=='records' or argument.strip().startswith('record ')):
    token=SUPPRESS.set(True)
    try:return await fn(self,*args,**kwargs)
    finally:SUPPRESS.reset(token)
   if not archive:return await fn(self,*args,**kwargs)
   bound=signature.bind(self,*args,**kwargs).arguments
   metadata={k:bound[k] for k in ('update_id','chat_id','text') if k in bound}
   if isinstance(bound.get('message'),dict):
    m=bound['message'];metadata.update(update_id=bound.get('update_id'),chat_id=m.get('chat',{}).get('id'),text=m.get('text',''))
    if m.get('_batch_sources'):metadata['input_parts']=m['_batch_sources']
   with archive.run(kind,metadata):
    for part in metadata.get('input_parts',[]):
     link('turn_id','tg:'+str(part['update_id']))
    if metadata.get('input_parts'):event('input.batch',{'count':len(metadata['input_parts']),'separator':'newline','quiet_window_seconds':10})
    if bound.get('destination') is not None:link('image_path',str(bound['destination']))
    if metadata.get('update_id') is not None:link('turn_id','tg:'+str(metadata['update_id']))
    return await fn(self,*args,**kwargs)
  return wrapped
 return decorate

def model_stage(fn):
 @wraps(fn)
 async def wrapped(self,trace,phase,messages,tokens,schema=None,*args,**kwargs):
  token=STAGE.set('User Observer' if phase==2 else 'Writer' if schema is None else 'Response Observer' if phase==5 else '記憶整理／結構化背景')
  try:return await fn(self,trace,phase,messages,tokens,schema,*args,**kwargs)
  finally:STAGE.reset(token)
 return wrapped

def audit_model(fn):
 @wraps(fn)
 async def wrapped(self,messages,*args,**kwargs):
  active=ACTIVE.get();archive=active[0] if active else getattr(self,'diagnostics',None)
  if SUPPRESS.get() or not archive:return await fn(self,messages,*args,**kwargs)
  if not active:
   with archive.run('unscoped_model') as new_id:
    if new_id is None:return await fn(self,messages,*args,**kwargs)
    return await wrapped(self,messages,*args,**kwargs)
  rid=active[1];began=time.monotonic()
  bound=inspect.signature(fn).bind(self,messages,*args,**kwargs);bound.apply_defaults()
  values=bound.arguments
  requested={'model':self.config.llm_model,'messages':messages,'max_tokens':values.get('max_tokens') or self.config.max_tokens,'temperature':values['temperature'],'response_schema':values.get('response_schema'),'enable_thinking':False}
  row=archive.execute('INSERT INTO model_calls(run_id,stage,started_at,status,requested) VALUES(?,?,?,?,?)',(rid,STAGE.get(),stamp(),'started',encode(requested)))
  call_id=row.lastrowid if row else None;callback=values.get('on_request')
  def prepared(actual):
   archive.execute("UPDATE model_calls SET status='prepared',prepared=? WHERE id=?",(encode(dict(requested,messages=actual)),call_id))
   if callback:callback(actual)
  bound.arguments['on_request']=prepared
  try:
   answer=await fn(*bound.args,**bound.kwargs)
   archive.execute("UPDATE model_calls SET ended_at=?,status='completed',output=?,metrics=? WHERE id=?",(stamp(),answer,encode(dict(getattr(self,'last_metrics',{}),wall_seconds=round(time.monotonic()-began,6))),call_id))
   return answer
  except BaseException as exc:
   archive.execute('UPDATE model_calls SET ended_at=?,status=?,error=? WHERE id=?',(stamp(),'cancelled' if isinstance(exc,asyncio.CancelledError) else 'failed',encode({'type':type(exc).__name__,'message':str(exc),'stack':traceback.format_exc(),'wall_seconds':round(time.monotonic()-began,6)}),call_id))
   raise
 return wrapped

class DiagnosticLogHandler(logging.Handler):
 def __init__(self,archive):super().__init__();self.archive=archive
 def emit(self,record):
  if SUPPRESS.get() or not record.name.startswith('companion'):return
  active=ACTIVE.get()
  self.archive.event(active[1] if active and active[0] is self.archive else None,'application.log',
     {'level':record.levelname,'logger':record.name,'message':record.getMessage()})

# Diagnostic report output must not recursively archive its own full report.
SUPPRESS=ContextVar('companion_diagnostic_readonly',default=False)

def audit_transport(fn):
 @wraps(fn)
 async def wrapped(self,method,*args,**kwargs):
  archive=getattr(self,'diagnostics',None)
  if SUPPRESS.get() or not archive or ACTIVE.get() or not method.startswith(('send','edit')):
   return await fn(self,method,*args,**kwargs)
  with archive.run('telegram_delivery',{'method':method}):return await fn(self,method,*args,**kwargs)
 return wrapped

def record_state(state,kind):
 if ACTIVE.get():event('state.saved',{'save_kind':kind,'snapshot':{k:state[k] for k in
  ('now','scene','appearance','user_appearance','activity','availability','emotions','energy','stress','relationship','intimacy') if k in state}})
