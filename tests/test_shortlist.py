"""Shortlist persistence and protected API; temporary databases, no broker calls."""
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest

from kronos.server import create_server
from kronos.shortlist import Shortlist, review_setups
from kronos.storage import Store


class ShortlistTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'research.sqlite3')
        self.book = Shortlist(self.store)

    def test_notes_survive_restart_and_pin_updates(self):
        self.book.save(dict(symbol='TEST', note='Wait for close above trigger'))
        self.book.save(dict(symbol='TEST', pinned=True))
        row = Shortlist(Store(self.store.path)).entries()['TEST']
        self.assertTrue(row['pinned'])
        self.assertEqual(row['note'], 'Wait for close above trigger')
        self.book.save(dict(symbol='TEST', pinned=False))
        self.assertEqual(self.book.entries()['TEST']['note'], row['note'])

    def test_notes_do_not_modify_settings_or_research_runs(self):
        settings = self.store.settings()
        run = self.store.create('forecast', {'symbol':'TEST'})
        self.book.save(dict(symbol='TEST', note='Research only', pinned=True))
        self.assertEqual(self.store.settings(), settings)
        self.assertEqual(self.store.get(run)['request'], {'symbol':'TEST'})

    def test_bad_requests_do_not_write(self):
        for request in (None, [], {'symbol':'../TEST'}, {'symbol':'TEST','pinned':1},
                        {'symbol':'TEST','note':'x'*2001}, {'symbol':'TEST','note':None},
                        {'symbol':'TEST','extra':'bad'}, {'symbol':['TEST']}):
            with self.subTest(request=str(request)[:70]), self.assertRaises(ValueError):
                self.book.save(request)
        self.assertEqual(self.book.entries(), {})

    def test_json_view_keeps_bearish_and_weekly_and_handles_bad_numbers(self):
        rows = review_setups([
            dict(symbol='TEST',scanner='cup',timeframe='D',direction='long',pivot=100,score=70),
            dict(symbol='TEST',scanner='cup_bear',timeframe='W',pivot=float('nan'),score=float('inf'))])
        self.assertEqual([r['direction'] for r in rows], ['long','short'])
        self.assertEqual(rows[1]['timeframe'],'W')
        self.assertIsNone(rows[1]['pivot'])
        json.dumps(rows,allow_nan=False)

    def test_api_persistence_and_write_protection(self):
        service = SimpleNamespace(store=self.store,setups=lambda:[dict(symbol='TEST',scanner='darvas_bear',timeframe='D')])
        server = create_server(service,0)
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            conn = HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            def call(method,path,body=None,headers=None):
                conn.request(method,path,json.dumps(body) if body is not None else None,headers or {})
                response=conn.getresponse()
                return response.status,json.loads(response.read())
            status,data=call('GET','/api/shortlist')
            self.assertEqual(status,200)
            self.assertEqual(data['setups'][0]['direction'],'short')
            body=dict(symbol='TEST',pinned=True,note='Keep after pattern disappears')
            self.assertEqual(call('POST','/api/shortlist',body,{'Content-Type':'application/json'})[0],403)
            headers={'Content-Type':'application/json','X-Kronos-Token':data['token']}
            self.assertEqual(call('POST','/api/shortlist',body,{**headers,'Origin':'https://untrusted.example'})[0],403)
            self.assertEqual(call('POST','/api/shortlist',body,headers)[0],200)
            service.setups=lambda:[]
            status,data=call('GET','/api/shortlist')
            self.assertEqual(data['setups'],[])
            self.assertTrue(data['entries']['TEST']['pinned'])
            self.assertEqual(call('POST','/api/shortlist',{'symbol':'TEST','pinned':'yes'},headers)[0],400)
            conn.close()
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__=='__main__':
    unittest.main()
