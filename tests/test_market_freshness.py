"""Fresh quotes, renewed credentials, and historical isolation regressions."""
from datetime import datetime
import os
from pathlib import Path
import pickle
import tempfile
import unittest
from unittest.mock import Mock, patch
import pandas as pd
from datalayer import config
from kronos.data import MarketData
from kronos.definedge_market import DefinedgeMarket
from kronos.options_market import LiveMarket, IST


class FreshnessTests(unittest.TestCase):
    def test_renewed_env_is_used_without_restart_and_external_env_wins(self):
        with tempfile.TemporaryDirectory() as folder:
            env = Path(folder)/'.env'
            env.write_text('TOKEN=old\nEXTERNAL=file\n', encoding='utf-8')
            with patch.dict(os.environ, {'EXTERNAL':'real'}, clear=True), patch.object(config,'ENV_FILE',env), patch.object(config,'_LEGACY',[]), patch.object(config,'_loaded',False), patch.object(config,'_managed',{}), patch.object(config,'_stamps',None):
                self.assertEqual(config.get('TOKEN'),'old')
                original_stat = env.stat()
                env.write_text('TOKEN=new\nEXTERNAL=file\n', encoding='utf-8')
                os.utime(env, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
                self.assertEqual(config.get('TOKEN'),'new')  # same length and timestamp, different token
                env.write_text('TOKEN=renewed-value\nEXTERNAL=changed\n', encoding='utf-8')
                self.assertEqual(config.get('TOKEN'),'renewed-value')
                self.assertEqual(config.get('EXTERNAL'),'real')

    def test_fyers_quotes_are_uncached_and_per_symbol_errors_stay_missing(self):
        market=LiveMarket()
        now=datetime(2026,10,30,12,tzinfo=IST)
        row=dict(n='NSE:SBIN-EQ',s='ok',v=dict(lp=100,tt=now.timestamp(),chp=1.))
        with patch('kronos.options_market.clock',return_value=now), patch.object(market,'_get',return_value=[row,dict(n='NSE:BAD-EQ',s='error')]) as get:
            first=market.quotes(['SBIN','BAD']);row['v']['lp']=101
            second=market.quotes(['SBIN','BAD'])
            self.assertEqual(first['quotes']['SBIN']['ltp'],100)
            self.assertEqual(second['quotes']['SBIN']['ltp'],101)
            self.assertEqual(first['missing'],['BAD'])
            self.assertTrue(first['quotes']['SBIN']['fresh'])
            self.assertEqual(get.call_count,2)

    def test_definedge_time_without_date_never_becomes_a_fresh_trade(self):
        market=DefinedgeMarket();market.instruments={'SBIN':'3045'}
        response=Mock(status_code=200)
        response.json.return_value=dict(status='SUCCESS',ltp='1026',last_trade_time='10:46:57')
        with patch.object(market,'ready'),patch('kronos.definedge_market.requests.get',return_value=response):
            quote=market.definedge_quotes(['SBIN'])['quotes']['SBIN']
        self.assertEqual(quote['ltp'],1026)
        self.assertIsNone(quote['quote_time'])
        self.assertFalse(quote['fresh'])
        self.assertEqual(quote['reported_trade_time'],'10:46:57')

    def test_unavailable_symbol_is_not_reported_as_global_network_failure(self):
        market=LiveMarket();response=Mock(status_code=422)
        with patch.object(market.session,'get',return_value=response),patch.object(config,'get',return_value='test'):
            with self.assertRaisesRegex(ValueError,'no market data for this symbol'):
                market._get('https://api-t1.fyers.in/data/history',auth=True)

    def test_primary_fyers_prices_and_definedge_fallback(self):
        market=DefinedgeMarket()
        quote=dict(ltp=101,quote_time=123,fetched_at='2026-10-30')
        with patch.object(market,'preview_depth',return_value=quote),patch.object(market,'definedge_quotes') as fallback:
            result=market.quotes(['SBIN'])
            self.assertEqual(result['source'],'FYERS')
            self.assertEqual(result['quotes']['SBIN']['timestamp_kind'],'trade')
            fallback.assert_not_called()
        with patch.object(market,'preview_depth',side_effect=ValueError('login expired')),patch.object(market,'definedge_quotes',return_value={'source':'Definedge'}) as fallback:
            self.assertEqual(market.quotes(['SBIN'])['source'],'Definedge')
            fallback.assert_called_once_with(['SBIN'])

    def test_completed_history_refresh_preserves_original_cache_and_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'.cache').mkdir()
            dates=pd.bdate_range(end='2026-10-29',periods=100)
            old=pd.DataFrame(dict(open=100.,high=102.,low=99.,close=101.,volume=1000.),index=dates)
            path=root/'.cache/bars.pkl';path.write_bytes(pickle.dumps({'SBIN':(0,old)}))
            original=path.read_bytes();market=MarketData(root)
            recent=old.tail(10).copy();recent.loc[pd.Timestamp('2026-10-30')]=[101,106,100,105,2000]
            provider=Mock();provider.daily_bars.return_value=recent
            market.refresh_symbol('SBIN',provider,end=datetime(2026,10,30).date())
            self.assertEqual(market.daily('SBIN').close.iloc[-1],105)
            self.assertEqual(market.daily('SBIN','2026-10-29').close.iloc[-1],101)
            self.assertEqual(MarketData(root).daily('SBIN').close.iloc[-1],105)
            self.assertEqual(path.read_bytes(),original)

    def test_failed_refresh_is_explicit_and_keeps_dated_saved_candles(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'.cache').mkdir()
            dates=pd.bdate_range(end='2026-10-29',periods=100)
            frame=pd.DataFrame(dict(open=100.,high=102.,low=99.,close=101.,volume=1000.),index=dates)
            (root/'.cache/bars.pkl').write_bytes(pickle.dumps({'SBIN':(0,frame)}))
            provider=Mock();provider.daily_bars.side_effect=ValueError('Definedge login failed')
            market=MarketData(root)
            context=market.context('SBIN',provider=provider)
            self.assertEqual(context['asof'],'2026-10-29')
            self.assertIn('login failed',context['refresh_error'])
            provider.reset_mock()
            market.context('SBIN',asof='2026-10-29',provider=provider)
            provider.daily_bars.assert_not_called()


if __name__=='__main__':unittest.main()
