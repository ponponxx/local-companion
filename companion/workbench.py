"""Loopback-only workbench. Run: uv run python -m companion.workbench.

Sandbox databases never contain production work queues or Telegram credentials.
A dedicated asyncio thread owns all sandbox writes and local model calls.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import uuid
from zoneinfo import ZoneInfo
from urllib.parse import urlparse
import subprocess

import httpx

from .config import Config, ROOT
from .services import LLM, ServiceError
from .pyramid import (PyramidCore, DecisionTrace, TurnInput, RULES, VERSION, initial_state,
                      validate_state, date, RELATIONSHIP_DEFAULTS)
from .pyramid_runner import PyramidRunner
from .pyramid_memory import MemorySearch, ensure_memory
from .router import EmbeddingClient

STATIC = Path(__file__).parent / 'workbench_static'
PRODUCTION_DB = Path.home() / 'Library/Application Support/LocalCompanion/.data/telegram/memory.sqlite3'
EDITABLE = {'story', 'user_profile', 'traits', 'need_weights', 'adult_traits', 'adult_policy',
            'relationship', 'emotions', 'energy', 'acute_stress', 'availability', 'activity',
            'scene', 'appearance', 'presentation', 'control_mode', 'language', 'schedule', 'intimacy'}


def merge(base, patch):
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict): merge(base[key], value)
        else: base[key] = deepcopy(value)
    return base


class SandboxRepository:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def connect(self, identifier):
        if len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
            raise ValueError('沙盒 ID 無效')
        path = self.root / (identifier + '.sqlite3')
        if not path.exists(): raise ValueError('找不到沙盒')
        return sqlite3.connect(path)

    def create(self, state):
        identifier = uuid.uuid4().hex
        path = self.root / (identifier + '.sqlite3')
        with sqlite3.connect(path) as db:
            db.executescript('CREATE TABLE snapshot(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE runs(id TEXT PRIMARY KEY,payload TEXT);')
            db.executemany('INSERT INTO snapshot VALUES(?,?)', [(k, json.dumps(state, ensure_ascii=False)) for k in ('baseline', 'state')])
            db.execute('INSERT INTO snapshot VALUES(?,?)', ('pending_overrides', '{}'))
        path.chmod(0o600)
        return identifier

    def read(self, identifier, key='state'):
        with self.connect(identifier) as db:
            row = db.execute('SELECT value FROM snapshot WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else {}

    def overrides(self, identifier, patch=None):
        current = self.read(identifier, 'pending_overrides')
        if patch is None: return current
        with self.connect(identifier) as db:
            db.execute('INSERT OR REPLACE INTO snapshot VALUES(?,?)', ('pending_overrides', json.dumps(patch, ensure_ascii=False)))

    def write(self, identifier, state, baseline=False):
        with self.connect(identifier) as db:
            keys = ['state', 'baseline'] if baseline else ['state']
            db.executemany('UPDATE snapshot SET value=? WHERE key=?', [(json.dumps(state, ensure_ascii=False), k) for k in keys])

    def result(self, identifier, job):
        with self.connect(identifier) as db:
            db.execute('INSERT OR REPLACE INTO runs VALUES(?,?)', (job['id'], json.dumps(job, ensure_ascii=False)))

    def export(self, identifier):
        with self.connect(identifier) as db:
            runs = [json.loads(r[0]) for r in db.execute('SELECT payload FROM runs ORDER BY rowid')]
        return {'version': VERSION, 'rules': RULES, 'state': self.read(identifier),
                'baseline': self.read(identifier, 'baseline'), 'runs': runs}


def import_production(path=PRODUCTION_DB):
    from .pyramid_import import import_legacy_state
    return import_legacy_state(path)

class WorkbenchService:
    def __init__(self, root, config=None, llm=None):
        self.repo = SandboxRepository(root)
        self.config = config or Config.load(require_credentials=False)
        self.llm_override = llm
        self.jobs, self.tasks = {}, {}
        self.core = PyramidCore()
        self.active = None
        self.memory_search = MemorySearch()
        self.memory_idle = None
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._serve_loop, daemon=True)
        self.thread.start()

    def _serve_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(timeout=30)

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks: task.cancel()
        if tasks: await asyncio.gather(*tasks, return_exceptions=True)
        if self.memory_idle:
            self.memory_idle.cancel()
            await asyncio.gather(self.memory_idle, return_exceptions=True)
        if self.memory_search.embedder: await self.memory_search.embedder.close()

    async def api(self, method, path, body):
        parts = path.strip('/').split('/')
        if path == '/api/meta':
            return {'version': VERSION, 'rules': RULES, 'default': initial_state(),
                    'production_available': PRODUCTION_DB.is_file(), 'local_only': True}
        if path == '/api/health':
            async with httpx.AsyncClient(trust_env=False) as client:
                healthy = await LLM(self.config, client).healthy()
            return {'llm_reachable': healthy, 'model_alias': self.config.llm_model, 'version': VERSION}
        if path == '/api/model/wake' and method == 'POST':
            # Start the already configured local model, never install/download anything.
            label = f'gui/{os.getuid()}/com.localcompanion.llama'
            proc = await asyncio.create_subprocess_exec('/bin/launchctl', 'kickstart', label,
                                                       stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            code = await proc.wait()
            if code: raise ValueError('本地模型服務未註冊；請用既有 scripts/start_chat.py 啟動')
            return {'requested': True}
        if path == '/api/sandboxes' and method == 'POST':
            if body.get('source', 'clean') not in {'clean', 'production'}: raise ValueError('資料來源無效')
            state = import_production() if body.get('source') == 'production' else initial_state()
            identifier = self.repo.create(state)
            return {'id': identifier, 'state': state}
        if len(parts) >= 3 and parts[:2] == ['api', 'sandboxes']:
            identifier = parts[2]
            state = self.repo.read(identifier)
            if len(parts) == 3:
                if method == 'GET': return {'id': identifier, 'state': state}
                if method == 'PATCH':
                    if self.active: raise ValueError('有測試執行中，請先完成或取消')
                    if not isinstance(body.get('patch'), dict) or set(body['patch']) - EDITABLE:
                        raise ValueError('含不可編輯欄位')
                    edited = merge(state, body['patch'])
                    validate_state(edited)
                    self.core._stress(edited)
                    self.repo.write(identifier, edited, baseline=True)
                    self.repo.overrides(identifier, merge(self.repo.overrides(identifier), body['patch']))
                    return {'id': identifier, 'state': edited}
            operation = parts[3] if len(parts) == 4 else ''
            if operation == 'memory' and method == 'POST':
                if self.active: raise ValueError('有測試執行中')
                mem = ensure_memory(state)
                note = next((n for n in mem['notes'] if n['id'] == body.get('id')), None)
                if note is None or type(body.get('important')) is not bool: raise ValueError('記憶標記無效')
                note['important'] = body['important']
                self.repo.write(identifier, state)
                return {'state': state}
            if operation == 'export' and method == 'GET': return self.repo.export(identifier)
            if operation == 'reset' and method == 'POST':
                if self.active: raise ValueError('有測試執行中，請先完成或取消')
                state = self.repo.read(identifier, 'baseline')
                self.repo.write(identifier, state)
                self.repo.overrides(identifier, {})
                return {'state': state}
            if operation == 'advance' and method == 'POST':
                if self.active: raise ValueError('有測試執行中，請先完成或取消')
                minutes = body.get('minutes', 10)
                if type(minutes) not in (int, float) or not 0 <= minutes <= 525600:
                    raise ValueError('分鐘範圍需為 0–525600')
                trace = DecisionTrace()
                to = (date(state['now']) + timedelta(minutes=minutes)).isoformat()
                self.core.advance(state, to, trace)
                self.repo.write(identifier, state)
                return {'state': state, 'trace': trace.nodes}
            if operation == 'runs' and method == 'POST':
                if self.active: raise ValueError('一次只執行一個模型測試，避免記憶體競爭')
                text = body.get('text', '')
                if not isinstance(text, str) or not text.strip() or len(text) > 3500: raise ValueError('請輸入 1–3500 字')
                observation_cache = {}
                if body.get('rerun'):
                    previous = [v for v in self.jobs.values() if v['sandbox'] == identifier and v.get('before')]
                    if previous:
                        state = deepcopy(previous[-1]['before'])
                        if body.get('reuse_observation', True) and previous[-1]['status'] == 'done':
                            observation_cache = deepcopy(previous[-1].get('user_observation_cache', {}))
                    state = merge(state, self.repo.overrides(identifier))
                    validate_state(state)
                    self.core._stress(state)
                self.repo.overrides(identifier, {})
                job_id = uuid.uuid4().hex
                job = {'id': job_id, 'sandbox': identifier, 'status': 'queued', 'progress': '準備中',
                       'before': deepcopy(state), 'trace': [], 'input': text, 'generate_reply': bool(body.get('generate_reply')),
                       'user_observation_cache': observation_cache}
                self.jobs[job_id] = job
                self.active = job_id
                self.tasks[job_id] = asyncio.create_task(self._run(identifier, state, job))
                return {'run_id': job_id}
        if len(parts) in (3, 4) and parts[:2] == ['api', 'runs']:
            job = self.jobs.get(parts[2])
            if not job: raise ValueError('測試工作不存在')
            if len(parts) == 4 and parts[3] == 'cancel' and method == 'POST':
                task = self.tasks.get(job['id'])
                if task: task.cancel()
                return {'requested': True}
            if len(parts) == 3 and method == 'GET': return deepcopy(job)
        raise ValueError('找不到 API')

    async def _run(self, identifier, state, job):
        trace = DecisionTrace(job['trace'])
        job['status'] = 'running'
        def progress(value): job['progress'] = value
        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                llm = self.llm_override or LLM(self.config, client)
                if not self.llm_override and self.memory_search.embedder is None:
                    self.memory_search.embedder = EmbeddingClient(self.config.router_python, self.config.router_model_path)
                    self.memory_idle = asyncio.create_task(self.memory_search.embedder.watch_idle())
                result = await PyramidRunner(llm, self.core, self.memory_search).run(state, TurnInput(job['input'], job['id'], job['generate_reply']), trace, progress,
                                                                observation_cache=job['user_observation_cache'])
            self.repo.write(identifier, result.after)
            job.update(status='done', progress='完成', **asdict(result))
        except asyncio.CancelledError:
            job.update(status='cancelled', progress='已取消', partial_state=state,
                       error='已取消；沙盒起始狀態未改寫，部分判讀保留在 trace')
        except Exception as exc:
            job.update(status='failed', progress='失敗', error=str(exc)[:500], partial_state=state)
        finally:
            self.repo.result(identifier, job)
            self.active = None
            self.tasks.pop(job['id'], None)


class Handler(BaseHTTPRequestHandler):
    server_version = 'LocalWorkbench/1'

    def log_message(self, *args):
        pass  # Do not log private prompts, model output or credentials.

    def send_json(self, value, status=200):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_request(self):
        host = self.headers.get('Host', '')
        allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        origin = self.headers.get('Origin')
        if host not in allowed or (origin and origin not in {'http://' + v for v in allowed}):
            return self.send_json({'error': '僅允許本機同來源請求'}, 403)
        path = urlparse(self.path).path
        if self.command == 'GET' and path in {'/', '/app.js', '/style.css'}:
            filename, mime = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}[path]
            data = (STATIC / filename).read_text().replace('__TOKEN__', self.server.token).encode()
            self.send_response(200)
            self.send_header('Content-Type', mime + '; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if not path.startswith('/api/'): return self.send_json({'error': 'Not found'}, 404)
        if self.headers.get('X-Workbench-Token') != self.server.token:
            return self.send_json({'error': '請重新載入本機平台'}, 403)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 <= length <= 2_000_000: raise ValueError('請求過大')
            if self.command in {'POST', 'PATCH'} and self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('需要 JSON 請求')
            body = json.loads(self.rfile.read(length)) if length else {}
            if not isinstance(body, dict): raise ValueError('需要 JSON 物件')
            result = self.server.service.submit(self.server.service.api(self.command, path, body))
            self.send_json(result)
        except (ValueError, KeyError, TypeError) as exc:
            self.send_json({'error': str(exc)[:300]}, 400)
        except Exception:
            self.send_json({'error': '本機服務錯誤；請檢查資料與服務狀態'}, 500)

    do_GET = do_POST = do_PATCH = handle_request


def main():
    parser = argparse.ArgumentParser(description='本地金字塔測試平台，不連線 Telegram')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data-dir', type=Path, default=ROOT / '.data/workbench')
    args = parser.parse_args()
    os.umask(0o077)
    service = WorkbenchService(args.data_dir)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.service, server.token = service, secrets.token_hex(32)
    print(f'Local workbench: http://127.0.0.1:{server.server_port}', flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        server.server_close()
        service.submit(service.close())
        service.loop.call_soon_threadsafe(service.loop.stop)
        service.thread.join(timeout=3)


if __name__ == '__main__':
    main()
