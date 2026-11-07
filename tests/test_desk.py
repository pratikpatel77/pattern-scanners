"""Fast options desk: chain shaping, maths and custom-leg paper trades. No network."""
from datetime import timedelta
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from kronos.desk import FastDesk, implied_vol, max_pain, payoff_summary, years_left
from kronos.options_journal import OptionsJournal
from kronos.options_market import clock
from kronos.options_math import bs
from kronos.storage import Store


def fyers_chain(spot=1000.):
    rows = [dict(option_type='', strike_price=-1, symbol='NSE:TEST-EQ', ltp=spot, ltpch=5, ltpchp=.5, bid=spot - .1, ask=spot, fp=spot + 2)]
    for k in (960, 980, 1000, 1020, 1040):
        for kind in ('CE', 'PE'):
            price = round(bs(spot, k, 20, 25, kind), 2) + .05
            rows.append(dict(option_type=kind, strike_price=k, symbol=f'NSE:TEST{k}{kind}', ltp=price, ltpch=0, ltpchp=0,
                             bid=price - .05, ask=price + .05, oi=1000 * (k / 10 if kind == 'PE' else 100 - k / 20), oich=10, volume=5))
    ts = int((clock() + timedelta(days=20)).timestamp())
    return dict(optionsChain=rows, callOi=10000, putOi=8000, indiavixData=dict(ltp=14.2),
                expiryData=[dict(date=(clock() + timedelta(days=20)).strftime('%d-%m-%Y'), expiry=str(ts))])


def master():
    expiry = str((clock() + timedelta(days=20)).date())
    return {f'NSE:TEST{k}{kind}': dict(symbol=f'NSE:TEST{k}{kind}', underlying='TEST', expiry=expiry, lot=100,
                                        strike=float(k), type=kind, tick=.05)
            for k in (960, 980, 1000, 1020, 1040) for kind in ('CE', 'PE')}


class MathTests(unittest.TestCase):
    def test_implied_vol_round_trips_black_scholes(self):
        price = bs(1000, 1020, 20, 30, 'CE')
        self.assertAlmostEqual(implied_vol(price, 1000, 1020, 20, 'CE'), 30, delta=.05)
        self.assertIsNone(implied_vol(10, 1000, 900, 20, 'CE'))  # below intrinsic: no IV

    def test_payoff_summary_of_bull_put_spread(self):
        legs = [dict(type='PE', strike=1000., qty=-1, lot=100), dict(type='PE', strike=980., qty=1, lot=100)]
        s = payoff_summary(legs, 1000, 800., 20.)
        self.assertEqual(s['max_profit'], 780.)
        self.assertEqual(s['max_loss'], 1220.)          # 20 wide * 100 - 800 credit + 20 costs
        self.assertFalse(s['unlimited_loss'])
        self.assertAlmostEqual(s['breakevens'][0], 992.2, delta=.2)

    def test_naked_short_call_is_flagged_unlimited(self):
        s = payoff_summary([dict(type='CE', strike=1000., qty=-1, lot=100)], 1000, 500., 0.)
        self.assertTrue(s['unlimited_loss'])

    def test_max_pain_and_expiry_floor(self):
        rows = [dict(strike=90, CE=dict(oi=0), PE=dict(oi=100)), dict(strike=100, CE=dict(oi=50), PE=dict(oi=50)),
                dict(strike=110, CE=dict(oi=100), PE=dict(oi=0))]
        self.assertEqual(max_pain(rows), 100)
        self.assertGreater(years_left(clock().timestamp() - 10), 0)


class FillRuleTests(unittest.TestCase):
    def test_paper_fills_use_worst_immediate_price_with_no_extra_slippage(self):
        from kronos.options_math import fill
        quote = dict(ltp=100., bid=99., ask_price=101., tick=.05)
        self.assertEqual(fill(quote, 1), 101.)          # buy pays the ask
        self.assertEqual(fill(quote, -1), 99.)          # sell / short receives the bid
        self.assertEqual(fill(quote, 1, .05), 101.)     # an old slippage argument is ignored
        self.assertEqual(fill(dict(bid=7.4, ask_price=7.5, tick=.05), 1), 7.5)


