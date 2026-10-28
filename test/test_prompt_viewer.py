"""Viewer is a read-only presentation surface, not a model or memory writer."""
import hashlib
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Thread
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from scripts.prompt_viewer import snapshot,handler,demo_snapshot,turns,turn


class ViewerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Path(self.temp.name)/'sample.sqlite3'
        self.raw=json.dumps([{'role':'system','content':'規則。\n'+json.dumps({'name':'林青','note':'第一行\n第二行','flag':False},ensure_ascii=False)},
                             {'role':'user','content':'字面 \\n 和 C:\\notes'}],ensure_ascii=False,indent=2)
        with sqlite3.connect(self.db) as c:
            c.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')
            c.executemany('INSERT INTO settings VALUES (?,?)',[('last_text_prompt',self.raw),('last_prompt','夜間花園，端著茶杯。'),('last_prompt_kind','image')])
        self.server=ThreadingHTTPServer(('127.0.0.1',0),handler(self.db,Path(__file__).resolve().parents[1]/'tools/prompt-viewer',0))
        self.port=self.server.server_address[1];self.server.RequestHandlerClass=handler(self.db,Path(__file__).resolve().parents[1]/'tools/prompt-viewer',self.port)
        self.thread=Thread(target=self.server.serve_forever,daemon=True);self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()

    def request(self,path,headers=None):return urlopen(Request(f'http://127.0.0.1:{self.port}'+path,headers=headers or {}),timeout=3)

    def test_snapshot_keeps_exact_raw_and_does_not_change_database(self):
        before=hashlib.sha256(self.db.read_bytes()).hexdigest();data=snapshot(self.db)
        self.assertEqual(data['text']['raw'],self.raw);self.assertEqual(data['latest']['kind'],'image')
        self.assertEqual(data['latest']['raw'],'夜間花園，端著茶杯。')
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(),before)

    def test_http_snapshot_and_etag_do_not_rewrite_prompt(self):
        with self.request('/api/snapshot') as r:
            self.assertEqual(json.load(r)['text']['raw'],self.raw);tag=r.headers['ETag']
            self.assertEqual(r.headers['Cache-Control'],'no-store');self.assertIsNone(r.headers.get('Access-Control-Allow-Origin'))
        with self.assertRaises(HTTPError) as e:self.request('/api/snapshot',{'If-None-Match':tag})
        self.assertEqual(e.exception.code,304)

    def test_host_origin_and_cross_site_guards(self):
        for headers in [{'Host':'evil.example'},{'Origin':'https://evil.example'},{'Sec-Fetch-Site':'cross-site'}]:
            with self.assertRaises(HTTPError) as e:self.request('/api/snapshot',headers)
            self.assertEqual(e.exception.code,403)

    def test_only_explicit_assets_and_read_endpoints(self):
        for path in ['/','/app.js','/style.css','/health']:
            with self.request(path) as r:self.assertEqual(r.status,200);self.assertIn("frame-ancestors 'none'",r.headers['Content-Security-Policy'])
        for path in ['/../../.env','/memory.sqlite3','/api/apply']:
            with self.assertRaises(HTTPError) as e:self.request(path)
            self.assertEqual(e.exception.code,404)
        req=Request(f'http://127.0.0.1:{self.port}/api/snapshot',data=b'{}',method='POST')
        with self.assertRaises(HTTPError) as e:urlopen(req,timeout=3)
        self.assertEqual(e.exception.code,501)

    def test_missing_database_error_does_not_leak_path(self):
        self.db.unlink()
        with self.assertRaises(HTTPError) as e:self.request('/api/snapshot')
        self.assertEqual(e.exception.code,503);self.assertNotIn(self.temp.name,e.exception.read().decode())
        self.assertFalse(self.db.exists())

    def test_demo_is_neutral_synthetic_and_nested_strings_are_intact(self):
        data=demo_snapshot();messages=json.loads(data['text']['raw']);content=messages[0]['content']
        inner=json.loads(content.split('\n',1)[1]);self.assertEqual(inner['identity']['user'],'阿哲')
        self.assertIn('\n',inner['proactive_story']);self.assertFalse(inner['decision']['image_requested'])


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=Path(self.temp.name)/'diagnostics.sqlite3'
        with sqlite3.connect(self.db) as c:
            c.executescript('''CREATE TABLE runs(id INTEGER PRIMARY KEY,kind TEXT,started_at TEXT,status TEXT,metadata TEXT);
            CREATE TABLE model_calls(id INTEGER PRIMARY KEY,run_id INTEGER,stage TEXT,started_at TEXT,ended_at TEXT,status TEXT,requested TEXT,prepared TEXT,output TEXT,metrics TEXT,error TEXT);''')
            for i,kind in [(1,'telegram_input'),(2,'background'),(3,'telegram_input'),(4,'telegram_input')]:
                c.execute('INSERT INTO runs VALUES (?,?,?,?,?)',(i,kind,'2026-10-27T06:00:00+00:00','completed',json.dumps({'text':'合成對話'+str(i)})))
            def call(i,r,stage,status='completed',prepared='prepared request',output='{"events":[]}',requested='original request'):
                c.execute('INSERT INTO model_calls VALUES (?,?,?,?,?,?,?,?,?,?,?)',(i,r,stage,'start','end',status,requested,prepared,output,'{"wall_seconds":2.3}',None))
            call(10,1,'User Observer','failed',None,None);call(11,1,'User Observer');call(12,1,'Writer')
            call(13,1,'Response Observer');call(14,1,'記憶整理／結構化背景')
            call(20,2,'Writer',output='unrelated background')
            call(30,3,'User Observer','failed',None,None)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),handler(Path(self.temp.name)/'unused.sqlite3',Path('.'),0,diagnostics=self.db))
        self.port=self.server.server_address[1];self.server.RequestHandlerClass=handler(Path(self.temp.name)/'unused.sqlite3',Path('.'),self.port,diagnostics=self.db)
        self.thread=Thread(target=self.server.serve_forever,daemon=True);self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()

    def test_turns_only_include_matching_foreground_not_proactive_or_empty(self):
        self.assertEqual([r['id'] for r in turns(self.db)['turns']],[3,1])

    def test_same_run_input_output_and_writer_cannot_cross_join(self):
        before=hashlib.sha256(self.db.read_bytes()).hexdigest();result=turn(self.db,1)
        self.assertEqual([x['id'] for x in result['observers']],[10,11])
        self.assertEqual([x['id'] for x in result['writers']],[12])
        self.assertEqual(result['observers'][1]['input']['raw'],'prepared request')
        self.assertEqual(result['observers'][1]['output']['raw'],'{"events":[]}')
        self.assertEqual(result['observers'][1]['input']['seconds'],2.3)
        self.assertNotIn('unrelated background',json.dumps(result))
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(),before)

    def test_missing_prepared_output_and_writer_explicit_not_borrowed(self):
        result=turn(self.db,3);observer=result['observers'][0]
        self.assertFalse(observer['input']['prepared']);self.assertEqual(observer['input']['raw'],'original request')
        self.assertFalse(observer['output']['available']);self.assertEqual(observer['output']['raw'],'')
        self.assertEqual(result['writers'],[])
        self.assertIsNone(turn(self.db,2));self.assertIsNone(turn(self.db,99))

    def test_run_identifiers_validated_and_snapshot_etag_changes_after_output(self):
        base=f'http://127.0.0.1:{self.port}'
        for value in ['-1','0','not-a-number','9223372036854775808']:
            with self.assertRaises(HTTPError) as e:urlopen(base+'/api/turn?id='+value,timeout=3)
            self.assertEqual(e.exception.code,400)
        with urlopen(base+'/api/turn?id=1',timeout=3) as r:
            tag=r.headers['ETag'];self.assertEqual(json.load(r)['run']['id'],1)
        with self.assertRaises(HTTPError) as e:urlopen(Request(base+'/api/turn?id=1',headers={'If-None-Match':tag}),timeout=3)
        self.assertEqual(e.exception.code,304)
        with sqlite3.connect(self.db) as c:c.execute('UPDATE model_calls SET output=? WHERE id=11',('{"events":[1]}',))
        with urlopen(Request(base+'/api/turn?id=1',headers={'If-None-Match':tag}),timeout=3) as r:
            self.assertEqual(r.status,200);self.assertNotEqual(r.headers['ETag'],tag)


if __name__=='__main__':unittest.main()
