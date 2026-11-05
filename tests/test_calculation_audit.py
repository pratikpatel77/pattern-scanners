"""Independent regression cases for the November calculation audit. No network."""
import copy
from dataclasses import replace
from datetime import timedelta
import unittest

import numpy as np
import pandas as pd

from analysis import analyse, measure
from datalayer.timeframe import to_weekly
from scanners import stage, darvas, cup_handle
from kronos.desk import implied_vol, payoff_summary
from kronos.engine import forecast
from kronos.options import preferences
from kronos.options_math import analyse_structure, book_fill, bs, model_exit, charges
from kronos.options_market import clock
from kronos.options_journal import paper_stats
from kronos.position_watch import evaluate
from kronos.settings import Settings
from test_analysis import make_bars, make_signal
from test_kronos_options import option, forecast_value
from test_kronos_research import bars, signal
from test_position_watch import trade, analysis, PUT_SPREAD


class PatternAudit(unittest.TestCase):
    def test_missing_delivery_does_not_become_low_weekly_delivery(self):
        frame=pd.DataFrame(dict(open=100.,high=102.,low=99.,close=101.,volume=1000.,deliv_qty=500.),
                           index=pd.bdate_range('2026-11-02',periods=5))
        self.assertEqual(to_weekly(frame).deliv_pct.iloc[0],50.)
        frame.loc[frame.index[-1],'deliv_qty']=np.nan
        weekly=to_weekly(frame)
        self.assertTrue(np.isnan(weekly.deliv_pct.iloc[0]))
        self.assertTrue(np.isnan(weekly.deliv_qty.iloc[0]))
        self.assertEqual(weekly.volume.iloc[0],5000.)

    def test_bad_delivery_quantity_cannot_be_scored(self):
        frame=make_bars().tail(5)
        frame['deliv_qty']=frame.volume*2
        self.assertTrue(to_weekly(frame).deliv_pct.isna().all())

    def test_distribution_is_red_for_a_bullish_setup(self):
        frame=make_bars(accumulation=False)
        result=analyse(make_signal(frame),frame)
        self.assertEqual(result['tone'],'bad')

    def test_no_overhead_volume_is_zero_not_missing(self):
        frame=make_bars(delivery=False)
        frame.loc[frame.index[-1],['close','high']]=[110.,111.]
        self.assertEqual(measure(make_signal(frame),frame)['overhead_supply'],0.)

    def test_all_scanners_reject_corrupt_candles(self):
        good=make_bars()
        for detector in (stage,darvas,cup_handle):
            for field,value in (('close',np.nan),('low',200.),('volume',-1.)):
                frame=good.copy();frame.loc[frame.index[-1],field]=value
                with self.subTest(scanner=detector.NAME,field=field),self.assertRaises(ValueError):
                    detector.scan('TEST',frame,'D')
            with self.assertRaises(ValueError):detector.scan('TEST',good.iloc[::-1],'D')