class AccountRealismTests(unittest.TestCase):
    def test_large_orders_walk_the_book_and_thin_books_are_refused(self):
        from kronos.options_math import book_fill
        q = dict(bid=99., ask_price=101., tick=.05, ask=[dict(price=101., volume=500), dict(price=101.5, volume=500)],
                 bids=[dict(price=99., volume=300), dict(price=98.5, volume=1000)])
        self.assertEqual(book_fill(q, 1, 500)[0], 101.)          # fits at the best ask
        self.assertEqual(book_fill(q, 1, 1000)[0], 101.25)       # half at 101, half at 101.5
        self.assertEqual(book_fill(q, -1, 600)[0], 98.75)        # sells hit 99 then 98.5
        with self.assertRaises(ValueError):
            book_fill(q, 1, 1500)

    def test_brokerage_per_order_with_gst(self):
        from kronos.options_math import charges
        c = charges(10000., 10000., orders=2, per_order=20.)
        self.assertEqual(c['brokerage'], 40.)
        self.assertAlmostEqual(c['gst'], (40 + 20000 * .0003553 + 20000 * .000001) * .18)
        self.assertEqual(charges(100., 100.)['brokerage'], 0.)  # planning estimates unchanged


class ChainTests(unittest.TestCase):
    def desk(self):
        market = Mock(); market.by_symbol = master(); market._get.return_value = fyers_chain()
        service = SimpleNamespace(options=SimpleNamespace(market=market), prices=Mock())
        return FastDesk(service), market

    def test_chain_is_shaped_and_shared_between_quick_polls(self):
        desk, market = self.desk()
        c = desk.chain('TEST')
        self.assertEqual(c['spot']['ltp'], 1000.)
        self.assertEqual(c['atm'], 1000.)
        self.assertEqual(c['lot'], 100)
        self.assertEqual(c['pcr'], .8)
        self.assertAlmostEqual(c['rows'][2]['CE']['iv'], 25, delta=1.5)
        desk.chain('TEST')
        self.assertEqual(market._get.call_count, 1)

    def test_chain_failure_serves_last_good_chain_marked_stale(self):
        desk, market = self.desk()
        desk.chain('TEST')
        desk.cache.clear()
        market._get.side_effect = ValueError('FYERS rate limit reached.')
        desk.service.prices.cash_quote.return_value = dict(ltp=1003.)
        c = desk.chain('TEST')
        self.assertTrue(c['stale'])
        self.assertEqual(c['spot']['ltp'], 1003.)
        self.assertEqual(c['spot']['source'], 'Definedge')

    def test_bad_symbol_rejected(self):
        desk, _ = self.desk()
        with self.assertRaises(ValueError):
            desk.chain('../etc')


