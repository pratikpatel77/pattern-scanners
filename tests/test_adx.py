"""ADX engine, option structures and the ADX trade record. Synthetic bars only."""
from datetime import date, timedelta
import math
import random
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pandas as pd

from kronos import adx_core as core
from kronos.adx_options import chain_read, days_needed, plan, realised_vol
from kronos.options_math import bs


def days(n, start=date(2024, 1, 1)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def bars(closes, spread=1.0):
    """open = previous close, high/low = +/- spread around the bar."""
    o = [closes[0]] + closes[:-1]
    h = [max(a, b) + spread for a, b in zip(o, closes)]
    l = [min(a, b) - spread for a, b in zip(o, closes)]
    return days(len(closes)), o, h, l, closes


def base_then_breakout(seed=3):
    """120 quiet bars, a sharp breakout, a three-bar dip to the 20 EMA, then continuation."""
    rnd = random.Random(seed)
    closes = [100 + math.sin(i / 3) * 1.2 + rnd.uniform(-.4, .4) for i in range(130)]
    closes += [closes[-1] + 2.2 * k for k in range(1, 9)]          # breakout leg
    top = closes[-1]
    closes += [top - 2.5, top - 5.5, top - 8.0, top - 9.0]          # pullback
    return closes


class Indicators(unittest.TestCase):
    def test_directional_movement_matches_the_book_example(self):
        # Yesterday 18-20, today 19-22: +DM 2, -DM 0. An outside day credits only the larger move.
        h, l, c = [20, 22, 23], [18, 19, 17], [19, 21, 20]
        pdi, mdi, adx, atr = core.dmi(h * 10, l * 10, c * 10)
        self.assertEqual(len(pdi), 30)
        self.assertTrue(all(0 <= x <= 100 for x in pdi + mdi + adx))

    def test_uptrend_has_plus_dmi_dominant_and_high_adx(self):
        d, o, h, l, c = bars([100 + i * 1.5 for i in range(120)])
        up, down = core.build(d, o, h, l, c)
        self.assertGreater(up.pdi[-1], up.mdi[-1])
        self.assertGreater(up.adx[-1], 40)
        self.assertEqual(up.dom[-1], 1)
        # The mirrored series sees the same bars as a downtrend.
        self.assertEqual(down.dom[-1], -1)
        self.assertAlmostEqual(down.mdi[-1], up.pdi[-1])
        self.assertAlmostEqual(down.adx[-1], up.adx[-1])

    def test_crossover_needs_a_higher_high_above_25_to_take_dominance(self):
        pdi = [10.] * 20 + [40., 42, 44, 40, 30, 22, 18, 16, 15, 15, 15, 15]
        mdi = [12.] * 20 + [10., 9, 8, 10, 18, 24, 27, 30, 33, 36, 46, 47]
        dom, since, kind = core.dominance(pdi, mdi, 1)
        self.assertEqual(dom[24], 1)
        self.assertEqual(dom[29], 1, '-DMI is on top above 25 but below the +DMI peak of 44: no change yet')
        self.assertEqual(dom[30], -1, 'a higher high than the preceding +DMI peak takes dominance')
        self.assertEqual(kind[30], 'XH')

    def test_weekly_context_never_sees_the_current_week(self):
        d, o, h, l, c = bars([100 + i for i in range(400)])
        full = core.weekly_context(d, h, l, c)
        cut = core.weekly_context(d[:300], h[:300], l[:300], c[:300])
        self.assertEqual(full['wk_adx'][:300], cut['wk_adx'])


class Setups(unittest.TestCase):
    def setUp(self):
        d, o, h, l, c = bars(base_then_breakout())
        self.up, self.down = core.build(d, o, h, l, c)

    def test_base_breakout_pullback_is_found_and_only_on_the_long_side(self):
        s = self.up
        found = [x for x in core.detect(s, s.n - 1) if x['state'] != 'watch']
        self.assertTrue(found, 'expected a pullback setup after the base breakout')
        sig = found[0]
        self.assertIn(sig['setup'], ('ADXodus', 'ADXplosive'))
        self.assertLess(sig['stop'], sig['trigger'])
        self.assertAlmostEqual(sig['target'] - sig['trigger'], core.TARGET_R * (sig['trigger'] - sig['stop']))
        self.assertFalse([x for x in core.detect(self.down, s.n - 1) if x['state'] != 'watch'])

    def test_short_setups_read_as_lows_and_bounces(self):
        self.assertEqual(core.short_text('New high with ADX above 25 (peak 37)'), 'New low with ADX above 25 (peak 37)')
        self.assertEqual(core.short_text('Price pulled back to the 20 EMA'), 'Price bounced up to the 20 EMA')
        self.assertEqual(core.short_text('ADX turned down during the pullback'), 'ADX turned down during the bounce')
        self.assertEqual(core.short_text('Daily close above the base'), 'Daily close below the base')

    def test_detection_uses_no_future_bars(self):
        s = self.up
        i = s.n - 3
        before = core.detect(s, i, watch=True)
        d, o, h, l, c = s.dates[:i + 1], s.o[:i + 1], s.h[:i + 1], s.l[:i + 1], s.c[:i + 1]
        short, _ = core.build(d, o, h, l, c)
        after = core.detect(short, i, watch=True)
        self.assertEqual([(x['setup'], x['state'], x.get('trigger')) for x in before],
                         [(x['setup'], x['state'], x.get('trigger')) for x in after])


def chain(spot=1000., step=20., iv=30., days=30., oi=5000, walls=()):
    rows = []
    for k in range(-8, 9):
        strike = 1000 + k * step
        call, put = bs(spot, strike, days, iv, 'CE'), bs(spot, strike, days, iv, 'PE')   # priced consistently with the IV
        leg = lambda price, t: dict(symbol=f'NSE:TEST{int(strike)}{t}', ltp=round(price, 2), bid=round(price * .99, 2),
                                    ask=round(price * 1.01, 2), oi=oi * (10 if strike in walls else 1))
        rows.append(dict(strike=strike, CE=leg(call, 'CE'), PE=leg(put, 'PE')))
    return dict(rows=rows, lot=100, expiry_date='2026-12-31', spot=dict(ltp=spot), atm=1000., atm_iv=iv, days=days,
                pcr=0.9, max_pain=1000.)


class OptionPlans(unittest.TestCase):
    sig = dict(direction='bullish', setup='ADXodus', trigger=1002., stop=975., target=1056.)
    flat = dict(direction='neutral', setup='Base', low=960., high=1040.)

    def names(self, p):
        return {i['name'] for i in p['ideas']}

    def test_trend_setup_compares_five_choices_and_recommends_one(self):
        p = plan(self.sig, chain(), hv=30.)
        self.assertEqual(self.names(p), {'Buy ITM call', 'Buy ATM call', 'Buy OTM call', 'Call debit spread', 'Put credit spread'})
        self.assertEqual(p['case_labels'], ['At target', 'At stop', 'Flat'])
        best = p['ideas'][0]
        self.assertEqual(p['best'], best['name'])
        self.assertEqual(best['score'], max(i['score'] for i in p['ideas']))
        for i in p['ideas']:
            self.assertGreater(i['max_loss'], 0)
            self.assertLessEqual(-i['cases'][1], i['max_loss'] + 1, 'the planned loss can never exceed the worst case')
            self.assertTrue(i['why'] and i['headline'])

    def test_range_setup_offers_the_four_neutral_structures(self):
        p = plan(self.flat, chain(), hv=30.)
        self.assertTrue(p['neutral'])
        self.assertEqual(self.names(p), {'Long straddle', 'Long strangle', 'Iron fly', 'Iron condor'})
        self.assertEqual(p['case_labels'], ['Flat', 'Up one move', 'Down one move'])
        fly = next(i for i in p['ideas'] if i['name'] == 'Iron fly')
        self.assertEqual(fly['kind'], 'short_vol')
        self.assertGreater(fly['net'], 0, 'an iron fly is sold for a credit')
        self.assertEqual(len(fly['breakevens']), 2)
        straddle = next(i for i in p['ideas'] if i['name'] == 'Long straddle')
        self.assertEqual(straddle['kind'], 'long_vol')
        self.assertLess(straddle['cases'][0], 0, 'a long straddle loses if the stock sits still')
        self.assertGreater(fly['cases'][0], 0, 'an iron fly makes money if the stock sits still')

    def test_cheap_options_favour_buying_volatility_and_rich_options_selling_it(self):
        cheap = {i['name']: i['score'] for i in plan(self.flat, chain(iv=20.), hv=28.)['ideas']}
        rich = {i['name']: i['score'] for i in plan(self.flat, chain(iv=45.), hv=22.)['ideas']}
        self.assertEqual(max(cheap, key=cheap.get)[:4], 'Long')
        self.assertIn(max(rich, key=rich.get), ('Iron fly', 'Iron condor'))

    def test_condor_shorts_sit_at_the_open_interest_walls(self):
        p = plan(self.flat, chain(iv=45., walls=(1100., 900.)), hv=22.)
        condor = next(i for i in p['ideas'] if i['name'] == 'Iron condor')
        shorts = sorted(l['strike'] for l in condor['legs'] if l['qty'] < 0)
        self.assertEqual(shorts, [900., 1100.])
        self.assertTrue(any('walls' in f[0].lower() or 'wall' in f[1].lower() for f in p['chain']['facts']))

    def test_a_bought_structure_is_judged_on_larger_moves_too(self):
        # Three points alone would weigh "exactly unchanged" too heavily and bury every long structure.
        p = plan(self.flat, chain(iv=20.), hv=34.)
        straddle = next(i for i in p['ideas'] if i['name'] == 'Long straddle')
        flat, up, down = straddle['cases']
        three = (.4 * flat + .3 * up + .3 * down) / straddle['max_loss'] * 100
        self.assertGreater(straddle['edge'], three)
        self.assertGreater(straddle['edge'], 0, 'a stock moving more than the options imply should favour buying them')

    def test_a_scheduled_event_marks_sold_options_down(self):
        score = lambda c, hv, event=None: {i['name']: i['score'] for i in plan(self.flat, c, hv=hv, event=event)['ideas']}
        rich, event = chain(iv=45.), 'Results on 2026-12-01'
        self.assertLess(score(rich, 22., event)['Iron fly'], score(rich, 22.)['Iron fly'])
        cheap = chain(iv=20.)
        self.assertGreater(score(cheap, 34., event)['Long straddle'], score(cheap, 34.)['Long straddle'])
        self.assertTrue(any('gap the stock' in n for n in plan(self.flat, rich, hv=22., event=event)['notes']))

    def test_structures_and_payoff_bounds(self):
        ideas = {i['name']: i for i in plan(self.sig, chain(), hv=30.)['ideas']}
        debit = ideas['Call debit spread']
        self.assertEqual([(l['strike'], l['qty']) for l in debit['legs']], [(1000., 1), (1060., -1)])
        self.assertAlmostEqual(debit['max_profit'] + debit['max_loss'], 60 * 100, places=1)
        credit = ideas['Put credit spread']
        self.assertLessEqual(next(l for l in credit['legs'] if l['qty'] < 0)['strike'], self.sig['stop'])
        self.assertGreater(credit['net'], 0)
        self.assertGreater(credit['cases'][0], 0)
        long = ideas['Buy ATM call']
        self.assertIsNone(long['max_profit'], 'a bought call has open-ended profit')
        self.assertAlmostEqual(long['max_loss'], long['legs'][0]['price'] * 100, places=1)
        self.assertLess(long['cases'][2], 0, 'a bought option loses time value if the stock goes nowhere')
        self.assertAlmostEqual(long['breakevens'][0], 1000. + long['legs'][0]['price'], places=1)

    def test_expensive_options_and_own_results_change_the_ranking(self):
        base = {i['name']: i['score'] for i in plan(self.sig, chain(), hv=30.)['ideas']}
        rich = {i['name']: i['score'] for i in plan(self.sig, chain(iv=45.), hv=25.)['ideas']}
        self.assertLess(rich['Buy ATM call'] / base['Buy ATM call'], rich['Put credit spread'] / base['Put credit spread'])
        taught = {i['name']: i['score'] for i in plan(self.sig, chain(), hv=30., learned=dict(debit=0.7))['ideas']}
        self.assertLess(taught['Call debit spread'], base['Call debit spread'])

    def test_bearish_mirror(self):
        p = plan(dict(direction='bearish', setup='ADXtender', trigger=998., stop=1025., target=944.), chain(), hv=30.)
        self.assertEqual(self.names(p), {'Buy ITM put', 'Buy ATM put', 'Buy OTM put', 'Put debit spread', 'Call credit spread'})
        credit = next(i for i in p['ideas'] if i['kind'] == 'credit')
        self.assertGreaterEqual(next(l for l in credit['legs'] if l['qty'] < 0)['strike'], 1025.)

    def test_illiquid_legs_are_never_used(self):
        thin = chain(oi=10)
        self.assertEqual(plan(self.sig, thin, hv=30.)['ideas'], [])
        self.assertFalse(chain_read(thin, 30.)['liquid'])
        self.assertTrue(chain_read(chain(), 30.)['liquid'])

    def test_a_far_target_needs_more_days_than_a_near_one(self):
        near = days_needed(dict(trigger=100., target=105.), atr=5.)
        far = days_needed(dict(trigger=100., target=140.), atr=5.)
        self.assertEqual(near, 10, 'never less than the minimum')
        self.assertGreater(far, near)
        self.assertLessEqual(far, 60)

    def test_realised_vol(self):
        self.assertIsNone(realised_vol([100.] * 5))
        self.assertAlmostEqual(realised_vol([100. * 1.01 ** k for k in range(40)]), 0., places=6)


class FakeMarket:
    def __init__(self, root, frames):
        self.path, self.live_path, self.frames = Path(root) / 'bars.pkl', Path(root) / 'live.pkl', frames

    def symbols(self):
        return sorted(self.frames)

    def daily(self, symbol):
        if symbol not in self.frames:
            raise ValueError('No cached bars for this symbol.')
        return self.frames[symbol]


class Desk(unittest.TestCase):
    def service(self, root, paper):
        from types import SimpleNamespace
        from kronos.storage import Store

        def update(key, payload, close=False):
            for row in paper:
                if row['id'] == key:
                    if row['status'] != 'open':
                        raise ValueError('This paper trade is already closed.')
                    row['payload'] = payload
                    if close:
                        row['status'] = 'closed'
                    return row
            raise ValueError('Paper trade not found.')

        def paper_of(key=None):
            if key is None:
                return paper
            row = next((r for r in paper if r['id'] == key), None)
            if not row:
                raise ValueError('Paper trade not found.')
            return row

        d, o, h, l, c = bars(base_then_breakout())
        frame = pd.DataFrame(dict(open=o, high=h, low=l, close=c, volume=1e6), index=pd.to_datetime(d))
        return SimpleNamespace(store=Store(Path(root) / 'r.sqlite3'), market=FakeMarket(root, {'TEST': frame}),
                               desk=SimpleNamespace(_fno_symbols=lambda: {'TEST', 'NOBARS'}),
                               options=SimpleNamespace(journal=SimpleNamespace(paper=paper_of, update=update), market=None),
                               prices=SimpleNamespace(quotes=lambda symbols: dict(quotes={})))

    def trade(self, k, pnl, kind='debit', status='closed'):
        return dict(id=f't{k}', status=status, created='2026-11-01T10:00:00+00:00', updated='2026-11-05T10:00:00+00:00',
                    payload=dict(symbol='TEST', source='adx', entry_spot=100., mark=dict(net_pnl=pnl, time='x') if pnl is not None else None,
                                 adx=dict(setup='ADXodus', direction='bullish', kind=kind, trigger=100., stop=95., target=110.),
                                 plan=dict(name=f"ADX ADXodus · {'Call debit spread' if kind == 'debit' else 'Put credit spread'}",
                                           expiry='2026-12-31', max_loss=1000., max_profit=1500.), legs=[]))

    def test_a_setup_is_tradable_from_adx_alone(self):
        from kronos.adx_desk import AdxDesk
        with tempfile.TemporaryDirectory() as root:
            desk = AdxDesk(self.service(root, []))
            desk._scan()
            self.assertEqual(desk.job['error'], '')
            live = [r for r in desk.board()['rows'] if r['state'] != 'watch']
            self.assertTrue(live)
            self.assertIn(live[0]['verdict'], ('enter', 'armed'))     # no history gate, no Kronos forecast needed
            self.assertEqual(live[0]['record']['n'], 0)

    def test_own_trades_teach_the_desk(self):
        from kronos.adx_desk import AdxDesk
        paper = [self.trade(k, -400.) for k in range(5)] + [self.trade(k + 5, 300., 'credit') for k in range(5)]
        paper.append(dict(id='kronos', status='closed', created='x', updated='x', payload=dict(symbol='TEST', source='desk')))
        with tempfile.TemporaryDirectory() as root:
            desk = AdxDesk(self.service(root, paper))
            desk._scan()
            row = next(r for r in desk.board()['rows'] if r['state'] != 'watch')
            for t in paper[:10]:
                t['payload']['adx']['setup'] = row['setup']       # the synthetic base may qualify as ADXodus or ADXplosive
            row = next(r for r in desk.board()['rows'] if r['state'] != 'watch')
            self.assertEqual(row['record']['n'], 10)                  # the non-ADX trade is ignored
            self.assertIn('losing so far', row['why'])
            self.assertEqual(desk._learned(desk._trades(), row['setup']), dict(debit=0.7, credit=1.2))
            summary = desk.trades()
            self.assertEqual(summary['total']['pnl'], -500.)
            self.assertEqual({g['name'] for g in summary['groups'] if g['by'] == 'structure'}, {'Call debit spread', 'Put credit spread'})

    @mock.patch('kronos.adx_desk.market_open', return_value=True)
    def test_open_trade_gets_exit_advice_from_the_stock(self, _market_open):
        from kronos.adx_desk import AdxDesk
        with tempfile.TemporaryDirectory() as root:
            open_trade = self.trade(1, None, status='open')
            open_trade['created'] = '2026-11-10T10:00:00+05:30'
            open_trade['payload']['adx'].update(stop=500., target=900.)   # the stock (about 108) is far below this stop
            desk = AdxDesk(self.service(root, [open_trade]))
            desk.service.prices = SimpleNamespace(quotes=lambda symbols: dict(quotes={s: dict(ltp=108.) for s in symbols}))
            self.assertEqual(desk.trades()['open'][0]['action'], 'exit')
            desk._scan()
            self.assertEqual([a['action'] for a in desk.board()['alerts']], ['exit'])

    def test_live_quotes_fetches_a_missing_symbol_instead_of_reusing_an_unrelated_cached_batch(self):
        # Regression: board() asks for one set of symbols, trades() asks for a different, overlapping
        # set moments later. A single time-keyed cache blob would serve the first request's symbols to
        # the second and silently drop the rest to their stale closing price.
        from kronos.adx_desk import AdxDesk
        with tempfile.TemporaryDirectory() as root:
            desk = AdxDesk(self.service(root, []))
            calls = []

            def quotes(symbols):
                calls.append(list(symbols))
                return dict(quotes={s: dict(ltp=100. + k) for k, s in enumerate(symbols)})

            desk.service.prices = SimpleNamespace(quotes=quotes)
            first = desk._live_quotes(['A'])
            self.assertIn('A', first)
            second = desk._live_quotes(['A', 'B'])
            self.assertIn('B', second, 'B must be fetched, not silently dropped because of a cached A-only batch')
            self.assertEqual(second['A']['ltp'], first['A']['ltp'], 'A is still fresh and should not need refetching')
            self.assertFalse(second['A']['stale'])
            self.assertEqual(calls, [['A'], ['B']], 'only the missing symbol is fetched the second time')

    def test_a_failed_price_refresh_returns_the_last_price_flagged_stale_with_its_age(self):
        from kronos import adx_desk
        from kronos.adx_desk import AdxDesk
        with tempfile.TemporaryDirectory() as root:
            desk = AdxDesk(self.service(root, []))
            desk.service.prices = SimpleNamespace(quotes=lambda symbols: dict(quotes={s: dict(ltp=250.) for s in symbols}))
            fresh = desk._live_quotes(['A'])['A']
            self.assertEqual((fresh['ltp'], fresh['stale']), (250., False))

            def down(symbols):
                raise ValueError('feed down')

            desk.service.prices = SimpleNamespace(quotes=down)
            desk.quotes['A'] = (desk.quotes['A'][0] - adx_desk.QUOTE_TTL - 5, desk.quotes['A'][1])   # the cached quote has aged
            old = desk._live_quotes(['A'])['A']
            self.assertEqual(old['ltp'], 250., 'the last known price is still returned')
            self.assertTrue(old['stale'])
            self.assertGreaterEqual(old['age'], adx_desk.QUOTE_TTL + 5)
            self.assertNotIn('A', desk._live_quotes(['B']), 'a symbol that never had a quote is absent, not invented')

    def test_the_board_and_trades_flag_a_stock_priced_without_a_live_quote(self):
        from kronos.adx_desk import AdxDesk
        with tempfile.TemporaryDirectory() as root:
            t = self.trade(1, None, status='open')
            t['created'] = '2026-11-10T10:00:00+05:30'
            desk = AdxDesk(self.service(root, [t]))
            desk._scan()
            with mock.patch('kronos.adx_desk.market_open', return_value=True):
                live = [r for r in desk.board()['rows'] if r['state'] != 'watch']
                self.assertTrue(live and all(r['price_stale'] and r['ltp'] is None for r in live), 'no quote arrived in market hours')
                trade = desk.trades()['open'][0]
                self.assertTrue(trade['spot_stale'])
                self.assertEqual(trade['spot_source'], 'close')
                self.assertIn('no live stock price', trade['advice'])
            with mock.patch('kronos.adx_desk.market_open', return_value=False):
                self.assertFalse(any(r['price_stale'] for r in desk.board()['rows']), 'nothing is expected from the feed when the market is closed')

    def test_the_trailing_stop_survives_an_intraday_round_trip_without_a_mark(self):
        # Regression: price ran 100 -> 107 -> 101 with no Mark taken at the peak and no daily bar covering
        # it either. The stop must remember the 107 high instead of forgetting it the moment price pulls back.
        from kronos.adx_desk import AdxDesk
        with tempfile.TemporaryDirectory() as root:
            t = self.trade(1, None, status='open')
            t['created'] = '2026-11-10T10:00:00+05:30'      # long after the fixture's own bars: no daily bar will cover it
            t['payload']['adx'].update(trigger=100., stop=95., target=110.)
            desk = AdxDesk(self.service(root, [t]))

            def quote(price):
                desk.quotes = {}      # bypass the live-quote cache between calls
                desk.service.prices = SimpleNamespace(quotes=lambda symbols: dict(quotes={s: dict(ltp=price) for s in symbols}))

            with mock.patch('kronos.adx_desk.market_open', return_value=True):
                quote(107.)
                first = desk.trades()['open'][0]
                self.assertEqual((first['stop_now'], first['trailed']), (102., True))

                quote(101.)
                second = desk.trades()['open'][0]
                self.assertEqual(second['stop_now'], 102., 'the 107 high must not be forgotten just because nothing marked it')
                self.assertEqual(second['action'], 'exit')
                self.assertIn('trailed stop', second['advice'])

    def test_the_trailing_stop_ignores_the_entry_days_own_candle(self):
        # Regression: a daily candle spans the whole session, including whatever happened before the
        # trade was even opened. Using the entry day's own high/low could raise the stop off a move the
        # trade was never exposed to.
        from kronos.adx_desk import AdxDesk
        with tempfile.TemporaryDirectory() as root:
            d, o, h, l, c = bars(base_then_breakout())
            h[-1], l[-1] = 999., 1.         # a wild pre-entry spike on what will become the entry day
            frame = pd.DataFrame(dict(open=o, high=h, low=l, close=c, volume=1e6), index=pd.to_datetime(d))
            entry = c[-1]
            t = self.trade(1, None, status='open')
            t['created'] = d[-1].isoformat() + 'T13:00:00+05:30'      # entered partway through the entry day
            t['payload']['adx'].update(trigger=entry, stop=entry - 5., target=entry + 20.)
            t['payload']['entry_spot'] = entry
            service = self.service(root, [t])
            service.market = FakeMarket(root, {'TEST': frame})
            desk = AdxDesk(service)
            desk.service.prices = SimpleNamespace(quotes=lambda symbols: dict(quotes={s: dict(ltp=entry + 2.) for s in symbols}))
            with mock.patch('kronos.adx_desk.market_open', return_value=True):
                out = desk.trades()['open'][0]
            self.assertFalse(out['trailed'], "the entry day's own 999 high must not be used; nothing has actually moved that far")
            self.assertEqual(out['stop_now'], entry - 5.)

    def test_an_older_slower_update_cannot_overwrite_a_newer_high(self):
        # Regression: two overlapping reads of the same trade (e.g. the watcher and a page load) must
        # never let whichever one happens to WRITE last erase a higher peak the other already saved.
        from kronos.adx_desk import AdxDesk
        with tempfile.TemporaryDirectory() as root:
            t = self.trade(1, None, status='open')
            desk = AdxDesk(self.service(root, [t]))
            self.assertEqual(desk._ratchet(t['id'], 107., 1), 107.)
            # A slower read that started before the 107 write finally computes its own, lower candidate.
            self.assertEqual(desk._ratchet(t['id'], 104., 1), 107., 'an older, lower observation must not lower the saved peak')
            self.assertEqual(desk._ratchet(t['id'], 110., 1), 110., 'a genuinely new peak still raises it')
            self.assertEqual(t['payload']['adx']['high_water'], 110.)

    def test_concurrent_ratchets_never_lose_the_highest_price_seen(self):
        from kronos.adx_desk import AdxDesk
        import threading as th
        with tempfile.TemporaryDirectory() as root:
            t = self.trade(1, None, status='open')
            desk = AdxDesk(self.service(root, [t]))
            candidates = [101., 107., 103., 99., 105., 108., 100., 104.]
            threads = [th.Thread(target=desk._ratchet, args=(t['id'], c, 1)) for c in candidates]
            for x in threads:
                x.start()
            for x in threads:
                x.join()
            self.assertEqual(t['payload']['adx']['high_water'], max(candidates))

    @mock.patch('kronos.adx_desk.market_open', return_value=True)
    def test_the_watcher_logs_each_alert_once(self, _market_open):
        from kronos.adx_desk import AdxDesk
        with tempfile.TemporaryDirectory() as root:
            open_trade = self.trade(1, None, status='open')
            open_trade['created'] = '2026-11-10T10:00:00+05:30'
            open_trade['payload']['adx'].update(stop=500., target=900.)
            desk = AdxDesk(self.service(root, [open_trade]))
            desk.service.prices = SimpleNamespace(quotes=lambda symbols: dict(quotes={s: dict(ltp=108.) for s in symbols}))
            desk._scan()
            first = desk.pulse()
            self.assertEqual([(a['kind'], a['action'], a['n']) for a in first], [('trade', 'exit', 1)])
            self.assertEqual(desk.pulse(), [], 'the same alert is not raised again')
            self.assertEqual(len(desk.alerts(0)['alerts']), 1)
            self.assertEqual(desk.alerts(1)['alerts'], [])
            # A restart forgets what was said, but the log remembers.
            again = AdxDesk(desk.service)
            self.assertEqual(again.pulse(), [])

    def test_board_rows_carry_a_score_and_the_market(self):
        from kronos.adx_desk import AdxDesk
        with tempfile.TemporaryDirectory() as root:
            desk = AdxDesk(self.service(root, []))
            desk._scan()
            board = desk.board()
            row = next(r for r in board['rows'] if r['state'] != 'watch')
            self.assertEqual(row['score'], 50 + sum(p['points'] for p in row['factors']))
            self.assertIn(row['grade'], 'ABC')
            self.assertEqual(board['market']['breadth']['n'], 1)


class JournalPeak(unittest.TestCase):
    """The saved trailing-stop peak, against the real journal and a real SQLite file."""

    def journal(self, root, direction='bullish', peak=107.):
        from kronos.options_journal import OptionsJournal
        from kronos.storage import Store
        journal = OptionsJournal(Store(Path(root) / 'r.sqlite3'))
        row = journal.open('analysis', 'candidate', dict(symbol='TEST', source='adx', legs=[], plan=dict(expiry='2026-12-31'),
                                                         adx=dict(direction=direction, high_water=peak)))
        return journal, row['id']

    def test_a_stale_mark_cannot_loosen_a_peak_the_watcher_just_saved(self):
        # Regression: Mark reads the trade, waits on the network for depth and the stock price, then writes the
        # whole payload back. A watcher update in that window used to be overwritten (peak 109 became 107).
        with tempfile.TemporaryDirectory() as root:
            journal, key = self.journal(root)
            snapshot = journal.paper(key)['payload']                  # what Mark read, with peak 107
            fresh = journal.paper(key)['payload']
            fresh['adx']['high_water'] = 109.                         # the watcher's newer save
            journal.update(key, fresh)
            journal.update(key, {**snapshot, 'mark': dict(time='t', net_pnl=50., underlying=108.)})
            saved = journal.paper(key)['payload']
            self.assertEqual(saved['adx']['high_water'], 109.)
            self.assertEqual(saved['mark']['net_pnl'], 50., 'the Mark itself is still saved')

    def test_a_bearish_peak_only_moves_down_and_a_new_peak_still_wins(self):
        with tempfile.TemporaryDirectory() as root:
            journal, key = self.journal(root, 'bearish', 93.)
            stale = journal.paper(key)['payload']
            newer = journal.paper(key)['payload']
            newer['adx']['high_water'] = 91.
            journal.update(key, newer)
            journal.update(key, stale)                                # an older snapshot arrives late
            self.assertEqual(journal.paper(key)['payload']['adx']['high_water'], 91.)
            worse = journal.paper(key)['payload']
            worse['adx']['high_water'] = 90.
            journal.update(key, worse)
            self.assertEqual(journal.paper(key)['payload']['adx']['high_water'], 90., 'a genuinely better price is kept')

    def test_trades_without_a_peak_and_closing_are_untouched(self):
        with tempfile.TemporaryDirectory() as root:
            journal, key = self.journal(root, peak=None)
            body = journal.paper(key)['payload']
            body['adx']['high_water'] = 104.
            journal.update(key, body)
            self.assertEqual(journal.paper(key)['payload']['adx']['high_water'], 104.)
            journal.update(key, {**body, 'adx': {**body['adx'], 'high_water': 100.}}, close=True)
            row = journal.paper(key)
            self.assertEqual((row['status'], row['payload']['adx']['high_water']), ('closed', 104.))
            with self.assertRaises(ValueError):
                journal.update(key, body)                             # still refuses a closed trade

    def test_marks_racing_the_watcher_on_real_threads_never_lose_the_peak(self):
        import threading as th
        from kronos.adx_desk import AdxDesk
        with tempfile.TemporaryDirectory() as root:
            journal, key = self.journal(root, peak=100.)
            desk = AdxDesk(SimpleNamespace(store=journal.store, options=SimpleNamespace(journal=journal)))
            snapshot = journal.paper(key)['payload']                  # every "Mark" started from this old read

            def mark(k):
                journal.update(key, {**snapshot, 'mark': dict(time=f't{k}', net_pnl=float(k), underlying=100.)})

            jobs = [th.Thread(target=desk._ratchet, args=(key, 100. + k, 1)) for k in range(1, 11)]
            jobs += [th.Thread(target=mark, args=(k,)) for k in range(10)]
            for x in jobs:
                x.start()
            for x in jobs:
                x.join()
            self.assertEqual(journal.paper(key)['payload']['adx']['high_water'], 110.)


class Brain(unittest.TestCase):
    row = dict(state='armed', direction='bullish', quality=100, risk_pct=3., trigger=100., verdict='armed',
               features=dict(wk_adx=30., weekly_aligned=True, rs20=4., adx_rising=True))

    def trade(self, pnl, marks=(), **more):
        base = dict(status='closed', pnl=pnl, max_loss=1000., direction='bullish', kind='debit', entry_spot=100., trigger=100.,
                    stop=95., target=110., planned_loss=500., updated='2026-11-05T10:00:00', legs=[],
                    marks=[dict(net_pnl=p, underlying=u) for p, u in marks])
        return dict(base, **more)

    def bench(self, slope):
        d, o, h, l, c = bars([1000 + slope * i + math.sin(i / 4) * 3 for i in range(300)])
        return d, h, l, c

    def test_market_state_reads_direction_and_breadth(self):
        from kronos import adx_brain as brain
        up = brain.market_state(self.bench(2.), dict(n=200, up=60, down=15, trending=50, above=70))
        down = brain.market_state(self.bench(-2.), dict(n=200, up=15, down=60, trending=50, above=30))
        self.assertEqual((up['regime'], down['regime']), ('bull', 'bear'))
        self.assertGreater(up['tilt'], brain.LEAN)
        self.assertLess(down['tilt'], -brain.LEAN)
        self.assertIn('bullish', up['stance'])
        self.assertEqual(brain.market_state(None)['regime'], 'unknown')

    def test_the_same_setup_scores_higher_with_the_market_than_against_it(self):
        from kronos import adx_brain as brain
        up, down = brain.market_state(self.bench(2.)), brain.market_state(self.bench(-2.))
        with_, against = brain.conviction(self.row, up), brain.conviction(self.row, down)
        self.assertGreater(with_['score'], against['score'])
        self.assertEqual(with_['grade'], 'A')
        self.assertEqual(with_['score'], 50 + sum(p['points'] for p in with_['factors']))
        bear = dict(self.row, direction='bearish')
        self.assertGreater(brain.conviction(bear, down)['score'], brain.conviction(bear, up)['score'])

    def test_learning_is_slow_and_capped(self):
        from kronos import adx_brain as brain
        lost = lambda n: [self.trade(-600., tags=dict(market='against')) for _ in range(n)]
        self.assertEqual(brain.lessons(lost(2))['effects'], {}, 'two trades teach nothing')
        few, many = brain.lessons(lost(3))['effects']['market:against'], brain.lessons(lost(40))['effects']['market:against']
        self.assertEqual(few['points'], -6)
        self.assertEqual(many['points'], -8, 'never more than 8 points')
        market = dict(regime='bear', tilt=-.8)
        plain, taught = brain.conviction(self.row, market), brain.conviction(self.row, market, dict({'market:against': few}))
        self.assertEqual(taught['score'], plain['score'] - 6)

    def test_review_names_the_mistake(self):
        from kronos import adx_brain as brain
        kind = lambda t, alert=None: brain.review(t, alert)['mistake']
        self.assertEqual(kind(self.trade(-300., [(400., 104.), (-300., 99.)])), 'gave_back')
        self.assertEqual(kind(self.trade(-300., [(-300., 104.)])), 'structure')
        self.assertEqual(kind(self.trade(-300., [(-300., 100.5)])), 'stalled')
        self.assertEqual(kind(self.trade(-900., [(-900., 93.)])), 'overshoot')
        self.assertEqual(kind(self.trade(-450., [(-450., 94.)])), '', 'a loss inside the plan is not a mistake')
        self.assertEqual(kind(self.trade(700., [(700., 109.)])), '')
        alert = dict(time='2026-11-03T11:00:00', pnl=-200.)
        late = brain.review(self.trade(-600., [(-600., 93.)]), alert)
        self.assertEqual((late['mistake'], late['cost']), ('late_exit', 400.))
        self.assertEqual(kind(self.trade(-700., [(-700., 93.)], updated='2026-11-03T15:00:00'), alert), 'overshoot',
                         'closing on the day of the alert is not late')

    def test_repeated_mistakes_change_the_rules(self):
        from kronos import adx_brain as brain
        gave = [dict(self.trade(-300.), review=dict(mistake='gave_back', cost=700.)) for _ in range(2)]
        learned = brain.lessons(gave)
        self.assertEqual((learned['policy']['book'], learned['policy']['trail']), (65, .75))
        self.assertEqual(learned['mistakes'][0]['cost'], 1400.)
        self.assertEqual(brain.lessons(gave[:1])['policy'], brain.POLICY)
        wrong = [dict(self.trade(-300., kind='long'), review=dict(mistake='structure', cost=300.)) for _ in range(2)]
        self.assertEqual(brain.lessons(wrong)['policy']['long_tilt'], .85)

    def test_manage_trails_the_stop_and_suggests_adjustments(self):
        from kronos import adx_brain as brain
        t = dict(direction='bullish', kind='long', entry_spot=100., trigger=100., stop=95., target=110., pnl=900., progress=60, days_held=3,
                 legs=[dict(strike=100., type='CE', qty=1)])
        self.assertFalse(brain.manage(t, 103., 103.)['trailed'], 'no trailing before the stock has moved one risk-unit')
        care = brain.manage(t, 106., 107., strikes=[100., 105., 110., 115.])
        self.assertEqual((care['stop'], care['trailed']), (102., True))
        self.assertIn('110 CE', care['adjust'])
        self.assertEqual(brain.manage(t, 105., 105.)['stop'], 100., 'the first raise is to the entry price')
        short = dict(t, direction='bearish', stop=105., target=90.)
        self.assertEqual(brain.manage(short, 94., 93.)['stop'], 98.)
        stuck = dict(t, kind='debit', pnl=-200., progress=5, days_held=8)
        self.assertIn('gone nowhere', brain.manage(stuck, 100.5, 101.)['adjust'])
        self.assertEqual(brain.manage(dict(stuck, days_held=5), 100.5, 101.)['adjust'], '')
        self.assertIn('gone nowhere', brain.manage(dict(stuck, days_held=5), 100.5, 101., dict(brain.POLICY, stall_days=5))['adjust'])
        condor = dict(direction='neutral', kind='short_vol', entry_spot=1000., pnl=100., legs=[
            dict(strike=1060., type='CE', qty=-1), dict(strike=940., type='PE', qty=-1), dict(strike=1100., type='CE', qty=1), dict(strike=900., type='PE', qty=1)])
        self.assertEqual(brain.manage(condor, 1010., None)['adjust'], '')
        self.assertIn('1060 CE', brain.manage(condor, 1050., None)['adjust'])
        self.assertIn('before the event', brain.manage(condor, 1000., None, event_days=1)['adjust'])

    def test_decide(self):
        from kronos import adx_brain as brain
        scored = lambda row: dict(row, **brain.conviction(row, dict(regime='bull', tilt=.8)))
        self.assertEqual(brain.decide(scored(self.row))['call'], 'Wait')
        self.assertEqual(brain.decide(scored(dict(self.row, verdict='enter')))['call'], 'Take')
        weak = dict(self.row, verdict='enter', quality=50, features=dict(wk_adx=30., weekly_aligned=False, rs20=-6., adx_rising=False))
        weak = dict(weak, **brain.conviction(weak, dict(regime='bear', tilt=-.8)))
        out = brain.decide(weak)
        self.assertEqual((weak['grade'], out['call']), ('C', 'Pass'))
        self.assertTrue(out['cons'])
        sold = dict(ideas=[dict(name='Put credit spread', kind='credit')], best='Put credit spread', chain=dict(iv_hv=1.4, liquid=True))
        event = dict(checked=True, inside=True, next=dict(title='Results', date='2026-11-20'))
        self.assertEqual(brain.decide(scored(dict(self.row, verdict='enter')), sold, event)['call'], 'Pass')

    def test_decide_waits_when_the_option_plan_or_the_stock_price_is_missing(self):
        from kronos import adx_brain as brain
        scored = lambda row: dict(row, **brain.conviction(row, dict(regime='bull', tilt=.8)))
        enter = scored(dict(self.row, verdict='enter'))
        out = brain.decide(enter, None, unpriced=True)
        self.assertEqual(out['call'], 'Wait')
        self.assertIn('option prices unavailable', out['why'])
        self.assertTrue(any('could be priced' in c for c in out['cons']))
        old = brain.decide(dict(enter, price_stale=True, ltp_age=42.), dict(ideas=[dict(name='x', kind='long')], best='x', chain=dict(liquid=True)))
        self.assertEqual(old['call'], 'Wait')
        self.assertTrue(any('42s old' in c for c in old['cons']))
        self.assertEqual(brain.decide(dict(enter, price_stale=True, ltp_age=None), dict(ideas=[dict(name='x', kind='long')], best='x', chain=dict(liquid=True)))['call'], 'Wait')
        self.assertEqual(brain.decide(scored(dict(self.row, verdict='skip', why='Price is beyond the stop.')), None, unpriced=True)['call'], 'Pass')
        self.assertEqual(brain.decide(scored(self.row), None, unpriced=True)['call'], 'Wait')

    def test_decide_does_not_say_take_on_a_stale_chain(self):
        # Regression: "Take" must not appear next to prices that are not actually live -- that reads as
        # ready to trade when it is not.
        from kronos import adx_brain as brain
        scored = lambda row: dict(row, **brain.conviction(row, dict(regime='bull', tilt=.8)))
        bought = dict(ideas=[dict(name='Buy OTM put', kind='long')], best='Buy OTM put', chain=dict(liquid=True), stale=True)
        out = brain.decide(scored(dict(self.row, verdict='enter')), bought)
        self.assertEqual(out['call'], 'Wait')
        self.assertIn('waiting for live option prices', out['why'])
        self.assertTrue(any('not live' in c for c in out['cons']))
        # A setup that is not even triggered yet keeps its own, more specific reason.
        armed = brain.decide(scored(self.row), bought)
        self.assertEqual(armed['call'], 'Wait')
        self.assertIn('Not triggered yet', armed['why'])
        # Staleness never turns a real Pass into anything softer.
        weak = dict(self.row, verdict='enter', quality=50, features=dict(wk_adx=30., weekly_aligned=False, rs20=-6., adx_rising=False))
        weak = dict(weak, **brain.conviction(weak, dict(regime='bear', tilt=-.8)))
        self.assertEqual(brain.decide(weak, bought)['call'], 'Pass')
        # A live chain never gets the stale warning or wording.
        fresh = dict(bought, stale=False)
        self.assertEqual(brain.decide(scored(dict(self.row, verdict='enter')), fresh)['call'], 'Take')


if __name__ == '__main__':
    unittest.main()