class OptionAudit(unittest.TestCase):
    def test_short_put_risk_includes_zero_stock_price(self):
        value=payoff_summary([dict(type='PE',strike=1000.,qty=-1,lot=100)],1000.,500.,20.)
        self.assertEqual(value['max_loss'],99520.)
        self.assertEqual(value['max_profit'],480.)
        self.assertEqual(value['breakevens'],[995.2])

    def test_unbounded_call_does_not_report_a_finite_loss_cap(self):
        value=payoff_summary([dict(type='CE',strike=100.,qty=-1,lot=100)],100.,500.,0.)
        self.assertTrue(value['unlimited_loss'])
        self.assertIsNone(value['max_loss'])

    def test_tail_breakeven_can_be_far_outside_plot_range(self):
        value=payoff_summary([dict(type='CE',strike=100.,qty=1,lot=1)],100.,-1000.,0.)
        self.assertEqual(value['breakevens'],[1100.])

    def test_european_put_iv_below_immediate_intrinsic(self):
        price=bs(90.,100.,90,10.,'PE')
        self.assertLess(price,10.)
        self.assertAlmostEqual(implied_vol(price,90.,100.,90, 'PE'),10.,places=2)
        self.assertIsNone(implied_vol(float('nan'),90.,100.,90,'PE'))

    def test_books_sorted_and_unknown_quantity_refused(self):
        q=dict(bid=9.,ask_price=10.,ask=[dict(price=11.,volume=100),dict(price=10.,volume=100)])
        self.assertEqual(book_fill(q,1,150)[0],10.3333)
        with self.assertRaises(ValueError):book_fill(dict(bid=9.,ask_price=10.),1,100)
        with self.assertRaises(ValueError):book_fill(q,1,0)

    def test_strategy_pays_brokerage_on_both_sides(self):
        legs=[option(970,qty=-1,bid=8,ask=8.1),option(950,bid=3,ask=3.1)]
        free=analyse_structure('Spread','bullish',legs,forecast_value(),preferences(dict(brokerage=0)),70)
        paid=analyse_structure('Spread','bullish',legs,forecast_value(),preferences(dict(brokerage=20)),70)
        self.assertEqual(paid['entry_charges']['brokerage'],40.)
        self.assertAlmostEqual(free['scenario_mean_net']-paid['scenario_mean_net'],94.4,places=2)
        self.assertAlmostEqual(paid['max_loss']-free['max_loss'],94.4,places=2)

    def test_stop_before_recovery_is_not_counted_as_hold_to_end(self):
        legs=[option(1000,'CE',1,bid=24.9,ask=25),option(1050,'CE',-1,bid=8,ask=8.1)]
        f=forecast_value(); f['summary']['atr']=20.
        for path in f['paths']:
            for row in path:row.update(open=1000.,low=995.,high=1100.,close=1100.)
        untouched=analyse_structure('Spread','bullish',legs,f,preferences({}),50)
        for path in f['paths']:path[0].update(low=970.)
        stopped=analyse_structure('Spread','bullish',legs,f,preferences({}),50)
        self.assertLess(stopped['scenario_mean_net'],untouched['scenario_mean_net'])
        self.assertEqual(stopped['scenario_profit_pct'],0.)

    def test_invalid_paths_or_exit_at_expiry_are_rejected(self):
        legs=[option(970,qty=-1,bid=8,ask=8.1),option(950,bid=3,ask=3.1)]
        for damage in ('empty','nan','bounds','date','expiry'):
            f=forecast_value();ls=copy.deepcopy(legs)
            if damage=='empty': f['paths']=[]
            elif damage=='nan': f['paths'][0][0]['close']=float('nan')
            elif damage=='bounds': f['paths'][0][0]['high']=1.
            elif damage=='date': f['paths'][0].pop()
            else:
                for l in ls:l['expiry']=f['forecast'][-1]['date']
            with self.subTest(damage=damage),self.assertRaises(ValueError):
                analyse_structure('Spread','bullish',ls,f,preferences({}),50)

    def test_model_exit_matches_cash_flows_and_costs(self):
        leg=option(1000,'CE',1,bid=20,ask=22)
        price=(bs(1020,1000,10,25,'CE')-1)*.99
        expected=price*100-sum(charges(0,price*100,1,20).values())
        self.assertAlmostEqual(model_exit([leg],1020,10,.01,20),expected)

    def test_journal_never_adds_unlimited_loss_as_a_finite_risk(self):
        t=trade(PUT_SPREAD);t['status']='open';t['payload']['plan']['max_loss']=None
        self.assertIsNone(paper_stats([t])['open']['risk'])


class ForecastAudit(unittest.TestCase):
    def test_median_bands_and_shares_use_individual_paths(self):
        class Samples:
            max_context=512
            def predict_samples(self,**kwargs):
                paths=[pd.DataFrame(dict(open=v,high=v+1,low=v-1,close=v,volume=1.),
                                    index=pd.DatetimeIndex(kwargs['y_timestamp'])) for v in (90.,100.,110.,140.)]
                return paths[0],paths
        result=forecast('TEST',bars(),replace(Settings(),samples=4,batch_size=4,horizon=2),predictor=Samples(),include_paths=True)
        self.assertEqual(result['summary']['median_end'],105.)
        self.assertEqual(result['summary']['upside_rate'],50.)
        self.assertAlmostEqual(result['summary']['end_q10'],93.)
        self.assertAlmostEqual(result['summary']['end_q90'],131.)

    def test_mismatched_pattern_is_rejected_before_inference(self):
        with self.assertRaisesRegex(ValueError,'does not match'):
            forecast('OTHER',bars(),Settings(),signal=signal())

    def test_old_or_beyond_exit_paths_do_not_produce_hold(self):
        t=trade(PUT_SPREAD);t['payload']['exit_by']=str(clock().date()+timedelta(days=1))
        a=analysis(1030.)
        for path in a['forecast']['paths']:
            for i,row in enumerate(path): row['date']=str(clock().date()+timedelta(days=2+i))
        result=evaluate(t,a,1000.,None)
        self.assertEqual(result['action'],'Watch')
        self.assertIsNone(result['stats']['profit_pct'])

    def test_today_event_is_not_treated_as_missing(self):
        event=dict(events=[dict(date=str(clock().date()),title='Results',days_away=0)])
        self.assertEqual(evaluate(trade(PUT_SPREAD),analysis(1030.),1000.,None,events=event)['action'],'Adjust')

    def test_entry_board_needs_current_quotes_and_checked_events(self):
        from test_entry_board import row,forecast as board_forecast,one
        for quotes,f in (({},board_forecast()),({'AAA':dict(ltp=99.5,fresh=False)},board_forecast()),
                         ({'AAA':dict(ltp=99.5,fresh=True)},board_forecast(checked=False))):
            self.assertEqual(one([row()],{'AAA':f},quotes)['verdict'],'watch')

    def test_expired_and_future_dated_forecasts_cannot_confirm_entry(self):
        from test_entry_board import forecast as board_forecast,TODAY
        from kronos.entry_board import kronos_view
        f=board_forecast();f['forecast_end']='2026-11-04'
        self.assertFalse(kronos_view('long',f,100.,TODAY)['fresh'])
        self.assertFalse(kronos_view('long',board_forecast(asof='2026-11-06'),100.,TODAY)['fresh'])


if __name__=='__main__': unittest.main()