class PaperTests(unittest.TestCase):
    def test_custom_legs_fill_at_bid_ask_and_record(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(f'{d}/r.sqlite3')
            market = Mock(); market.by_symbol = master()
            quote = lambda s: dict(bid=10., ask_price=10.2, bid_qty=1000, ask_qty=1000, ltp=10.1, quote_time=clock().timestamp(), source='FYERS')
            import threading
            options = SimpleNamespace(market=market, journal=OptionsJournal(store), action_lock=threading.RLock(),
                                      _depths=lambda symbols: [quote(s) for s in symbols])
            market.depth.return_value = dict(ltp=1000.)
            desk = FastDesk(SimpleNamespace(options=options))
            trade = desk.open_paper(dict(symbol='TEST', name='Bull put spread',
                                         legs=[dict(symbol='NSE:TEST1000PE', lots=-1), dict(symbol='NSE:TEST980PE', lots=1)]))
            p = trade['payload']
            self.assertEqual(trade['status'], 'open')
            self.assertEqual([l['entry_fill'] for l in p['legs']], [10.0, 10.2])  # sell at the bid, buy at the ask
            self.assertEqual(p['plan']['name'], 'Bull put spread')
            self.assertFalse(p['plan']['unlimited_loss'])
            # The stock-at-zero loss includes both entry costs and the disclosed exit reserve.
            self.assertAlmostEqual(p['plan']['max_loss'], 2020.+p['plan']['cost_estimate'], places=2)
            self.assertGreater(p['plan']['exit_cost_reserve'], 0.)
            with self.assertRaises(ValueError):    # one open trade per stock
                desk.open_paper(dict(symbol='TEST', legs=[dict(symbol='NSE:TEST1000CE', lots=1)]))

    def test_rejects_foreign_or_oversized_legs(self):
        market = Mock(); market.by_symbol = master()
        desk = FastDesk(SimpleNamespace(options=SimpleNamespace(market=market)))
        with self.assertRaises(ValueError):
            desk.open_paper(dict(symbol='OTHER', legs=[dict(symbol='NSE:TEST1000PE', lots=1)]))
        with self.assertRaises(ValueError):
            desk.open_paper(dict(symbol='TEST', legs=[dict(symbol='NSE:TEST1000PE', lots=500)]))


class AITests(unittest.TestCase):
    def test_saved_forecast_and_best_ideas_are_returned_without_running_the_model(self):
        from kronos.options import OptionsDesk
        with tempfile.TemporaryDirectory() as d:
            journal = OptionsJournal(Store(f'{d}/r.sqlite3'))
            forecast = dict(asof='2026-10-30', last_close=1000., summary=dict(bias='Upside bias', median_end=1020., sample_count=32),
                            history=[dict(date=f'2026-10-{i:02d}', close=990. + i) for i in range(1, 31)],
                            forecast=[dict(date='2026-11-02', median=1005., q10=990., q90=1015.)], paths=[])
            legs = [dict(strike=980., type='PE', qty=-1, ltp=8.), dict(strike=960., type='PE', qty=1, ltp=4.)]
            idea = dict(id='a', oi=dict(version=1,bias='neutral'), calculation_version=2, name='Put credit spread', bias='bullish', status='Paper candidate', score=5, expiry='2026-11-23',
                        entry_credit=400., max_loss=1600., legs=legs, reasons=['Chain preview: refresh leg depth before entry'],
                        underlying_stop=dict(lower=985., upper=None))
            journal.save_analysis(dict(symbol='TEST', forecast=forecast, candidates=[idea], data_mode='EOD', lower=980., upper=1030.))
            options = SimpleNamespace(journal=journal, top_ideas=OptionsDesk.top_ideas)
            desk = FastDesk(SimpleNamespace(options=options, active=None))
            a = desk.ai('TEST')['analysis']
            self.assertEqual(a['forecast']['summary']['bias'], 'Upside bias')
            self.assertEqual(len(a['forecast']['history']), 30)
            self.assertEqual(a['ideas'][0]['legs'][0]['strike'], 980.)
            self.assertEqual(a['ideas'][0]['reasons'], [])   # internal preview notes are hidden
            self.assertIsNone(desk.ai('OTHER')['analysis'])



class PatternLevelTests(unittest.TestCase):
    def desk(self, setups):
        store = SimpleNamespace(settings=lambda: SimpleNamespace(target_r=1.0, min_target_pct=5.0))
        return FastDesk(SimpleNamespace(store=store, setups=lambda: setups))

    def test_targets_follow_the_pattern_direction(self):
        desk = self.desk([
            dict(symbol='TEST', scanner='cup_bear', direction='short', timeframe='W', close=100, pivot=95, support=110, score=70),
            dict(symbol='TEST', scanner='cup', timeframe='D', close=100, pivot=104, support=90, score=80),
            dict(symbol='OTHER', scanner='cup', timeframe='D', close=10, pivot=11, support=9, score=90)])
        rows = desk.pattern_levels('TEST')
        self.assertEqual([r['direction'] for r in rows], ['long', 'short'])   # best score first
        self.assertEqual(rows[0]['target'], 118.0)       # 104 + (104 - 90)
        self.assertEqual(rows[1]['target'], 80.0)        # 95 - (110 - 95)
        self.assertEqual(rows[1]['title'], 'Inverted Cup & Handle')

    def test_same_pattern_on_both_timeframes_is_one_row(self):
        row = dict(symbol='TEST', scanner='stage_bear', direction='short', close=100, pivot=95, support=110)
        rows = self.desk([{**row, 'timeframe': 'D', 'score': 60}, {**row, 'timeframe': 'W', 'score': 70}]).pattern_levels('TEST')
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['timeframe'], rows[0]['score']), ('D+W', 70))

    def test_minimum_move_and_bad_levels(self):
        desk = self.desk([
            dict(symbol='TEST', scanner='darvas_bear', direction='short', timeframe='D', close=100, pivot=99, support=100.5, score=60),
            dict(symbol='TEST', scanner='stage_bear', direction='short', timeframe='D', close=100, pivot=101, support=99, score=50)])
        rows = desk.pattern_levels('TEST')
        self.assertEqual(len(rows), 1)                   # stop below breakdown is not a bearish setup
        self.assertEqual(rows[0]['target'], 94.05)       # at least 5% below the breakdown level


