"""Disposable local retrieval vectors; never a source of dialogue facts."""
import hashlib
import json
import math
from pathlib import Path


def model_fingerprint(model_path):
    root=Path(model_path).resolve()
    entries=[]
    if not root.is_dir():return None
    for path in sorted(root.rglob('*')):
        if not path.is_file() or '.cache' in path.relative_to(root).parts:continue
        if path.suffix not in {'.json','.safetensors','.bin','.model','.txt'}:continue
        stat=path.stat()
        entries.append((str(path.relative_to(root)),stat.st_size,stat.st_mtime_ns))
    if not entries:return None
    # Prompt name, input truncation and encoding implementation are part of identity.
    payload=['retrieval-document-v1',str(root),entries]
    worker=Path(__file__).with_name('embedding_worker.py')
    payload.append(hashlib.sha256(worker.read_bytes()).hexdigest())
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


def valid_vector(vector):
    return (isinstance(vector,list) and bool(vector) and
            all(type(v) in (int,float) and math.isfinite(v) for v in vector))


class VectorCache:
    def __init__(self,db,namespace):
        self.db,self.namespace=db,namespace
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS retrieval_vectors(namespace TEXT, digest TEXT, vector TEXT NOT NULL, PRIMARY KEY(namespace,digest))')

    @staticmethod
    def key(text):return hashlib.sha256(text.encode()).hexdigest()

    def load(self,texts):
        result={}
        for text in texts:
            row=self.db.execute('SELECT vector FROM retrieval_vectors WHERE namespace=? AND digest=?',
                                (self.namespace,self.key(text))).fetchone()
            if row:
                try:vector=json.loads(row[0])
                except (ValueError,TypeError):continue
                if valid_vector(vector):result[text]=vector
        return result

    def put(self,values):
        if not all(valid_vector(v) for v in values.values()):raise ValueError('invalid vectors')
        with self.db:
            self.db.executemany('INSERT OR REPLACE INTO retrieval_vectors VALUES(?,?,?)',
                [(self.namespace,self.key(t),json.dumps(v)) for t,v in values.items()])

    def prune(self,texts):
        keep={self.key(t) for t in texts}
        rows=self.db.execute('SELECT namespace,digest FROM retrieval_vectors').fetchall()
        with self.db:
            self.db.executemany('DELETE FROM retrieval_vectors WHERE namespace=? AND digest=?',
                [(r[0],r[1]) for r in rows if r[0]!=self.namespace or r[1] not in keep])

    def clear(self):
        with self.db:self.db.execute('DELETE FROM retrieval_vectors')
