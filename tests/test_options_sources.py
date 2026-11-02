import html
import json
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from kronos.options_sources import ContextSources, parse_iv, parse_events, SB_EVENTS
from kronos.options_market import IST
from kronos.storage import Store


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name)/'test.sqlite3')
        self.source=ContextSources(self.store)
        self.clock=patch('kronos.options_sources.clock',return_value=datetime(2026,10,30,17,tzinfo=IST))
        self.clock.start()
    def tearDown(self):self.clock.stop();self.tmp.cleanup()

    def test_iv_parser_preserves_real_zero_and_drops_invalid_values(self):
        p=dict(date='2026-10-29',rows=[dict(s='AAA',ivp=0,n=247),dict(s='BAD',ivp=101,n=247)])
        value=parse_iv('<div data-payload="'+html.escape(json.dumps(p),quote=True)+'"></div>')
        self.assertEqual(value['rows']['AAA']['value'],0)
        self.assertNotIn('BAD',value['rows'])
        with self.assertRaises(ValueError):parse_iv('<html>Login required</html>')

    def test_iv_freshness_and_short_history(self):
        p=dict(date='2026-10-29',rows={'AAA':dict(value=75,sessions=247)})
        with patch.object(self.source,'get',return_value={**p,'fetched_at':'2026-10-30'}):
            self.assertTrue(self.source.ivp('AAA')['usable'])
            p['rows']['AAA']['sessions']=20
            self.assertFalse(self.source.ivp('AAA')['usable'])
        with patch.object(self.source,'get',return_value={**p,'date':'2026-10-20','fetched_at':'2026-10-30'}):
            self.assertFalse(self.source.ivp('AAA')['usable'])

    def test_exchange_symbol_and_iso_date_parsing(self):
        rows=parse_events([dict(bm_symbol='SBIN',bm_date='03-Nov-2026',bm_purpose='Financial Results'),
                           dict(single_stock='AAA',event_date='2026-11-02',title='stock_results')],'Results','source')
        self.assertEqual(rows[0]['symbol'],'SBIN')
        self.assertEqual(rows[1]['date'],'2026-11-02')
        self.assertEqual(rows[1]['title'],'Financial results')

    def test_daily_cache_survives_restart(self):
        calls=[]
        def load():calls.append(1);return dict(rows=[])
        self.source.get('test',load)
        ContextSources(self.store).get('test',load)
        self.assertEqual(len(calls),1)

    def test_events_deduplicate_sources_and_show_after_holding_period(self):
        def get(name,loader):
            return dict(fetched_at='2026-10-30',rows=[dict(symbol='SBIN',date='2026-11-03',title='Financial Results',source=name),
                         dict(symbol='SBIN',date='2026-11-10',title='Dividend',source=name)])
        with patch.object(self.source,'get',side_effect=get):
            value=self.source.calendar('SBIN','2026-11-06')
        self.assertTrue(value['checked'])
        self.assertEqual(len(value['events']),1)
        self.assertEqual(len(value['upcoming']),2)
        self.assertEqual(value['upcoming'][0]['days_away'],4)
        self.assertFalse(value['upcoming'][1]['during_trade'])
        self.assertEqual(len(value['upcoming'][0]['sources']),3)

    def test_partial_or_failed_sources_never_report_complete_coverage(self):
        def get(name,loader):
            if name.startswith('nse'):raise ValueError('blocked')
            return dict(fetched_at='2026-10-30',rows=[])
        with patch.object(self.source,'get',side_effect=get):
            value=self.source.calendar('SBIN','2026-11-06')
        self.assertFalse(value['checked'])
        self.assertIn('Partial',value['coverage'])


if __name__=='__main__':unittest.main()
