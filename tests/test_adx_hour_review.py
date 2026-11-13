"""Regression tests for the defects found in the 13 Nov review of the 1-hour ADX desk: one entry gate for
every path, current bars, an expiry floor, atomic saving, a stop that stays touched, every leg priced.
Synthetic bars only; no network."""
from datetime import datetime
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_adx import Desk, chain   # noqa: E402
from test_adx_hour import HourDesk   # noqa: E402

from kronos import adx_hour   # noqa: E402
from kronos.adx_options import plan   # noqa: E402
from kronos.options_math import bs   # noqa: E402
from kronos.adx_desk import Declined   # noqa: E402

LIVE = lambda: mock.patch('kronos.adx_desk.market_open', return_value=True)
REQUEST = lambda row: dict(symbol='TEST', signal=row['id'], idea='Call debit spread')


class ReviewFixes(HourDesk):
    # HourDesk's own tests are inherited on purpose: they must keep passing under these stricter rules.

    def live_desk(self, root, paper=None):
        """A desk with a scanned board, a live price just through a trigger, and a canned option plan."""
        desk = self.desk(root, [] if paper is None else paper)
        row = self.enter(desk)
        idea = dict(name='Call debit spread', kind='debit', legs=[dict(symbol='NSE:TESTA', qty=1), dict(symbol='NSE:TESTB', qty=-1)], cases=[900., -400., 0.])
        self.made = dict(ideas=[idea], best=idea['name'], days=12., expiry='2099-12-31', stale=False, notes=[], chain=None, spot=100.)
        desk._plan = lambda *a, **k: self.made
        desk.start = lambda: dict(desk.job)      # a board that is behind would start a background rescan; keep the test single-threaded
        self.requests = []
        desk.service.desk.open_paper = lambda request: self.requests.append(request) or dict(id='new', payload={'plan': {'name': request['name']}})
        return desk, row

    def call(self, verdict='Take'):
        return mock.patch('kronos.adx_desk.brain.decide', return_value=dict(call=verdict, why='because', pros=[], cons=[]))

    def test_every_entry_needs_a_triggered_setup_current_bars_and_a_live_price(self):
        with tempfile.TemporaryDirectory() as root, LIVE(), self.call():
            desk, row = self.live_desk(root)
            desk.paper(REQUEST(row))                                       # triggered, current, priced: allowed
            self.assertEqual(len(self.requests), 1)
            with mock.patch.object(adx_hour, 'last_bar', return_value=datetime(2099, 1, 1, 15, 15)):
                with self.assertRaisesRegex(Declined, 'out-of-date'):
                    desk.paper(REQUEST(row))                               # the bars are behind the newest completed hour
            desk.quotes, desk.service.prices = {}, SimpleNamespace(quotes=lambda symbols: dict(quotes={}))
            with self.assertRaisesRegex(Declined, 'No live stock price'):
                desk.paper(REQUEST(row))
            self.assertEqual(len(self.requests), 1)

    def test_an_armed_setup_cannot_be_bought_by_hand_either(self):
        with tempfile.TemporaryDirectory() as root, LIVE():
            desk, row = self.live_desk(root)
            sign = 1 if row['direction'] == 'bullish' else -1
            before = row['trigger'] - sign * .5 * abs(row['trigger'] - row['stop'])
            desk.quotes = {}
            desk.service.prices = SimpleNamespace(quotes=lambda symbols: dict(quotes={s: dict(ltp=before) for s in symbols}))
            self.assertEqual(next(r for r in desk.board()['rows'] if r['id'] == row['id'])['verdict'], 'armed')
            with self.assertRaisesRegex(Declined, 'Not triggered'):
                desk.paper(REQUEST(row))
            self.assertEqual(self.requests, [])

    def test_an_expiry_inside_the_exit_window_is_refused(self):
        with tempfile.TemporaryDirectory() as root, LIVE(), self.call():
            desk, row = self.live_desk(root)
            self.made['days'] = 2.
            with self.assertRaisesRegex(Declined, 'only 2 days'):
                desk.paper(REQUEST(row))
            self.assertEqual(self.requests, [])
            self.made['days'] = adx_hour.FLOOR_DAYS
            desk.paper(REQUEST(row))
            self.assertEqual(len(self.requests), 1)

    def test_the_detail_panel_agrees_the_short_expiry_is_a_pass(self):
        with tempfile.TemporaryDirectory() as root, LIVE():
            desk, row = self.live_desk(root)
            self.made['days'] = 2.
            plan = dict(self.made, chain=dict(liquid=True, iv_hv=None))
            call = desk._decide(row, plan, dict(checked=True, next=None, inside=False), 'TEST', 100.)
            self.assertEqual(call['call'], 'Pass')
            self.assertIn('at least', call['why'])

    def test_the_automatic_entry_follows_the_desks_own_call(self):
        with tempfile.TemporaryDirectory() as root, LIVE():
            desk, row = self.live_desk(root)
            request = dict(symbol='TEST', signal=row['id'])
            for verdict in ('Pass', 'Wait'):
                with self.call(verdict), self.assertRaisesRegex(Declined, f'call is {verdict}'):
                    desk.paper(request, auto=True)
            self.assertEqual(self.requests, [])
            with self.call('Take'):
                desk.paper(request, auto=True)
            self.assertEqual(len(self.requests), 1)

    def test_a_declined_setup_raises_no_alert_uses_no_attempt_and_says_why(self):
        with tempfile.TemporaryDirectory() as root, LIVE(), self.call('Pass'):
            desk, row = self.live_desk(root)
            for _ in range(5):
                self.assertEqual([a for a in desk.pulse() if a['kind'] == 'entry'], [])
            self.assertEqual(self.requests, [])
            self.assertEqual(desk.auto_tries.get(row['id'], (0, 0))[0], 0, 'a declined setup uses up none of its attempts')
            self.assertIn('Not traded automatically', next(r for r in desk.board()['rows'] if r['id'] == row['id'])['why'])

    def test_entries_stop_at_the_open_trade_cap(self):
        with tempfile.TemporaryDirectory() as root, LIVE(), self.call():
            desk, row = self.live_desk(root)
            desk.MAX_OPEN = 1
            full = Desk().trade(1, None, status='open')
            full['payload'].update(source='adx1h')
            desk.service.options.journal.paper = lambda key=None: full if key else [full]
            with self.assertRaisesRegex(Declined, 'already open'):
                desk.paper(REQUEST(row))

    def test_no_automatic_entry_while_the_scan_is_behind(self):
        with tempfile.TemporaryDirectory() as root, LIVE(), self.call():
            desk, row = self.live_desk(root)
            desk.paper = lambda *a, **k: self.fail('a setup from out-of-date bars must not be traded')
            with mock.patch.object(adx_hour, 'last_bar', return_value=datetime(2099, 1, 1, 15, 15)):
                board = desk.board()
                self.assertTrue(board['behind'])
                self.assertEqual(desk._auto_enter(board), [])

    def test_the_trade_and_its_adx_record_are_saved_together(self):
        with tempfile.TemporaryDirectory() as root, LIVE(), self.call():
            desk, row = self.live_desk(root)
            desk.paper(REQUEST(row))
            request = self.requests[0]
            self.assertEqual(request['extra']['source'], 'adx1h')
            self.assertEqual(request['extra']['adx']['signal'], row['id'])
            self.assertIn('underlying_stop', request['plan_extra'])
            # A half-made record (an older version, or a crash between two writes) is skipped, not fatal.
            broken = Desk().trade(9, None, status='open')
            broken['payload'].update(source='adx1h')
            del broken['payload']['adx']
            desk.service.options.journal.paper = lambda key=None: [broken]
            self.assertEqual(desk._trades(), [])

    def test_a_stop_touched_while_nobody_was_looking_is_not_forgotten(self):
        with tempfile.TemporaryDirectory() as root, LIVE():
            t = Desk().trade(1, None, status='open')
            t['payload'].update(source='adx1h')
            desk = self.desk(root, [t])
            frame = desk.bars.frames['TEST']
            last = float(frame.close.iloc[-1])
            t['created'] = frame.index[-5].isoformat() + '+05:30'        # the five bars from here on all happened after the entry
            t['payload']['entry_spot'] = last
            t['payload']['plan']['expiry'] = '2099-12-31'
            desk.service.prices = SimpleNamespace(quotes=lambda symbols: dict(quotes={s: dict(ltp=last) for s in symbols}))
            # Five quiet bars since the entry, one of which dipped 4 below the entry price; the stock is back where it began.
            frame = frame.copy()
            frame.iloc[-5:, frame.columns.get_loc('high')] = last + .2
            frame.iloc[-5:, frame.columns.get_loc('low')] = last - .2
            frame.iloc[-3, frame.columns.get_loc('low')] = last - 4.
            desk.bars.frames['TEST'] = frame
            for stop, touched in ((last - 2., True), (last - 10., False)):
                t['payload']['adx'].pop('breach', None)
                t['payload']['adx'].update(trigger=last, stop=stop, target=last + 5000.)
                desk.quotes = {}
                out = desk.view('trades')['open'][0]
                self.assertEqual('traded through the stop' in out['advice'], touched, out['advice'])
                if touched:
                    self.assertEqual(out['action'], 'exit')
                    self.assertEqual(t['payload']['adx']['breach']['source'], 'later bars', 'the breach is saved on the trade')
                    self.assertGreater(out['spot'], stop, 'the stock is back above the stop, yet the trade has already failed')

    def test_every_open_leg_is_priced_not_just_the_first_fifty(self):
        with tempfile.TemporaryDirectory() as root, LIVE():
            paper = []
            for k in range(26):
                t = Desk().trade(k, None, status='open')
                t['payload'].update(source='adx1h')
                t['payload']['legs'] = [dict(strike=100., type='CE', qty=1, entry_fill=1., symbol=f'NSE:S{k}A', lot=1),
                                        dict(strike=110., type='CE', qty=-1, entry_fill=1., symbol=f'NSE:S{k}B', lot=1)]
                t['payload']['plan']['expiry'] = '2099-12-31'
                paper.append(t)
            desk = self.desk(root, paper)
            asked = []

            def ltp(symbols):
                asked.append(list(symbols))
                return dict(quotes={s: dict(ltp=2.) for s in symbols})

            desk.service.desk.ltp = ltp
            out = desk.view('trades')['open']
            self.assertEqual(sorted(len(a) for a in asked), [2, 50])
            self.assertEqual(len({s for a in asked for s in a}), 52)
            self.assertTrue(all(t['pnl'] is not None for t in out), 'no trade is left without a P&L because of the 50-contract cap')

    def test_a_behind_scan_is_repeated_with_a_growing_wait(self):
        with tempfile.TemporaryDirectory() as root:
            desk = self.desk(root, [])
            self.assertFalse(desk._behind(dict(data_date=str(adx_hour.last_bar()))))
            self.assertTrue(desk._behind(dict(data_date='2020-01-01 09:15:00')))
            waits = []
            for late in range(6):
                desk.late = late
                waits.append(desk._gap())
            self.assertEqual(waits, [120, 240, 480, 900, 900, 900])

    def test_coverage_names_the_stocks_without_the_newest_hour(self):
        with tempfile.TemporaryDirectory() as root:
            desk = self.desk(root, [])
            desk.bars.failed = {'BAD'}
            report = desk.bars.coverage(['TEST', 'LATE', 'BAD'])
            self.assertEqual((report['behind_n'], report['behind'], report['failed']), (2, ['BAD', 'LATE'], ['BAD']))

    def test_volatility_leaves_out_a_day_still_in_progress(self):
        with tempfile.TemporaryDirectory() as root:
            desk = self.desk(root, [])
            up, _ = desk._views('TEST', None)
            frame = desk.bars.frames['TEST']
            days = len({t.date() for t in frame.index})
            finished = frame.index[-1].hour == 15
            self.assertEqual(len(desk._day_closes(up)), days if finished else days - 1)

    # ---------- follow-up review (13 Nov, second pass) ----------
    def test_a_missing_benchmark_or_scan_refuses_the_entry(self):
        with tempfile.TemporaryDirectory() as root, LIVE(), self.call():
            desk, row = self.live_desk(root)
            desk._bench = lambda: None
            with self.assertRaisesRegex(Declined, 'Nifty bars are missing'):
                desk.paper(REQUEST(row))
            desk._bench = lambda: ([datetime(2099, 1, 1)], [1.], [1.], [1.])
            desk._saved = lambda key='board': None
            with self.assertRaisesRegex(ValueError, 'no longer on the board'):       # without a saved scan there is no setup to trade at all
                desk.paper(REQUEST(row))
            self.assertEqual(self.requests, [])

    def test_a_manual_entry_against_the_desks_call_needs_an_explicit_override_that_is_recorded(self):
        with tempfile.TemporaryDirectory() as root, LIVE(), self.call('Pass'):
            desk, row = self.live_desk(root)
            with self.assertRaisesRegex(Declined, 'Confirm the override'):
                desk.paper(REQUEST(row))
            self.assertEqual(self.requests, [])
            desk.paper(dict(REQUEST(row), override=True))
            self.assertEqual(self.requests[0]['extra']['adx']['override'], 'Pass')
        with tempfile.TemporaryDirectory() as root, LIVE(), self.call('Take'):
            desk, row = self.live_desk(root)
            desk.paper(dict(REQUEST(row), override=True))
            self.assertNotIn('override', self.requests[0]['extra']['adx'], 'nothing to override when the call is Take')

    def test_a_stop_seen_on_a_live_price_stays_breached_after_the_stock_recovers(self):
        with tempfile.TemporaryDirectory() as root, LIVE():
            t = Desk().trade(1, None, status='open')
            t['payload'].update(source='adx1h')
            t['payload']['adx'].update(trigger=100., stop=95., target=110.)
            t['payload']['plan']['expiry'] = '2099-12-31'
            desk = self.desk(root, [t])

            def at(price):
                desk.quotes = {}
                desk.service.prices = SimpleNamespace(quotes=lambda symbols: dict(quotes={s: dict(ltp=price) for s in symbols}))
                return desk.view('trades')['open'][0]

            self.assertEqual(at(94.)['action'], 'exit')
            self.assertEqual(t['payload']['adx']['breach']['source'], 'live price', 'the breach is saved on the trade')
            again = at(100.)
            self.assertEqual(again['action'], 'exit')
            self.assertIn('has since come back', again['advice'])
            self.assertEqual(again['breach']['price'], 94.)

    def test_replay_judges_each_bar_against_the_stop_in_force_when_it_began(self):
        replay = adx_hour.AdxHourDesk._replay
        # Long from 100, stop 95 (risk 5). The first bar lifts the trail to 101; the second dips to 100.5: below the trail, above the original stop.
        self.assertEqual(replay([(106., 100.), (104., 100.5)], 100., 95., 5., 1, 1.), 100.5)
        self.assertIsNone(replay([(106., 100.), (104., 101.5)], 100., 95., 5., 1, 1.))
        self.assertIsNone(replay([(110., 96.)], 100., 95., 5., 1, 1.), 'a bar never raises the stop for itself')
        # The same trade short, mirrored.
        self.assertEqual(replay([(100., 94.), (99.5, 96.)], 100., 105., 5., -1, 1.), 99.5)

    def test_missing_option_prices_do_not_leave_an_old_mark_as_the_current_pnl(self):
        with tempfile.TemporaryDirectory() as root, LIVE():
            t = Desk().trade(1, -700., status='open')
            t['payload'].update(source='adx1h')
            t['payload']['plan'].update(expiry='2099-12-31', max_loss=1000.)
            t['payload']['legs'] = [dict(strike=100., type='CE', qty=1, entry_fill=8., symbol='NSE:TESTA', lot=100)]
            desk = self.desk(root, [t])
            desk.service.desk.ltp = lambda symbols: dict(quotes={})            # the option feed returned nothing
            out = desk.view('trades')['open'][0]
            self.assertIsNone(out['pnl'])
            self.assertEqual(out['last_mark'], -700.)
            self.assertNotIn('half of what you put at risk', out['advice'], 'an old loss is not evidence now')
            self.assertIn('P&L is unknown', out['advice'])
            desk.service.desk.ltp = lambda symbols: dict(quotes={'NSE:TESTA': dict(ltp=1.)})
            priced = desk.view('trades')['open'][0]
            self.assertEqual(priced['pnl'], -700.)
            self.assertIn('half of what you put at risk', priced['advice'])

    def test_a_late_benchmark_or_single_stock_keeps_the_scan_retrying(self):
        with tempfile.TemporaryDirectory() as root:
            desk = self.desk(root, [])
            current = dict(data_date=str(adx_hour.last_bar()))
            self.assertFalse(desk._behind(current))
            frame = desk.bars.frames[adx_hour.NIFTY]
            desk.bars.frames[adx_hour.NIFTY] = frame.iloc[:-3]                   # only the Nifty is behind
            self.assertTrue(desk._behind(current))
            desk.bars.frames[adx_hour.NIFTY] = frame
            self.assertFalse(desk._behind(current))
            desk.cover = dict(behind_n=1)                                        # a stock that was never fetched
            self.assertTrue(desk._behind(current))

    def test_a_new_hour_starts_the_wait_again(self):
        with tempfile.TemporaryDirectory() as root:
            desk = self.desk(root, [])
            desk.late, desk.late_for = 5, 'an earlier hour'
            self.assertEqual(desk._gap(), 120)
            desk.late = 2
            self.assertEqual(desk._gap(), 480, 'the same hour keeps its count')

    def test_the_open_trade_cap_is_enforced_inside_the_journal_write(self):
        from kronos.options_journal import OptionsJournal
        from kronos.storage import Store
        with tempfile.TemporaryDirectory() as root:
            journal = OptionsJournal(Store(Path(root) / 'r.sqlite3'))
            journal.open('a', 'c', dict(symbol='ONE', source='adx1h', legs=[], plan={}), cap=2)
            journal.open('b', 'c', dict(symbol='TWO', source='adx1h', legs=[], plan={}), cap=2)
            with self.assertRaisesRegex(ValueError, '2 paper trades'):
                journal.open('c', 'c', dict(symbol='THREE', source='adx1h', legs=[], plan={}), cap=2)
            journal.open('d', 'c', dict(symbol='SHARED', source='desk', legs=[], plan={}), cap=2)   # another book is not counted

    def test_a_saved_breach_cannot_be_erased_by_an_older_snapshot(self):
        from kronos.options_journal import keep_peak
        saved = dict(adx=dict(direction='bullish', high_water=107., breach=dict(level=95., price=94.)))
        incoming = dict(adx=dict(direction='bullish', high_water=104.))
        merged = keep_peak(saved, incoming)['adx']
        self.assertEqual((merged['high_water'], merged['breach']['price']), (107., 94.))

    def test_costs_and_volatility_stress_lower_every_outcome_and_a_reward_floor_can_leave_no_pick(self):
        sig = dict(direction='bullish', setup='ADXodus', trigger=1002., stop=975., target=1056.)
        plain = plan(sig, chain())
        costly = {x['name']: x for x in plan(sig, chain(), costs=True, iv_stress=.15)['ideas']}
        for x in plain['ideas']:
            y = costly.get(x['name'])
            if y:
                self.assertGreater(y['round_trip_cost'], 0)
                self.assertLess(y['cases'][0], x['cases'][0], 'the gain at target is smaller after costs and the volatility stress')
                self.assertLess(y['cases'][1], x['cases'][1], 'the loss at the stop is larger')
        floor = plan(sig, chain(), costs=True, iv_stress=.15, min_edge=1000.)
        self.assertIsNone(floor['best'])
        self.assertTrue(all(any(w in x['why'] for w in ('needs at least', 'Does not pay', 'Nothing here')) for x in floor['ideas']))

    def test_the_fill_is_checked_again_at_the_prices_actually_obtained(self):
        with tempfile.TemporaryDirectory() as root, LIVE(), self.call():
            desk, row = self.live_desk(root)
            desk.MIN_EDGE = .01
            typ = 'CE' if row['direction'] == 'bullish' else 'PE'
            strike = round(row['trigger'])
            self.made.update(left=8.)
            self.made['ideas'][0]['legs'] = [dict(symbol='NSE:TESTA', qty=1, iv=30.)]
            fair = bs(row['trigger'], strike, 8., 30., typ)
            ran = []

            def fill(price):
                def open_paper(request):
                    leg = dict(symbol='NSE:TESTA', qty=1, lot=1000, strike=strike, type=typ, entry_fill=price, bid=price * .99, ask_price=price * 1.01)
                    request['check']([leg], dict(brokerage=20., stt=0.))
                    ran.append(request)
                    return dict(id='new', payload={'plan': {'name': request['name']}})
                desk.service.desk.open_paper = open_paper

            fill(fair * 10)                                                     # the book was far worse than the quote
            with self.assertRaisesRegex(Declined, 'really get'):
                desk.paper(REQUEST(row))
            self.assertEqual(ran, [])
            fill(fair * .5)
            desk.paper(REQUEST(row))
            self.assertEqual(len(ran), 1)


    def test_fallback_and_stale_prices_cannot_create_a_stop_breach(self):
        with tempfile.TemporaryDirectory() as root, LIVE():
            t = Desk().trade(1, None, status='open')
            t['payload'].update(source='adx1h', entry_spot=100.)
            t['payload']['plan']['expiry'] = '2099-12-31'
            desk = self.desk(root, [t])
            desk._views = lambda *a: (SimpleNamespace(c=[94.], dom=[1], adx=[30.]), None)
            desk._after = lambda *a: []  # completed close is before entry
            desk._bench = lambda: None
            for quote in ({}, {'ltp': 94., 'age': 20., 'stale': True}, {'ltp': 94.}):
                desk._live_quotes = lambda *a: {'TEST': quote}
                out = desk.trades()['open'][0]
                self.assertIsNone(out['breach'])
                self.assertNotIn('breach', t['payload']['adx'])
                self.assertNotEqual(out['action'], 'exit')
            desk._live_quotes = lambda *a: {'TEST': dict(ltp=100., age=0., stale=False)}
            self.assertEqual(desk.trades()['open'][0]['action'], 'hold')
            # The missing quote must not prevent a verified post-entry bar breach.
            desk._live_quotes = lambda *a: {}
            desk._after = lambda *a: [(101., 94.)]
            out = desk.trades()['open'][0]
            self.assertEqual(out['action'], 'exit')
            self.assertEqual(out['breach']['source'], 'later bars')

    def test_manual_selection_uses_its_own_event_decision_and_override(self):
        with tempfile.TemporaryDirectory() as root, LIVE(), mock.patch(
                'kronos.adx_desk.brain.conviction', return_value=dict(score=80, grade='A', factors=[])):
            desk, row = self.live_desk(root)
            credit = dict(name='Put credit spread', kind='credit', legs=[], cases=[900., -400., 0.])
            self.made['ideas'].append(credit)
            desk._events = lambda *a: dict(checked=True, inside=True, next=dict(date='2026-11-14', title='Results'))
            detail = desk.detail('TEST', row['id'])
            self.assertEqual(detail['decision']['call'], 'Take')
            self.assertEqual(detail['plan']['ideas'][1]['decision']['call'], 'Pass')
            request = dict(symbol='TEST', signal=row['id'], idea=credit['name'])
            with self.assertRaisesRegex(Declined, 'Confirm the override'):
                desk.paper(request)
            self.assertEqual(self.requests, [])
            desk.paper(dict(request, override=True))
            self.assertEqual(self.requests[0]['extra']['adx']['override'], 'Pass')
            self.assertEqual(self.requests[0]['extra']['adx']['kind'], 'credit')
            desk.paper(dict(symbol='TEST', signal=row['id']), auto=True)
            self.assertEqual(self.requests[-1]['extra']['adx']['kind'], 'debit')
            self.assertNotIn('override', self.requests[-1]['extra']['adx'])

    def test_costs_keep_premium_separate_from_charges(self):
        sig = dict(direction='bullish', setup='ADXodus', trigger=1002., stop=975., target=1056.)
        plain = {i['name']: i for i in plan(sig, chain())['ideas']}
        charged = {i['name']: i for i in plan(sig, chain(), costs=True)['ideas']}
        for name, idea in plain.items():
            premium = abs(sum(l['qty'] * l['price'] for l in idea['legs'])) * idea['lot']
            self.assertAlmostEqual(idea['cost'], premium, places=2)
            self.assertEqual(idea['round_trip_cost'], 0.)
            self.assertEqual(charged[name]['cost'], idea['cost'])
            self.assertGreater(charged[name]['round_trip_cost'], 0.)
            for gross, net in zip(idea['cases'], charged[name]['cases']):
                self.assertAlmostEqual(gross - net, charged[name]['round_trip_cost'], places=2)


if __name__ == '__main__':
    unittest.main()
