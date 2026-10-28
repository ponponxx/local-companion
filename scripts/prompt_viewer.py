"""Read-only loopback viewer for saved Prompts; never imports the chat runtime."""
import argparse
from contextlib import closing
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs, urlsplit


def readable_db(path):
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
    db.execute('PRAGMA query_only=ON')
    db.row_factory=sqlite3.Row
    return db


def panel(kind,raw,**meta):
    raw=raw if isinstance(raw,str) else ''
    return dict(kind=kind,raw=raw,digest=hashlib.sha256((kind+'\0'+raw+'\0'+json.dumps(meta,sort_keys=True)).encode()).hexdigest(),**meta)


def turns(database):
    with closing(readable_db(database)) as db:
        rows=db.execute('''SELECT r.id,r.started_at,r.status,r.metadata FROM runs r
            WHERE r.kind='telegram_input' AND EXISTS
              (SELECT 1 FROM model_calls c WHERE c.run_id=r.id AND c.stage IN ('User Observer','Writer'))
            ORDER BY r.id DESC LIMIT 30''').fetchall()
    return {'turns':[{'id':r['id'],'at':r['started_at'],'status':r['status'],
                     'preview':json.loads(r['metadata']).get('text','')[:50]} for r in rows]}


def turn(database,identifier):
    with closing(readable_db(database)) as db:
        row=db.execute("SELECT id,started_at,status,metadata FROM runs WHERE id=? AND kind='telegram_input'",(identifier,)).fetchone()
        if row is None:return None
        calls=db.execute("SELECT * FROM model_calls WHERE run_id=? AND stage IN ('User Observer','Writer') ORDER BY id",(identifier,)).fetchall()
    result={'run':{'id':row['id'],'at':row['started_at'],'status':row['status'],
                   'text':json.loads(row['metadata']).get('text','')},'observers':[],'writers':[]}
    for call in calls:
        meta={'call_id':call['id'],'status':call['status'],'at':call['started_at'],
              'ended_at':call['ended_at'],'prepared':bool(call['prepared'])}
        metrics=json.loads(call['metrics']) if call['metrics'] else {}
        meta['seconds']=metrics.get('wall_seconds',metrics.get('total_seconds'))
        # The prepared transport packet wins. A requested-only record is clearly
        # labelled; neither record proves that the server received a request.
        entry={'input':panel('request',call['prepared'] or call['requested'],**meta),
               'output':panel('output',call['output'],available=call['output'] is not None,**meta),
               'status':call['status'],'id':call['id']}
        result['observers' if call['stage']=='User Observer' else 'writers'].append(entry)
    return result


def demo_turn():
    raw=demo_snapshot()['text']['raw'];messages=json.loads(raw)
    observer_messages=[{'role':'system','content':'只判讀本輪原文，回傳事件與話語分類。'},
        {'role':'user','content':json.dumps({'speaker':'user','current_text':'今天好嗎？',
            'snapshot':{'character_location':'書房','user_location':'花園'},'history':[]},ensure_ascii=False)}]
    request=json.dumps({'messages':observer_messages,'max_tokens':1024,'temperature':0,
        'response_schema':{'type':'object','properties':{'events':{'type':'array'}},'required':['events']}},ensure_ascii=False)
    observed=json.dumps({'utterance':{'act':'question','purpose':'social'},'information_scope':'current_state',
                         'events':[],'working_updates':[],'needs_clarification':False},ensure_ascii=False)
    return {'run':{'id':1,'at':'2026-10-27T06:05:00+00:00','status':'completed','text':'今天好嗎？'},
        'observers':[{'id':1,'status':'completed','input':panel('request',request,prepared=True,status='completed',seconds=4.2),
                      'output':panel('output',observed,available=True,status='completed',seconds=4.2)}],
        'writers':[{'id':2,'status':'completed','input':panel('request',json.dumps({'messages':messages,'max_tokens':768,'temperature':0.7},ensure_ascii=False),prepared=True,status='completed',seconds=3.1)}]}


def snapshot(database):
    keys=('last_text_prompt','last_prompt','last_prompt_kind','last_prompt_image_id')
    with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
        db.execute('PRAGMA query_only=ON')
        values=dict(db.execute('SELECT key,value FROM settings WHERE key IN (?,?,?,?)',keys))
    text=values.get('last_text_prompt','')
    latest=values.get('last_prompt','')
    result={'text':{'kind':'text','raw':text},
            'latest':{'kind':values.get('last_prompt_kind','text'),'raw':latest}}
    for item in result.values():
        item['digest']=hashlib.sha256((item['kind']+'\0'+item['raw']).encode()).hexdigest()
    return result


