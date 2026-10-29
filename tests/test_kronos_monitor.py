"""Daily monitor lifecycle and input-change checks without loading model weights."""
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import tempfile
import unittest
import pandas as pd
from kronos.data import IST
from kronos.monitor import DailyMonitor, describe_change
from kronos.storage import Store

class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/'test.db')
        dates=pd.bdate_range(end=datetime.now(IST).date()-timedelta(days=1),periods=128)
        self.bars=pd.DataFrame(dict(open=100.,high=101.,low=99.,close=100.,volume=1000.),index=dates)
        self.service=SimpleNamespace(store=self.store,active=None,market=Mock())
        self.service.market.symbols.return_value=['TEST']
        self.service.market.bars.side_effect=lambda *args:self.bars.copy()
        self.service.submit=Mock(side_effect=self.submit)
        self.monitor=DailyMonitor(self.service)
        self.monitor.configure({'symbol':'TEST','enabled':True})
    def tearDown(self):
        self.monitor.close();self.temp.cleanup()
    def submit(self, request):
        key=self.store.create('forecast',request)
        return self.store.get(key)
    def test_no_duplicate_while_pending_and_after_failure(self):
        self.monitor.tick();self.monitor.tick()
        self.assertEqual(self.service.submit.call_count,1)
        row=self.monitor.rows()[0]
        self.store.update(row['run'],status='failed')
        self.monitor.tick();self.monitor.tick()
        self.assertEqual(self.service.submit.call_count,1)
        self.assertIn('failed',self.monitor.rows()[0]['message'])
    def test_changed_data_retries_failed_input(self):
        self.monitor.tick()
        self.store.update(self.monitor.rows()[0]['run'],status='failed')
        self.bars.iloc[-1,self.bars.columns.get_loc('close')]=100.5
        self.monitor.tick()
        self.assertEqual(self.service.submit.call_count,2)
    def test_stale_data_and_busy_service_do_not_submit(self):
        self.service.active='manual-job';self.monitor.tick()
        self.assertEqual(self.service.submit.call_count,0)
        self.service.active=None
        self.bars.index-=pd.Timedelta(days=20)
        self.monitor.tick()
        self.assertEqual(self.service.submit.call_count,0)
        self.assertIn('old',self.monitor.rows()[0]['message'])
    def test_pause_persists_after_restart(self):
        self.monitor.configure({'symbol':'TEST','enabled':False})
        restarted=DailyMonitor(self.service);restarted.tick()
        self.assertFalse(restarted.rows()[0]['enabled'])
        self.assertEqual(self.service.submit.call_count,0)
    def test_setting_change_and_new_bar_are_new_inputs(self):
        from dataclasses import replace
        self.monitor.tick()
        self.store.update(self.monitor.rows()[0]['run'],status='cancelled')
        self.store.save_settings(replace(self.store.settings(),horizon=10))
        self.monitor.tick()
        self.assertEqual(self.service.submit.call_count,2)
    def test_change_description_distinguishes_settings(self):
        old={'settings':{'horizon':15},'summary':{'bias':'Mixed outlook','upside_rate':50},'pattern':None}
        new={'settings':{'horizon':15},'summary':{'bias':'Upside bias','upside_rate':75},'pattern':{'scanner':'darvas'}}
        self.assertIn('+25.0',describe_change(old,new))
        self.assertIn('Pattern detected',describe_change(old,new))
        new['settings']={'horizon':10}
        self.assertIn('settings changed',describe_change(old,new))
    def test_completed_forecast_scored_without_duplicate(self):
        self.monitor.tick()
        key=self.monitor.rows()[0]['run']
        result={'asof':str(self.bars.index[-1].date()),'settings':{},
                'summary':{'bias':'Mixed outlook','upside_rate':50},'pattern':None}
        self.store.update(key,status='complete',result=result)
        comparison={'available':1,'required':15,'complete':False,'metrics':{'mae':1,'baseline_mae':2}}
        self.service.run=Mock(return_value={'result':{'comparison':comparison}})
        self.monitor.tick()
        row=self.monitor.rows()[0]
        self.assertEqual(row['last_success'],key)
        self.assertEqual(row['quality']['available'],1)
        self.assertEqual(row['quality_origin'],result['asof'])
        self.assertEqual(self.service.submit.call_count,1)

if __name__=='__main__':unittest.main()
