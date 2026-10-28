import json
import sqlite3
import unittest
from companion.vector_cache import VectorCache
from companion.pyramid_memory import MemorySearch
from companion.pyramid import initial_state,DecisionTrace
from companion.context_plan import ContextPlanner
from test_pyramid import STAMP


class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_persistent_vectors_survive_new_search_and_preserve_ranking(self):
        class Encoder:
            def __init__(self):self.calls=[]
            async def encode(self,texts,prompt_name=None):
                self.calls.append((list(texts),prompt_name))
                return [[1.,0.] for _ in texts]
        db=sqlite3.connect(':memory:');cache=VectorCache(db,'model-a')
        self.addCleanup(db.close)
        state=initial_state(STAMP)
        state['history']=[{'role':'user','content':'我喜歡紅茶','at':STAMP}]
        first_encoder=Encoder();first=await MemorySearch(first_encoder,cache).select(state,'紅茶')
        second_encoder=Encoder();second=await MemorySearch(second_encoder,VectorCache(db,'model-a')).select(state,'紅茶')
        self.assertEqual(first['items'],second['items'])
        self.assertEqual(second['documents_encoded'],0)
        self.assertTrue(second['vector_cache_hits'])
        self.assertEqual([p for _,p in second_encoder.calls],['Retrieval-query'])
        self.assertNotIn('紅茶',str(db.execute('SELECT * FROM retrieval_vectors').fetchall()))

    def test_model_namespace_content_change_corruption_and_deletion(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        a=VectorCache(db,'a');a.put({'舊資料':[1.,0.]})
        b=VectorCache(db,'b')
        self.assertEqual(b.load(['舊資料']),{})
        self.assertEqual(a.load(['新資料']),{})
        with db:db.execute("UPDATE retrieval_vectors SET vector='[NaN]'")
        self.assertEqual(a.load(['舊資料']),{})
        a.put({'舊資料':[1.,0.]});a.prune(['新資料'])
        self.assertEqual(a.load(['舊資料']),{})
        a.put({'新資料':[1.,0.]});MemorySearch(vector_cache=a).clear_cache()
        self.assertEqual(a.load(['新資料']),{})

    async def test_exact_count_memo_is_stage_local_and_does_not_skip_generation_check(self):
        class Counter:
            def __init__(self):self.calls=0
            async def count_context(self,messages):self.calls+=1;return 20
        counter=Counter();planner=ContextPlanner(counter);trace=DecisionTrace()
        state=initial_state(STAMP)
        build=lambda *args:[{'role':'user','content':'你好'}]
        await planner.plan('writer',state,trace,build,768,[],archive=False,memory=False)
        self.assertEqual(counter.calls,1)
        node=next(n for n in trace.nodes if n['rule']=='pipeline.context')
        self.assertGreater(node['output']['cache_hits'],0)
        await planner.plan('writer',state,trace,build,768,[],archive=False,memory=False)
        self.assertEqual(counter.calls,2)
        await planner.count(build())
        self.assertEqual(counter.calls,3)
