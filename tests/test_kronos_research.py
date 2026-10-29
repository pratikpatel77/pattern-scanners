"""Research workspace tests: future isolation, trade fills, metrics and local API."""
from dataclasses import replace
from datetime import date
from http.client import HTTPConnection
import json
from pathlib import Path
import pickle
import sys
import tempfile
import threading
import unittest
import numpy as np
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from scanners.common import Signal
from kronos.settings import Settings
from kronos.storage import Store
from kronos.data import MarketData
from kronos.engine import Cancelled, forecast
from kronos.evaluation import compare, simulate_trade, summarize
from kronos.service import ResearchService
from kronos.server import create_server


def bars(n=200):
    index=pd.bdate_range('2025-01-01',periods=n)
    return pd.DataFrame({'open':100.,'high':101.,'low':99.,'close':100.,'volume':10000.},index=index)


def path(rows):
    return pd.DataFrame(rows,columns=['open','high','low','close'],index=pd.bdate_range('2025-11-03',periods=len(rows))).assign(volume=10000.)


def signal(asof=date(2025,10,7)):
    return Signal('darvas','TEST','D',asof,100.,105.,95.,75.,'Test box')


class Predictor:
    max_context=512
    research_metadata={'model':'test-model','device':'test'}
    def __init__(self): self.calls=[]
    def predict_samples(self,**kwargs):
        self.calls.append(kwargs)
        values=np.linspace(106,116,kwargs['pred_len'])
        result=pd.DataFrame({'open':values,'high':values+1,'low':values-1,'close':values,'volume':10000.},index=pd.DatetimeIndex(kwargs['y_timestamp']))
        return result,[result.copy() for _ in range(kwargs['sample_count'])]


class SettingsTests(unittest.TestCase):
    def test_single_strategy_row_updates_in_place(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'test.db')
            for horizon in (5,10,15): store.save_settings(replace(Settings(),horizon=horizon))
            self.assertEqual(store.settings().horizon,15)
            with store.connect() as db: self.assertEqual(db.execute('SELECT count(*) FROM strategy').fetchone()[0],1)

    def test_run_snapshots_do_not_change_with_shared_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'test.db')
            key=store.create('forecast',{'settings':Settings().to_dict()})
            store.save_settings(replace(Settings(),horizon=30))
            self.assertEqual(store.get(key)['request']['settings']['horizon'],15)

    def test_invalid_controls_are_rejected(self):
        for values in ({'samples':0},{'lookback':10000},{'samples':4.5},{'model':'unknown'},{'fee_bps':float('nan')},{'risk_pct':50},{'future':'bad'}):
            with self.assertRaises(ValueError):Settings.from_dict(values)

    def test_interrupted_run_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'test.db')
            key=store.create('forecast',{})
            store.update(key,status='running')
            store.recover()
            self.assertEqual(store.get(key)['status'],'interrupted')


class ForecastTests(unittest.TestCase):
    def test_band_sample_count_and_raw_path_batching(self):
        predictor=Predictor()
        result=forecast('TEST',bars(),replace(Settings(),horizon=4,samples=23,batch_size=8),signal(),predictor)
        self.assertEqual([c['sample_count'] for c in predictor.calls],[8,8,7])
        self.assertEqual(result['summary']['sample_count'],23)
        self.assertEqual(len(result['history']),128)
        self.assertTrue(all(r['q10']<=r['median']<=r['q90'] for r in result['forecast']))

    def test_no_pattern_does_not_invent_breakout_odds(self):
        result=forecast('TEST',bars(),replace(Settings(),horizon=4,samples=4),predictor=Predictor())
        self.assertIsNone(result['breakout'])
        self.assertFalse(result['plan']['pattern_detected'])

    def test_invalid_decoder_output_is_visible(self):
        class Broken(Predictor):
            def predict_samples(self,**kwargs):
                mean,paths=super().predict_samples(**kwargs)
                paths[0].iloc[0,0]=float('nan')
                return mean,paths
        with self.assertRaises(ValueError):forecast('TEST',bars(),replace(Settings(),samples=4),predictor=Broken())

    def test_cancel_before_model_work(self):
        event=threading.Event();event.set();predictor=Predictor()
        with self.assertRaises(Cancelled):forecast('TEST',bars(),Settings(),predictor=predictor,cancel=event)
        self.assertEqual(predictor.calls,[])

    def test_cancel_between_batches(self):
        event=threading.Event();predictor=Predictor()
        def progress(*_):event.set()
        with self.assertRaises(Cancelled):forecast('TEST',bars(),replace(Settings(),samples=20,batch_size=4),predictor=predictor,progress=progress,cancel=event)
        self.assertEqual(len(predictor.calls),1)

    def test_malformed_future_dates_rejected(self):
        with self.assertRaises(ValueError):forecast('TEST',bars(),Settings(),predictor=Predictor(),future_dates=bars().index[:15])


