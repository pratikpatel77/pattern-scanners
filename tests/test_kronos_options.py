"""Options economics and safety checks. Synthetic quotes never reach the live desk."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pathlib
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from unittest.mock import Mock

import numpy as np
import pandas as pd

from kronos.options import OptionsDesk, preferences, liquid, classify
from kronos.options_journal import OptionsJournal, paper_stats
from kronos.options_market import LiveMarket, IST
from kronos.options_math import analyse_structure, bs, candidates, charges, entry, expiry_value, fee, fill, underlying_stop, reward_reasons
from kronos.storage import Store


def option(strike,kind='PE',qty=1,bid=5.,ask=5.1):
    return dict(symbol=f'TEST-{strike}-{kind}',underlying='TEST',strike=float(strike),type=kind,
        qty=qty,bid=bid,ask_price=ask,lot=100,expiry='2026-11-23',iv=25.,tick=.05,
        oi=100000,v=10000,bid_qty=1000,ask_qty=1000,quote_time=datetime.now().timestamp())


def forecast_value():
    paths=[]
    for end in np.linspace(990,1040,32):
        paths.append([dict(date=str(d.date()),open=float(s),high=float(s+3),low=float(s-3),close=float(s),volume=1.)
            for d,s in zip(pd.bdate_range('2026-11-02',periods=5),np.linspace(1000,end,5))])
    return dict(symbol='TEST',last_close=1000.,paths=paths,
        forecast=[dict(date=r['date'],median=r['close'],q10=r['low'],q90=r['high']) for r in paths[16]],
        summary=dict(median_end=1015.,bias='Upside bias',sample_count=32),metadata={})


class MathTests(unittest.TestCase):
    def test_reward_floors_reject_tiny_profit_and_poor_return(self):
        prefs=preferences({})
        self.assertTrue(reward_reasons(dict(stressed_mean_net=99,max_loss=500),prefs))
        self.assertTrue(reward_reasons(dict(stressed_mean_net=100,max_loss=2000),prefs))
        self.assertEqual(reward_reasons(dict(stressed_mean_net=100,max_loss=1000),prefs),[])
        self.assertTrue(reward_reasons(dict(stressed_mean_net=float('nan'),max_loss=1000),prefs))

    def test_high_scenario_success_cannot_hide_weak_reward_warning(self):
        prefs=preferences(dict(min_net_profit=100000.))
        legs=[option(970,qty=-1,bid=8,ask=8.1),option(950,qty=1,bid=3,ask=3.1)]
        plan=analyse_structure('Put credit spread','bullish',legs,forecast_value(),prefs,70)
        self.assertEqual(classify(plan['reasons']),'Research only')  # shown, flagged, needs acknowledgement
        self.assertIn('Stressed net profit below minimum',plan['reasons'])
        self.assertIn('Stressed net profit below minimum',OptionsDesk.entry_checks(plan)['warnings'])

    def test_best_ideas_always_shown_but_passing_ones_rank_first(self):
        passed=dict(name='Spread',score=.1,status='Paper candidate')
        failed=dict(name='Condor',score=99,status='Skip')
        self.assertEqual(OptionsDesk.top_ideas([failed]),[failed])
        self.assertEqual(OptionsDesk.top_ideas([failed,passed]),[passed,failed])

    def test_underlying_invalidation_stops_match_direction(self):
        f=forecast_value();f['summary']['atr']=20
        legs=[option(970,qty=-1),option(950)]
        bullish=underlying_stop('bullish',legs,f)
        bearish=underlying_stop('bearish',legs,f)
        neutral=underlying_stop('range',legs,f)
        self.assertEqual(bullish['lower'],980)
        self.assertIsNone(bullish['upper'])
        self.assertEqual(bearish['upper'],1020)
        self.assertIsNone(bearish['lower'])
        self.assertLess(neutral['lower'],1000)
        self.assertGreater(neutral['upper'],1000)

    def test_put_credit_expiry_math_and_costs(self):
        legs=[option(970,qty=-1,bid=8,ask=8.1),option(950,qty=1,bid=3,ask=3.1)]
        prefs=preferences({})
        c=analyse_structure('Put credit spread','bullish',legs,forecast_value(),prefs,70.)
        credit,cost=entry(legs,100,.01)
        expected_credit=fill(legs[0],-1)*100-fill(legs[1],1)*100
        self.assertAlmostEqual(credit,expected_credit)
        self.assertAlmostEqual(c['max_loss'],2000-credit+c['cost_estimate'],places=2)
        self.assertAlmostEqual(c['max_profit'],credit-c['cost_estimate'],places=2)
        self.assertAlmostEqual(c['breakevens'][0],970-(credit-c['cost_estimate'])/100,places=2)
        self.assertEqual(cost['brokerage'],0.)
        self.assertGreater(c['cost_estimate'],sum(cost.values()))

    def test_butterfly_and_condor_have_bounded_losses(self):
        legs=[option(950,qty=1),option(1000,qty=-2,bid=20,ask=20.1),option(1050,qty=1,bid=45,ask=45.1)]
        values=[expiry_value(legs,s,100) for s in (0,950,1000,1050,10000)]
        self.assertEqual(values,[0.,0.,5000.,0.,0.])
        # Independent brute-force check of exact piecewise max loss for unequal wings.
        legs[-1]['strike']=1075.
        plan=analyse_structure('Broken-wing butterfly','range',legs,forecast_value(),preferences({}),70)
        cash,_=entry(legs,100,.01)
        brute=min(cash+expiry_value(legs,s,100)-plan['cost_estimate'] for s in range(0,2001))
        self.assertAlmostEqual(plan['max_loss'],-brute,places=2)

    def test_unhedged_call_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'Unbounded'):
            analyse_structure('Bad','bearish',[option(1000,'CE',-1)],forecast_value(),preferences({}),50)

    def test_uncapped_profit_backspread_still_has_bounded_loss(self):
        legs=[option(1000,'CE',-1,25,25.1),option(1050,'CE',2,9,9.1)]
        plan=analyse_structure('Call backspread','bullish',legs,forecast_value(),preferences({}),50)
        self.assertIsNone(plan['max_profit'])
        self.assertGreater(plan['max_loss'],0)

    def test_future_prices_are_valued_before_expiry(self):
        self.assertGreater(bs(1000,1000,20,30,'PE'),0)
        self.assertEqual(bs(1000,1000,0,30,'PE'),0)
        self.assertAlmostEqual(bs(1000,1000,20,30,'CE')-bs(1000,1000,20,30,'PE'),
            1000-1000*np.exp(-.06*20/365),places=7)

    def test_liquidity_checks_all_sides_and_ratio_quantity(self):
        p=preferences({});o=option(970)
        self.assertTrue(liquid(o,p))
        self.assertFalse(liquid({**o,'bid_qty':50},p))
        self.assertFalse(liquid({**o,'qty':2,'bid_qty':150},p))
        self.assertFalse(liquid({**o,'bid':6},p))
        self.assertFalse(liquid({**o,'v':0},p))
        self.assertFalse(liquid({**o,'ask_price':10},p))

    def test_nonzero_friction_and_input_validation(self):
        for bad in ({'slippage':0},{'min_oi':float('nan')},{'samples':32.5},{'avoid_events':'yes'}):
            with self.assertRaises(ValueError):preferences(bad)
        c=charges(10000,20000)
        self.assertEqual(c['stt'],30.)
        self.assertAlmostEqual(c['stamp'],.3)
        self.assertAlmostEqual(c['exchange'],10.659)


class LiveDataTests(unittest.TestCase):
    def test_eod_history_keeps_only_completed_candles(self):
        market=LiveMarket()
        dates=pd.bdate_range(end='2026-10-30',periods=70)
        rows=[[int(d.tz_localize(IST).timestamp()),100,102,99,101,1000] for d in dates]
        with patch.object(market,'_get',return_value=dict(candles=rows)):
            with patch('kronos.options_market.clock',return_value=datetime(2026,10,30,15,45,tzinfo=IST)):
                before=market.eod_history('TEST')
                self.assertEqual(str(before.index[-1].date()),'2026-10-29')
            with patch('kronos.options_market.clock',return_value=datetime(2026,10,30,16,30,tzinfo=IST)):
                after=market.eod_history('TEST')
                self.assertEqual(str(after.index[-1].date()),'2026-10-30')
                self.assertEqual(after.close.iloc[-1],101)

    def test_depth_reads_fyers_d_and_rejects_stale_trades(self):
        now=datetime(2026,10,30,11,0,tzinfo=IST)
        market=LiveMarket()
        item=dict(ltt=now.timestamp(),ltp=1000,bids=[dict(price=999,volume=500)],ask=[dict(price=1000,volume=600)])
        with patch('kronos.options_market.clock',return_value=now),patch.object(market,'_get',return_value={'NSE:TEST-EQ':item}):
            self.assertEqual(market.depth('NSE:TEST-EQ')['bid'],999)
            # A strike that last traded 5 minutes ago is still tradable while its book is live and two-sided.
            item['ltt']-=300
            quote=market.depth('NSE:TEST-EQ')
            self.assertTrue(quote['fresh'])
            self.assertEqual(quote['trade_age_seconds'],300)
            item['bids']=[]
            with self.assertRaisesRegex(ValueError,'Fresh prices'):
                market.depth('NSE:TEST-EQ')
            preview=market.preview_depth('NSE:TEST-EQ')
            self.assertFalse(preview['fresh'])
            self.assertEqual(preview['ltp'],1000)
        closed=datetime(2026,10,30,16,0,tzinfo=IST)
        item.update(bids=[dict(price=999,volume=500)],ltt=closed.timestamp())
        with patch('kronos.options_market.clock',return_value=closed),patch.object(market,'_get',return_value={'NSE:TEST-EQ':item}):
            with self.assertRaisesRegex(ValueError,'Fresh prices'):
                market.depth('NSE:TEST-EQ')

    def test_master_maps_lot_expiry_and_excludes_indices(self):
        now=datetime(2026,10,30,11,0,tzinfo=IST)
        expiry=int(datetime(2026,11,23,15,30,tzinfo=IST).timestamp())
        rows=[]
        for symbol in ['TEST','NIFTY']:
            r=['']*21;r[3]='100';r[4]='0.05';r[8]=str(expiry);r[9]=f'NSE:{symbol}26NOV1000PE';r[13]=symbol;r[15]='1000';r[16]='PE'
            rows.append(','.join(r))
        cash=['']*14;cash[9]='NSE:TEST-EQ'
        market=LiveMarket()
        with patch('kronos.options_market.clock',return_value=now),patch.object(market,'_get',side_effect=[','.join(cash),'\n'.join(rows)]):
            self.assertEqual(market.universe(),['TEST'])
            self.assertEqual(market.contracts[0]['lot'],100)
            self.assertEqual(market.contracts[0]['expiry'],'2026-11-23')


class DefinedgeFallbackTests(unittest.TestCase):
    NOW = datetime(2026, 11, 2, 11, 0, tzinfo=IST)

    def quote(self, bid='19.45', ask='19.65'):
        return {'status': 'SUCCESS', 'ltp': '19.50', 'last_trade_time': '10:59:30', 'day_open': '18', 'day_high': '20',
                'day_low': '17', 'volume': '500', 'best_bid_price1': bid, 'best_ask_price1': ask,
                'best_bid_qty1': '950', 'best_ask_qty1': '950', 'best_bid_price2': '19.4', 'best_bid_qty2': '1900'}

    def test_definedge_quote_is_converted_to_the_fyers_depth_shape(self):
        from kronos.definedge_market import depth_item
        item = depth_item(self.quote(), self.NOW)
        self.assertEqual((item['ltp'], item['bids'][0], item['ask'][0]), (19.5, dict(price=19.45, volume=950.), dict(price=19.65, volume=950.)))
        self.assertEqual(len(item['bids']), 2)
        self.assertAlmostEqual(self.NOW.timestamp() - item['ltt'], 30)
        empty = depth_item({**self.quote(), 'best_bid_price1': '0', 'best_bid_price2': '', 'last_trade_time': '00:00:00'}, self.NOW)
        self.assertEqual((empty['bids'], empty['ltt']), ([], 0.))  # nothing invented

    def test_fyers_failure_switches_to_definedge_then_cools_down_before_retrying(self):
        market = LiveMarket()
        fallback = Mock()
        fallback.definedge_item.return_value = {'ltp': 19.5, 'ltt': self.NOW.timestamp() - 5,
            'bids': [dict(price=19.45, volume=950)], 'ask': [dict(price=19.65, volume=950)]}
        market.fallback = fallback
        with patch('kronos.options_market.clock', return_value=self.NOW), \
             patch.object(market, '_get', side_effect=ValueError('FYERS rate limit reached. Wait before refreshing.')) as fyers:
            first = market.depth('NSE:DLF26NOV700PE')
            self.assertEqual((first['source'], first['bid'], first['ask_price'], first['fresh']), ('Definedge', 19.45, 19.65, True))
            calls = fyers.call_count
            market.depth('NSE:DLF26NOV700PE')
            self.assertEqual(fyers.call_count, calls)  # FYERS is not hammered during the cool-down
            self.assertEqual(fallback.definedge_item.call_count, 2)
            market.fyers_down_until = 0.
            market.depth('NSE:DLF26NOV700PE')
            self.assertEqual(fyers.call_count, calls + 1)  # retried once the cool-down ends

    def test_fyers_is_used_first_and_without_a_fallback_the_error_stays(self):
        market = LiveMarket()
        item = dict(ltt=self.NOW.timestamp(), ltp=10, bids=[dict(price=9.9, volume=1)], ask=[dict(price=10, volume=1)])
        market.fallback = Mock()
        with patch('kronos.options_market.clock', return_value=self.NOW), patch.object(market, '_get', return_value={'S': item}):
            self.assertEqual(market.depth('S')['source'], 'FYERS')
            market.fallback.definedge_item.assert_not_called()
        bare = LiveMarket()
        with patch.object(bare, '_get', side_effect=ValueError('FYERS rate limit reached.')):
            with self.assertRaisesRegex(ValueError, 'rate limit'):
                bare.depth('S')

    def test_a_closed_market_is_never_turned_into_a_fresh_price_by_the_fallback(self):
        market = LiveMarket()
        closed = datetime(2026, 11, 2, 16, 0, tzinfo=IST)
        market.fallback = Mock()
        market.fallback.definedge_item.return_value = {'ltp': 10., 'ltt': closed.timestamp(),
            'bids': [dict(price=9.9, volume=1)], 'ask': [dict(price=10., volume=1)]}
        with patch('kronos.options_market.clock', return_value=closed), patch.object(market, '_get', side_effect=ValueError('down')):
            with self.assertRaisesRegex(ValueError, 'Fresh prices'):
                market.depth('S')

    def test_history_falls_back_to_definedge_daily_candles(self):
        market = LiveMarket()
        dates = pd.bdate_range(end='2026-10-30', periods=70)
        frame = pd.DataFrame(dict(open=100., high=102., low=99., close=101., volume=1000.), index=dates)
        market.fallback = Mock()
        market.fallback.definedge_daily.return_value = frame
        with patch('kronos.options_market.clock', return_value=datetime(2026, 10, 31, 16, 30, tzinfo=IST)), \
             patch.object(market, '_get', side_effect=ValueError('FYERS login expired')):
            self.assertEqual(len(market.history('TEST', None, completed=True)), 70)


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'db.sqlite3');self.j=OptionsJournal(self.store)
        oi_patch=patch.object(OptionsDesk,'oi_context',return_value=dict(available=True,bias='neutral',by_expiry={}))
        oi_patch.start();self.addCleanup(oi_patch.stop)
    def tearDown(self):self.tmp.cleanup()

    def test_atomic_risk_cap_duplicate_and_persistence(self):
        payload=dict(symbol='TEST',plan=dict(max_loss=1000))
        t=self.j.open('a','c',payload)
        with self.assertRaises(ValueError):self.j.open('b','c',payload)
        self.j.open('b','d',{**payload,'symbol':'OTHER'})
        self.j.open('e','f',{**payload,'symbol':'THIRD','plan':dict(max_loss=10**7)})  # no capital or loss-budget cap
        self.j.update(t['id'],{**payload,'mark':dict(net_pnl=20)},True)
        reopened=OptionsJournal(Store(self.store.path))
        self.assertEqual(reopened.paper(t['id'])['payload']['mark']['net_pnl'],20)
        with self.assertRaises(ValueError):self.j.update(t['id'],payload,True)
        with self.assertRaises(ValueError):self.j.open('a','c',payload)

    def test_forecast_record_survives_without_trade(self):
        key=self.j.forecast(forecast_value())
        self.assertEqual(len(self.j.forecasts()),1)
        self.j.score(key,dict(direction_correct=True))
        self.j.score(key,dict(direction_correct=False))
        self.assertTrue(self.j.forecasts()[0]['outcome']['direction_correct'])

    def test_close_charges_and_opposite_side_quotes(self):
        legs=[option(970,qty=-1,bid=8,ask=8.1),option(950,qty=1,bid=3,ask=3.1)]
        prefs=preferences({});cash,cost=entry(legs,100,.01)
        p=dict(symbol='TEST',plan=dict(max_loss=1500,expiry='2099-11-23'),legs=legs,prefs=prefs,
               entry_credit=cash,entry_charges=cost,exit_by='2099-01-01')
        t=self.j.open('a','b',p)
        fake=SimpleNamespace(depth=lambda s:{**next(l for l in legs if l['symbol']==s),'quote_time':datetime.now().timestamp()})
        desk=OptionsDesk(SimpleNamespace(store=self.store),fake)
        marked=desk.mark_paper(dict(id=t['id'],close=True))
        expected=cash+fill(legs[1],-1)*100-fill(legs[0],1)*100-sum(cost.values())-fee(fill(legs[0],1)*100,fill(legs[1],-1)*100,len(legs),prefs['brokerage'])
        self.assertAlmostEqual(marked['payload']['mark']['net_pnl'],expected,places=2)
        self.assertLess(expected,0)  # immediate round trip must lose to spread, slippage and charges

    @patch('kronos.options.market_open',lambda:True)
    def test_full_analysis_records_forecast_and_blocks_unknown_checks(self):
        f=forecast_value()
        chain=[]
        for k in range(850,1151,10):
            for kind in ('CE','PE'):
                value=bs(1000,k,24,25,kind)
                o=option(k,kind,bid=max(.1,value*.99),ask=max(.2,value*1.01))
                chain.append(o)
        bars=pd.DataFrame(dict(open=1000.,high=1005.,low=995.,close=1000.,volume=100000.),
                          index=pd.bdate_range('2026-01-01',periods=180))
        market=Mock()
        market.universe.return_value=['TEST']
        market.depth.return_value=dict(ltp=1000.,quote_time=datetime.now().timestamp())
        market.history.return_value=bars;market.chain.return_value=chain
        desk=OptionsDesk(SimpleNamespace(store=self.store,predictor=None),market)
        with (patch('kronos.options.forecast',return_value=f),
              patch.object(desk,'iv_percentile',return_value=dict(value=None,source='Missing')),
              patch.object(desk,'events',return_value=dict(checked=False,events=[],coverage='Missing'))):
            result=desk.analyse('TEST',preferences({}))
        self.assertGreater(len(result['candidates']),0)
        self.assertIn('oi_context',result)
        self.assertTrue(all('oi' in c for c in result['candidates']))
        self.assertEqual(len(self.j.forecasts()),1)
        self.assertTrue(all('Event calendar needs review' in c['reasons'] for c in result['candidates']))
        # Unknown context makes ideas research-only (visible, acknowledgement needed), never a clean pass.
        self.assertNotIn('Paper candidate',{c['status'] for c in result['candidates']})
        if result['status']=='Review':
            self.assertIn('Research only',{c['status'] for c in result['candidates']})
        with self.assertRaises(ValueError):desk.open_paper(dict(analysis=result['id'],candidate=result['candidates'][0]['id']))
        market.eod_history.return_value=bars
        with (patch('kronos.options.market_open',return_value=False),
              patch('kronos.options.forecast',return_value=f),
              patch.object(desk,'iv_percentile',return_value=dict(value=None,source='Missing')),
              patch.object(desk,'events',return_value=dict(checked=False,events=[],coverage='Missing'))):
            eod=desk.analyse('TEST',preferences({}))
        self.assertEqual(eod['data_mode'],'EOD')
        self.assertFalse(eod['forecast']['metadata']['partial_day'])
        self.assertGreater(len(eod['candidates']),0)
        with self.assertRaisesRegex(ValueError,'EOD analysis'):
            desk.open_paper(dict(analysis=eod['id'],candidate=eod['candidates'][0]['id'],accept_warnings=True))
        market.chain.side_effect=ValueError('Option chain unavailable')
        with patch('kronos.options.forecast',return_value=f):
            failed=desk.analyse('TEST',preferences({}))
        self.assertEqual(failed['status'],'Data needed')
        self.assertEqual(failed['reason'],'Option chain unavailable')
        self.assertEqual(failed['forecast'],f)
        self.assertTrue(desk.compact(failed)['has_forecast'])

    def test_entry_refreshes_quotes_and_rejects_forged_or_old_ideas(self):
        desk=OptionsDesk(SimpleNamespace(store=self.store),Mock())
        a=self.j.save_analysis(dict(symbol='TEST',candidates=[]))
        with self.assertRaisesRegex(ValueError,'did not pass'):
            desk.open_paper(dict(analysis=a['id'],candidate='invented'))
        old=self.j.save_analysis(dict(symbol='TEST',forecast_created='2020-01-01T00:00:00+00:00',candidates=[]))
        with self.assertRaisesRegex(ValueError,'older than'):
            desk.open_paper(dict(analysis=old['id'],candidate='invented'))

    def test_eligible_paper_entry_uses_server_plan_and_refreshed_depth(self):
        f=forecast_value()
        for path in f['paths']:
            path[-1].update(close=1100.,high=1103.)
        prefs=preferences({})
        legs=[option(970,qty=-1,bid=8,ask=8.1),option(950,qty=1,bid=3,ask=3.1)]
        plan=analyse_structure('Put credit spread','bullish',legs,f,prefs,70)
        self.assertEqual(plan['status'],'Paper candidate')
        plan['id']='candidate'
        a=self.j.save_analysis(dict(symbol='TEST',prefs=prefs,spot=1000,forecast=f,forecast_id='f',
            forecast_end='2026-11-06',ivp=dict(value=70),candidates=[plan]))
        def quote(symbol):
            if symbol=='NSE:TEST-EQ':return dict(ltp=1000.,quote_time=datetime.now().timestamp())
            return {**next(l for l in legs if l['symbol']==symbol),'quote_time':datetime.now().timestamp()}
        market=Mock();market.depth.side_effect=quote
        desk=OptionsDesk(SimpleNamespace(store=self.store),market)
        with patch.object(desk,'events',return_value=dict(checked=True,events=[])):
            trade=desk.open_paper(dict(analysis=a['id'],candidate='candidate',entry_credit=999999))
        self.assertEqual(trade['status'],'open')
        self.assertEqual(market.depth.call_count,3)
        self.assertEqual(trade['payload']['entry_credit'],plan['entry_credit'])
        self.assertEqual(trade['payload']['legs'][0]['entry_fill'],fill(legs[0],-1))

    def test_pattern_list_contains_stocks_without_analysis(self):
        service=SimpleNamespace(store=self.store,setups=lambda:[
            dict(symbol='AAA',label='Darvas',timeframe='D'),dict(symbol='BBB',label='Cup',timeframe='W'),
            dict(symbol='AAA',label='Stage',timeframe='D')])
        rows=OptionsDesk(service,Mock()).stock_ideas()
        self.assertEqual([r['symbol'] for r in rows],['AAA','BBB'])
        self.assertEqual(rows[0]['patterns'],['Darvas · D','Stage · D'])
        self.assertEqual(len(self.j.analyses()),0)

    def test_bearish_patterns_are_named_and_marked(self):
        service=SimpleNamespace(store=self.store,setups=lambda:[
            dict(symbol='AAA',scanner='cup_bear',label='Handle forming',timeframe='W',direction='short'),
            dict(symbol='BBB',scanner='cup',label='Handle forming',timeframe='W'),
            dict(symbol='BBB',scanner='darvas_bear',label='Inside box',timeframe='D',direction='short')])
        rows={r['symbol']:r for r in OptionsDesk(service,Mock()).stock_ideas()}
        self.assertEqual(rows['AAA']['patterns'],['Bearish Inverted Cup & Handle · W'])
        self.assertEqual(rows['AAA']['bias'],'bearish')
        self.assertEqual(rows['BBB']['bias'],'mixed')

    def test_fresh_pattern_report_does_not_reintroduce_old_csv_patterns(self):
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder);(directory/'scanner.html').write_text('updated')
            market=Mock();market.saved_setups.return_value=[dict(symbol='OLD',label='Old',timeframe='D')]
            service=SimpleNamespace(store=self.store,directory=directory,market=market,
                setups=lambda:[dict(symbol='NEW',label='Current',timeframe='D')])
            rows=OptionsDesk(service,Mock()).stock_ideas()
            self.assertEqual([r['symbol'] for r in rows],['NEW'])
            market.saved_setups.assert_not_called()

    def test_top_two_are_distinct_and_prefer_eligible_plans(self):
        rows=[dict(name='Put spread',score=1,status='Skip'),dict(name='Put spread',score=.5,status='Paper candidate'),
              dict(name='Put spread',score=.4,status='Paper candidate'),dict(name='Condor',score=.3,status='Paper candidate')]
        chosen=OptionsDesk.top_ideas(rows)
        self.assertEqual([c['name'] for c in chosen],['Put spread','Condor'])
        self.assertEqual(chosen[0]['score'],.5)

    def manual_desk(self):
        prefs=preferences({});f=forecast_value()
        for p in f['paths']:p[-1].update(close=1100.,high=1103.)
        legs=[option(970,qty=-1,bid=8,ask=8.1),option(950,qty=1,bid=3,ask=3.1)]
        plan=analyse_structure('Put credit spread','bullish',legs,f,prefs,70);plan['id']='original'
        a=self.j.save_analysis(dict(symbol='TEST',prefs=prefs,spot=1000,forecast=f,forecast_id='f',
            forecast_end='2026-11-06',expiry='2026-11-23',ivp=dict(value=70),candidates=[plan]))
        chain=legs+[option(960,bid=4,ask=4.1)]
        market=Mock();market.chain.return_value=chain
        def quote(symbol):
            if symbol=='NSE:TEST-EQ':return dict(ltp=1000.,quote_time=datetime.now().timestamp())
            return {**next(l for l in chain if l['symbol']==symbol),'ltp':12.34,'expiry':'1795428600','quote_time':datetime.now().timestamp()}
        market.depth.side_effect=quote
        desk=OptionsDesk(SimpleNamespace(store=self.store),market)
        desk.events=Mock(return_value=dict(checked=True,events=[]))
        return desk,a,chain

    def test_oi_is_rechecked_on_edit_and_paper_entry(self):
        desk,a,chain=self.manual_desk()
        context=dict(available=False,bias='unavailable',issues=['Test stale OI'])
        with patch.object(desk,'oi_context',return_value=context):
            result=desk.reprice(dict(analysis=a['id'],candidate='original',strikes=[970,950]))
            self.assertEqual(result['plan']['oi']['bias'],'unavailable')
            self.assertEqual(self.j.analysis(result['analysis'])['oi_context'],context)
            with self.assertRaisesRegex(ValueError,'OI context'):
                desk.open_paper(dict(analysis=a['id'],candidate='original'))
            self.assertEqual(self.j.paper(),[])

    def test_edited_strikes_use_fresh_ltp_and_persist_original(self):
        desk,a,chain=self.manual_desk()
        result=desk.reprice(dict(analysis=a['id'],candidate='original',strikes=[970,960],ltp=999999))
        plan=result['plan'];draft=self.j.analysis(result['analysis'])
        self.assertEqual(plan['legs'][1]['strike'],960)
        self.assertEqual(plan['legs'][0]['ltp'],12.34)
        self.assertEqual(plan['legs'][0]['expiry'],'2026-11-23')
        self.assertNotEqual(plan['entry_credit'],a['candidates'][0]['entry_credit'])
        self.assertEqual(self.j.analysis(a['id'])['candidates'][0]['legs'][1]['strike'],950)
        self.assertEqual(draft['parent_analysis'],a['id'])
        self.assertTrue(draft['is_draft'])
        self.assertEqual(len(self.j.analyses()),1)
        self.assertEqual(desk.market.depth.call_count,3)

    def test_invalid_strikes_and_contract_reuse_are_rejected(self):
        desk,a,chain=self.manual_desk()
        for strikes in ([970],[970,999],[970,970],[950,970],[970,float('nan')],[970,'960']):
            with self.assertRaises(ValueError):desk.reprice(dict(analysis=a['id'],candidate='original',strikes=strikes))
        self.assertEqual(desk.market.depth.call_count,0)

    def test_manual_liquidity_failure_blocks_paper_entry(self):
        desk,a,chain=self.manual_desk()
        chain[-1]['bid_qty']=0
        result=desk.reprice(dict(analysis=a['id'],candidate='original',strikes=[970,960]))
        self.assertEqual(result['plan']['status'],'Skip')
        self.assertIn('A selected leg fails liquidity or depth checks',result['plan']['reasons'])
        with self.assertRaises(ValueError):desk.open_paper(dict(analysis=result['analysis'],candidate=result['plan']['id']))

    def test_refresh_does_not_reset_forecast_age(self):
        desk,a,chain=self.manual_desk()
        old=self.j.save_analysis({**a,'forecast_created':'2020-01-01T00:00:00+00:00'})
        result=desk.reprice(dict(analysis=old['id'],candidate='original',strikes=[970,960]))
        # The idea stays reviewable, but its age blocks entry.
        self.assertIn('Forecast is older than 10 minutes. Analyse this stock again',result['plan']['checks']['blockers'])
        with self.assertRaisesRegex(ValueError,'older than'):
            desk.open_paper(dict(analysis=result['analysis'],candidate=result['plan']['id'],accept_warnings=True))
        self.assertEqual(self.j.analysis(result['analysis'])['forecast_created'],old['forecast_created'])

    def test_research_paper_requires_acknowledgement_and_records_warnings(self):
        desk,a,chain=self.manual_desk()
        a['ivp']['value']=None
        a['candidates'][0]['status']='Skip'
        a['candidates'][0]['reasons']=['IV percentile needs a dated source']
        a=self.j.save_analysis(a)
        with self.assertRaisesRegex(ValueError,'Review'):
            desk.open_paper(dict(analysis=a['id'],candidate='original'))
        t=desk.open_paper(dict(analysis=a['id'],candidate='original',accept_warnings=True))
        self.assertTrue(t['payload']['research'])
        self.assertIn('IV percentile needs a dated source',t['payload']['accepted_warnings'])
        self.assertEqual(t['status'],'open')

    def test_research_ack_cannot_bypass_liquidity_or_events(self):
        for failure in ('liquidity','event'):
            desk,a,chain=self.manual_desk()
            if failure=='liquidity':chain[0]['bid_qty']=0
            if failure=='event':desk.events.return_value=dict(checked=True,events=[dict(date='2026-11-02',title='Results')])
            a=self.j.save_analysis(a)
            with self.assertRaises(ValueError):
                desk.open_paper(dict(analysis=a['id'],candidate='original',accept_warnings=True))
        self.assertEqual(self.j.paper(),[])

    def test_stale_depth_keeps_display_payoff_but_blocks_entry(self):
        desk,a,chain=self.manual_desk()
        for l in chain:l['fresh']=False
        result=desk.reprice(dict(analysis=a['id'],candidate='original',strikes=[970,960]))
        self.assertGreater(len(result['plan']['payoff']),100)
        self.assertGreater(len(result['plan']['holding_payoff']),100)
        self.assertTrue(result['plan']['checks']['blockers'])
        self.assertEqual(result['plan']['legs'][0]['ltp'],12.34)

    def test_status_classes_separate_quality_warnings_and_timing(self):
        self.assertEqual(classify([]),'Paper candidate')
        self.assertEqual(classify(['Chain preview: refresh leg depth before entry']),'Paper candidate')
        self.assertEqual(classify(['EOD analysis: run a live forecast before paper entry']),'Paper candidate')
        self.assertEqual(classify(['IV percentile needs a dated source']),'Research only')
        self.assertEqual(classify(['Stressed net profit below minimum','Negative average outcome under IV stress']),'Research only')
        self.assertEqual(classify(['IV percentile needs a dated source','A selected leg fails liquidity or depth checks']),'Skip')
        self.assertEqual(classify(['Scheduled event during trade period']),'Skip')
        rows=[dict(name='Condor',score=9,status='Research only'),dict(name='Spread',score=1,status='Paper candidate'),
              dict(name='Fly',score=99,status='Skip')]
        self.assertEqual([c['name'] for c in OptionsDesk.top_ideas(rows)],['Spread','Condor'])
        # Nothing is hidden: with only blocked ideas, the best two are still returned.
        self.assertEqual([c['name'] for c in OptionsDesk.top_ideas([rows[2],dict(name='Cal',score=5,status='Skip')])],['Fly','Cal'])

    def test_research_only_saved_idea_is_visible_and_needs_acknowledgement(self):
        desk,a,chain=self.manual_desk()
        a['ivp']['value']=None
        a['candidates'][0]['reasons']=['IV percentile needs a dated source']
        a=self.j.save_analysis(a)
        self.assertEqual(desk.compact(a)['best']['status'],'Research only')
        with self.assertRaisesRegex(ValueError,'Review'):
            desk.open_paper(dict(analysis=a['id'],candidate='original'))
        a['candidates'][0]['reasons']=['A selected leg fails liquidity or depth checks']
        a=self.j.save_analysis(a)
        self.assertEqual(desk.compact(a)['best']['status'],'Skip')  # still shown for review
        with self.assertRaisesRegex(ValueError,'liquidity'):
            desk.open_paper(dict(analysis=a['id'],candidate='original',accept_warnings=True))

    def test_repeated_reprice_keeps_one_small_draft_and_entry_uses_parent_forecast(self):
        desk,a,chain=self.manual_desk()
        first=desk.reprice(dict(analysis=a['id'],candidate='original',strikes=[970,960]))
        second=desk.reprice(dict(analysis=a['id'],candidate='original',strikes=[970,950]))
        self.assertEqual(first['analysis'],second['analysis'])
        with self.store.connect() as db:
            rows=db.execute("SELECT payload FROM options_analyses WHERE json_extract(payload,'$.is_draft')=1").fetchall()
        self.assertEqual(len(rows),1)
        self.assertNotIn('"paths"',rows[0][0])
        self.assertEqual(self.j.analysis(second['analysis'])['forecast'],a['forecast'])
        t=desk.open_paper(dict(analysis=second['analysis'],candidate=second['plan']['id']))
        self.assertEqual(t['status'],'open')
        self.assertEqual(t['analysis'],a['id'])

    def test_paper_trades_are_permanent_and_restorable(self):
        import sqlite3
        t=self.j.open('a','c',dict(symbol='TEST',plan=dict(max_loss=10,name='X')))
        self.j.update(t['id'],{**t['payload'],'mark':dict(time='t',net_pnl=5)},True)
        with self.assertRaisesRegex(sqlite3.DatabaseError,'permanent'):
            with self.store.connect() as db: db.execute('DELETE FROM options_paper')
        self.assertTrue((self.j.backup_dir/'paper_trades.json').exists())
        self.assertEqual(len(self.j.paper()),1)
        # Simulate losing the database file entirely: the next start restores trades from the backup.
        for suffix in ('','-wal','-shm'):
            pathlib.Path(str(self.store.path)+suffix).unlink(missing_ok=True)
        restored=OptionsJournal(Store(self.store.path)).paper(t['id'])
        self.assertEqual(restored['status'],'closed')
        self.assertEqual(restored['payload']['mark']['net_pnl'],5)
        self.assertEqual(restored['payload']['marks'][0]['net_pnl'],5)

    def test_old_drafts_are_pruned_unless_a_trade_used_them(self):
        old=self.j.save_analysis(dict(symbol='TEST',is_draft=True,candidates=[]),key='draft-old')
        kept=self.j.save_analysis(dict(symbol='TEST',is_draft=True,candidates=[]),key='draft-kept')
        with self.store.connect() as db:
            db.execute("UPDATE options_analyses SET created='2020-01-01T00:00:00+00:00' WHERE id IN ('draft-old','draft-kept')")
        self.j.open('a','c',dict(symbol='TEST',plan=dict(max_loss=10),priced_analysis='draft-kept'))
        self.assertEqual(self.j.prune_drafts(),1)
        self.assertEqual(self.j.analysis('draft-kept')['id'],'draft-kept')
        with self.assertRaises(ValueError):self.j.analysis(old['id'])

    def test_analysis_list_uses_small_summaries(self):
        desk,a,chain=self.manual_desk()
        rows=self.j.analyses()
        self.assertEqual(len(rows),1)
        self.assertNotIn('forecast',rows[0])
        self.assertNotIn('payoff',rows[0]['candidates'][0])
        row=desk.compact(rows[0])
        self.assertEqual(row['bias'],'Upside bias')
        self.assertAlmostEqual(row['expected_pct'],1.5)
        self.assertEqual(row['best']['name'],'Put credit spread')

    def test_mark_all_records_history_and_continues_after_one_failure(self):
        desk,a,chain=self.manual_desk()
        t=desk.open_paper(dict(analysis=a['id'],candidate='original'))
        self.j.open('x','y',dict(symbol='GONE',plan=dict(max_loss=10,expiry='2020-01-01'),legs=[],prefs={}))
        result=desk.mark_all()
        self.assertEqual(result['marked'],1)
        self.assertEqual(result['errors'][0]['symbol'],'GONE')
        desk.mark_all()
        self.assertEqual(len(self.j.paper(t['id'])['payload']['marks']),2)

    def test_paper_stats_win_rate_drawdown_and_groups(self):
        def trade(pnl,name,day,research=False,status='closed'):
            return dict(status=status,created=f'2026-10-0{day}T04:00:00+00:00',updated=f'2026-10-0{day+1}T04:00:00+00:00',
                payload=dict(symbol='T',research=research,plan=dict(name=name,max_loss=1000),mark=dict(net_pnl=pnl)))
        s=paper_stats([trade(300,'Spread',1),trade(-500,'Condor',2,True),trade(100,'Spread',3),trade(50,'Spread',4,status='open')])
        c=s['closed']
        self.assertEqual((c['count'],c['wins'],c['win_rate']),(3,2,66.7))
        self.assertEqual(c['realised'],-100)
        self.assertEqual(c['max_drawdown'],500)
        self.assertEqual(c['profit_factor'],.8)
        self.assertEqual([p['cumulative'] for p in s['curve']],[300,-200,-100])
        self.assertEqual(s['open']['unrealised'],50)
        spread=next(g for g in s['groups'] if g['name']=='Spread')
        self.assertEqual((spread['trades'],spread['pnl']),(2,400))
        self.assertEqual(paper_stats([])['closed']['win_rate'],None)

    def test_rules_persist_across_sessions_and_old_keys_are_ignored(self):
        desk=OptionsDesk(SimpleNamespace(store=self.store,active=None,setups=lambda:[]),Mock())
        self.j.save_settings(dict(capital=1e13,risk_pct=1,portfolio_pct=5,slippage=.02,horizon=7))
        first=desk.bootstrap()['settings']
        self.assertEqual((first['slippage'],first['horizon']),(.02,7))
        self.assertNotIn('capital',first)
        self.j.save_settings({**self.j.settings(),'min_oi':5000})
        again=OptionsDesk(SimpleNamespace(store=Store(self.store.path),active=None,setups=lambda:[]),Mock()).bootstrap()['settings']
        self.assertEqual((again['min_oi'],again['slippage'],again['horizon']),(5000,.02,7))
        self.assertEqual(self.j.settings(),again)  # defaults and cleaned values are stored on disk

    def test_underlying_stop_breach_takes_priority_without_inventing_exit(self):
        desk,a,chain=self.manual_desk()
        t=desk.open_paper(dict(analysis=a['id'],candidate='original'))
        def quote(symbol):
            if symbol=='NSE:TEST-EQ':return dict(ltp=980.,quote_time=datetime.now().timestamp())
            return {**next(l for l in chain if l['symbol']==symbol),'quote_time':datetime.now().timestamp()}
        desk.market.depth.side_effect=quote
        marked=desk.mark_paper(dict(id=t['id']))
        self.assertIn('EXIT NOW',marked['payload']['exit_signal'])
        self.assertEqual(marked['status'],'open')
        self.assertEqual(marked['payload']['mark']['underlying'],980)


if __name__=='__main__':unittest.main()