def demo_snapshot():
    data={'identity':{'character':'林青','user':'阿哲','actors_are_distinct':True},
          'current_time':'今天是2026年10月27日星期二，現在是14點05分。',
          'current_facts':{'scene':{'character_location':'書房','user_location':'花園'},
                           'activity':'整理植物標籤','availability':'free'},
          'decision':{'primary':'share_self','image_requested':False,'response_outline':'分享有來源的自身近況。'},
          'delivery':{'warmth':'友善自然。','length':'brief'},
          'proactive_story':'一張貼反的標籤（處理中）。\n角色正在重新確認，尚未完成。',
          'daily_plan':{'upcoming':[{'time':'18:00','location':'餐廳','activity':'晚餐'}]},
          'recent_dialogue_context':[]}
    raw=json.dumps([{'role':'system','content':'你正在扮演角色。只按來源回覆，不把計畫當成完成。\n'+json.dumps(data,ensure_ascii=False,separators=(',',':'))},
                    {'role':'user','content':'今天好嗎？'}],ensure_ascii=False,indent=2)
    return {name:{'kind':'text','raw':raw,'digest':hashlib.sha256(raw.encode()).hexdigest()} for name in ('text','latest')}


def handler(database,assets,port,demo=False,diagnostics=None):
    diagnostics=diagnostics or database.with_name('diagnostics.sqlite3')
    hosts={f'127.0.0.1:{port}',f'localhost:{port}'}
    origins={'http://'+h for h in hosts}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass

        def reply(self,status,body,mime='application/json; charset=utf-8',etag=None):
            self.send_response(status)
            self.send_header('Content-Type',mime)
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('X-Frame-Options','DENY')
            self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            if etag:self.send_header('ETag',etag)
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            if body:self.wfile.write(body)

        def do_GET(self):
            if (self.headers.get('Host') not in hosts
                or self.headers.get('Origin') not in origins|{None}
                or self.headers.get('Sec-Fetch-Site')=='cross-site'):
                return self.reply(403,b'{"error":"Local access only"}')
            parsed=urlsplit(self.path);route=parsed.path
            if route in {'/api/turns','/api/turn'}:
                try:
                    if route=='/api/turns':
                        result={'turns':[dict(id=1,at='2026-10-27T06:05:00+00:00',status='completed',preview='今天好嗎？')]} if demo else turns(diagnostics)
                    else:
                        value=parse_qs(parsed.query).get('id',[''])[0]
                        if not value.isascii() or not value.isdigit() or not 0<int(value)<2**63:
                            return self.reply(400,b'{"error":"Invalid turn"}')
                        result=demo_turn() if demo and value=='1' else None if demo else turn(diagnostics,int(value))
                        if result is None:return self.reply(404,b'{"error":"Turn not found"}')
                    result['demo']=demo
                except (sqlite3.Error,OSError,ValueError,TypeError):
                    return self.reply(503,json.dumps({'error':'目前無法讀取本輪診斷封存。'},ensure_ascii=False).encode())
                body=json.dumps(result,ensure_ascii=False).encode();tag='"'+hashlib.sha256(body).hexdigest()+'"'
                if self.headers.get('If-None-Match')==tag:return self.reply(304,b'',etag=tag)
                return self.reply(200,body,etag=tag)
            if route=='/api/snapshot':
                try:result=demo_snapshot() if demo else snapshot(database)
                except (sqlite3.Error,OSError):
                    return self.reply(503,json.dumps({'error':'目前無法讀取封存資料；請稍後重試。'},ensure_ascii=False).encode())
                result['demo']=demo
                body=json.dumps(result,ensure_ascii=False).encode()
                tag='"'+hashlib.sha256(body).hexdigest()+'"'
                if self.headers.get('If-None-Match')==tag:return self.reply(304,b'',etag=tag)
                return self.reply(200,body,etag=tag)
            if route=='/health':return self.reply(200,b'{"status":"ok","read_only":true}')
            files={'/':('index.html','text/html; charset=utf-8'),
                   '/app.js':('app.js','text/javascript; charset=utf-8'),
                   '/style.css':('style.css','text/css; charset=utf-8')}
            if route not in files:return self.reply(404,b'{"error":"Not found"}')
            name,mime=files[route]
            try:return self.reply(200,(assets/name).read_bytes(),mime)
            except OSError:return self.reply(404,b'{"error":"Not found"}')
    return Handler


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,default=Path(__file__).resolve().parents[1]/'.data/telegram/memory.sqlite3')
    parser.add_argument('--assets',type=Path,default=Path(__file__).resolve().parents[1]/'tools/prompt-viewer')
    parser.add_argument('--port',type=int,default=8768)
    parser.add_argument('--demo',action='store_true')
    args=parser.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),handler(args.database,args.assets,args.port,args.demo))
    try:server.serve_forever()
    finally:server.server_close()


if __name__=='__main__':main()