class TradeTests(unittest.TestCase):
    def setUp(self):
        self.settings=replace(Settings(),fee_bps=5,slippage_bps=5)
        self.confirm=[[100,107,99,106],[106,108,105,107]]

    def test_next_open_entry_with_costs(self):
        result=simulate_trade(signal(),path(self.confirm+[[108,116,107,115]]),self.settings)
        self.assertEqual(result['status'],'target')
        self.assertAlmostEqual(result['entry'],108*1.0005)
        self.assertAlmostEqual(result['exit'],115*.9995)
        expected=result['units']*(result['exit']*.9995-result['entry']*1.0005)
        self.assertAlmostEqual(result['pnl'],expected)

    def test_confirmation_bar_cannot_be_entry(self):
        result=simulate_trade(signal(),path(self.confirm),self.settings)
        self.assertEqual(result['status'],'no_trade')

    def test_ambiguous_bar_is_stop_first(self):
        result=simulate_trade(signal(),path(self.confirm+[[108,116,94,110]]),self.settings)
        self.assertEqual(result['status'],'stop')
        self.assertAlmostEqual(result['exit'],95*.9995)

    def test_known_opening_gap_has_priority(self):
        result=simulate_trade(signal(),path(self.confirm+[[108,110,107,109],[116,118,94,97]]),self.settings)
        self.assertEqual(result['status'],'target')
        self.assertAlmostEqual(result['exit'],116*.9995)

    def test_stop_gap_fills_at_open_not_stop(self):
        result=simulate_trade(signal(),path(self.confirm+[[108,110,107,109],[90,96,89,94]]),self.settings)
        self.assertEqual(result['status'],'stop')
        self.assertAlmostEqual(result['exit'],90*.9995)

    def test_extended_entry_is_skipped(self):
        result=simulate_trade(signal(),path(self.confirm+[[116,118,114,117]]),self.settings)
        self.assertEqual(result['status'],'no_trade')

    def test_failed_breakout_exits_next_open(self):
        result=simulate_trade(signal(),path(self.confirm+[[108,110,103,104],[100,120,99,118]]),self.settings)
        self.assertEqual(result['status'],'failed_breakout')
        self.assertAlmostEqual(result['exit'],100*.9995)

    def test_invalidation_before_entry_blocks_trade(self):
        result=simulate_trade(signal(),path([[100,110,94,106]]+self.confirm),self.settings)
        self.assertEqual(result['status'],'no_trade')

    def test_position_cannot_exceed_cash(self):
        result=simulate_trade(signal(),path(self.confirm+[[108,116,107,115]]),replace(self.settings,capital=1000,risk_pct=5))
        self.assertLessEqual(result['units']*result['entry']*1.0005,1000)