if __name__ == '__main__':
    unittest.main()


class PreflightTests(unittest.TestCase):
    """The pre-entry check must reach the same advice the monitor gives after entry."""

    def desk(self, analysis):
        from kronos.options_market import clock as now
        expiry = str((now() + timedelta(days=20)).date())
        by_symbol = {f'NSE:TEST{k}CE': dict(symbol=f'NSE:TEST{k}CE', underlying='TEST', expiry=expiry, lot=100, strike=float(k), type='CE', tick=.05)
                     for k in (1005, 1010)}
        journal = SimpleNamespace(latest_forecast_id=lambda s: 'a1' if analysis else None, analysis=lambda k: analysis, settings=lambda: {})
        options = SimpleNamespace(journal=journal, market=SimpleNamespace(universe=lambda: ['TEST'], by_symbol=by_symbol, contracts=[]),
                                  events=lambda s, e: dict(upcoming=[]))
        desk = FastDesk(SimpleNamespace(options=options))
        quotes = {'NSE:TEST-EQ': dict(ltp=1000.), 'NSE:TEST1005CE': dict(ltp=30., bid=29.9, ask=30.1), 'NSE:TEST1010CE': dict(ltp=27., bid=26.9, ask=27.1)}
        desk.ltp = lambda symbols: dict(quotes=quotes)
        return desk

    def request(self):
        return dict(symbol='TEST', legs=[dict(symbol='NSE:TEST1005CE', lots=-1), dict(symbol='NSE:TEST1010CE', lots=2)])

    def test_no_forecast_is_reported_instead_of_guessing(self):
        r = self.desk(None).preflight(self.request())
        self.assertIsNone(r['action'])
        self.assertIn('Run one', r['message'])

    def test_backspread_with_a_bullish_forecast_passes(self):
        from test_position_watch import analysis
        r = self.desk(analysis(1090.)).preflight(self.request())
        self.assertNotIn(r['action'], ('Adjust', 'Exit'), r['reasons'])
        self.assertFalse(r['stale'])

    def test_bearish_forecast_against_a_bullish_structure_warns_before_entry(self):
        from test_position_watch import analysis
        r = self.desk(analysis(930., bias='Downside bias')).preflight(self.request())
        self.assertIn(r['action'], ('Adjust', 'Exit'))
        self.assertTrue(r['reasons'])

    def test_rejects_foreign_legs(self):
        bad = dict(symbol='TEST', legs=[dict(symbol='NSE:OTHER1005CE', lots=1)])
        with self.assertRaises(ValueError):
            self.desk(None).preflight(bad)