class MetricTests(unittest.TestCase):
    def test_errors_coverage_and_baseline(self):
        result={'last_close':100,'forecast':[{'date':'2025-11-03','median':102,'q10':99,'q90':103},{'date':'2025-11-04','median':104,'q10':100,'q90':105}]}
        actual=path([[100,102,99,101],[101,108,100,107]])
        comparison=compare(result,actual)
        self.assertEqual(comparison['metrics']['mae'],2)
        self.assertEqual(comparison['metrics']['baseline_mae'],4)
        self.assertEqual(comparison['metrics']['coverage_pct'],50)
        self.assertTrue(comparison['metrics']['direction_correct'])

    def test_flat_baseline_has_no_infinite_skill(self):
        result={'last_close':100,'forecast':[{'date':'2025-11-03','median':102,'q10':99,'q90':103}]}
        metrics=compare(result,path([[100,101,99,100]]))['metrics']
        self.assertIsNone(metrics['mae_skill_pct'])

    def test_no_future_is_pending_not_zero_error(self):
        result={'last_close':100,'forecast':[{'date':'2025-11-03','median':102,'q10':99,'q90':103}]}
        comparison=compare(result,bars(0))
        self.assertEqual(comparison['metrics'],{})
        self.assertFalse(comparison['complete'])


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'.cache').mkdir()
        self.frame=bars()
        with (self.root/'.cache/bars.pkl').open('wb') as f:pickle.dump({'TEST':(0,self.frame)},f)
        self.predictor=Predictor();self.service=ResearchService(self.root,self.predictor)
    def tearDown(self):
        self.service.close();self.temp.cleanup()

    def test_historical_forecast_never_sees_later_prices(self):
        origin=self.frame.index[100]
        frame=self.frame.copy();frame.loc[frame.index>origin,['open','high','low','close']]=900
        with (self.root/'.cache/bars.pkl').open('wb') as f:pickle.dump({'TEST':(0,frame)},f)
        result=self.service._forecast({'symbol':'TEST','asof':str(origin.date()),'scanner':'all'},replace(Settings(),lookback=60,horizon=4,samples=4),lambda *_:None,None)
        self.assertEqual(result['last_close'],100)
        self.assertTrue(all(c['df'].close.max()==100 for c in self.predictor.calls))
        self.assertEqual(result['comparison']['rows'][0]['actual'],900)

    def test_weekly_cut_happens_before_resampling(self):
        date=self.frame.index[100]
        out=self.service.market.bars('TEST','W',date)
        self.assertLessEqual(out.index.max(),date)

    def test_weekly_actuals_exclude_origin_week_and_unfinished_week(self):
        from kronos.service import future_observations
        frame=pd.DataFrame({'close':[1,2,3]},index=pd.to_datetime(['2025-01-10','2025-01-17','2025-01-20']))
        actual=future_observations(frame,'2025-01-08','W')
        self.assertEqual(list(actual.index),[pd.Timestamp('2025-01-17')])

    def test_backtest_replays_patterns_and_uses_disjoint_windows(self):
        seen=[]
        def detect(symbol,timeframe,asof,scanner):
            history=self.service.market.bars(symbol,timeframe,asof)
            seen.append(history.index[-1])
            return []
        self.service.market.detect=detect
        settings=replace(Settings(),lookback=60,horizon=4,samples=4)
        result=self.service._backtest({'symbol':'TEST','start':'2025-04-01','end':'2025-09-30','max_windows':3,'scanner':'all'},settings,lambda *_:None,None)
        self.assertEqual(len(result['windows']),3)
        self.assertEqual(len(seen),3)
        for i,w in enumerate(result['windows']):
            self.assertEqual(w['history'][-1]['date'],w['asof'])
            self.assertEqual(str(seen[i].date()),w['asof'])
            self.assertLess(w['asof'],w['forecast'][0]['date'])
            if i:self.assertLessEqual(result['windows'][i-1]['forecast'][-1]['date'],w['asof'])
        self.assertEqual(result['summary']['trades'],0)
        self.assertEqual(result['summary']['net_return_pct'],0)

    def test_bad_job_requests_fail_before_queueing(self):
        for request in ({'kind':'shell'},{'kind':'forecast','symbol':'UNKNOWN'},{'kind':'backtest','symbol':'TEST','start':'2025-05-01','end':'2025-04-01'}):
            with self.assertRaises(ValueError):self.service.submit(request)
        self.assertEqual(self.service.store.list(),[])

    def test_local_api_and_write_protection(self):
        server=create_server(self.service,0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            port=server.server_port
            conn=HTTPConnection('127.0.0.1',port)
            conn.request('GET','/api/bootstrap');response=conn.getresponse();data=json.loads(response.read())
            self.assertEqual(response.status,200)
            conn.request('POST','/api/settings',json.dumps(Settings().to_dict()),{'Content-Type':'application/json'})
            response=conn.getresponse();response.read();self.assertEqual(response.status,403)
            headers={'Content-Type':'application/json','X-Kronos-Token':data['token'],'Origin':'https://untrusted.example'}
            conn.request('POST','/api/settings',json.dumps(Settings().to_dict()),headers)
            response=conn.getresponse();response.read();self.assertEqual(response.status,403)
            headers.pop('Origin')
            conn.request('POST','/api/settings',json.dumps(replace(Settings(),horizon=8).to_dict()),headers)
            response=conn.getresponse();response.read();self.assertEqual(response.status,200)
            self.assertEqual(self.service.store.settings().horizon,8)
            conn.request('GET','/../requirements.txt');response=conn.getresponse();response.read();self.assertEqual(response.status,404)
            conn.request('GET','/api/health',headers={'Host':'untrusted.example'})
            response=conn.getresponse();response.read();self.assertEqual(response.status,403)
            conn.close()
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
