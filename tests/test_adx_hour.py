"""The ADX desk on 1-hour bars: same rules, the day as the bigger timeframe, automatic paper entry.
Synthetic bars only; no network."""
from datetime import date, datetime
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_adx import Desk, bars, base_then_breakout, days   # noqa: E402  (the daily desk's fixtures)

from kronos import adx_core as core   # noqa: E402
from kronos import adx_hour   # noqa: E402
from kronos.adx_desk import AdxDesk   # noqa: E402


def hours(n, start=date(2026, 1, 5)):
    """n hourly bar starts, seven a session from 09:15."""
    out = []
    for d in days(n // 7 + 1, start):
        out += [datetime(d.year, d.month, d.day, 9 + k, 15) for k in range(7)]
    return out[:n]


class HourDesk(unittest.TestCase):
    def desk(self, root, paper):
        service = Desk().service(root, paper)
        service.directory = root
        desk = adx_hour.AdxHourDesk(service)
        desk.universe = lambda: ['TEST']            # the shared fixture also lists a stock with no bars, which would keep a rescan pending
        # A long quiet stretch first, so the daily background (which needs about 22 completed days) exists.
        quiet = [100 + math.sin(i / 3) * 1.2 for i in range(280)]
        _, o, h, l, c = bars(quiet + base_then_breakout())
        frame = pd.DataFrame(dict(open=o, high=h, low=l, close=c), index=pd.to_datetime(hours(len(c))))
        desk.bars.frames = {'TEST': frame, adx_hour.NIFTY: frame}
        newest = mock.patch.object(adx_hour, 'last_bar', return_value=frame.index[-1].to_pydatetime())   # the fixture is "up to date"
        newest.start()
        self.addCleanup(newest.stop)
        desk.bars.refresh = lambda symbols, progress=None: None       # no network in tests
        return desk

    def test_the_newest_completed_hour(self):
        at = lambda *hm: datetime(2026, 11, 12, *hm)                  # a Thursday
        self.assertEqual(adx_hour.last_bar(at(10, 14)), datetime(2026, 11, 11, 15, 15), 'the 09:15 bar is still forming')
        self.assertEqual(adx_hour.last_bar(at(10, 16)), at(9, 15))
        self.assertEqual(adx_hour.last_bar(at(15, 29)), at(14, 15), 'the last bar ends with the session at 15:30')
        self.assertEqual(adx_hour.last_bar(at(15, 31)), at(15, 15))
        self.assertEqual(adx_hour.last_bar(datetime(2026, 11, 16, 9, 0)), datetime(2026, 11, 13, 15, 15), 'Monday morning looks back over the weekend')

    def test_wording_is_translated_once(self):
        self.assertEqual(adx_hour.reword('Daily DMI dominance returned to the weekly direction'),
                         'Hourly DMI dominance returned to the daily direction')
        out = adx_hour.reword(dict(id='A|Daily', why=['Weekly DMI points the same way', '20-day high made']))
        self.assertEqual(out, dict(id='A|Daily', why=['Daily DMI points the same way', '20-bar high made']))

    def test_the_daily_background_never_sees_the_current_day(self):
        _, o, h, l, c = bars([100 + i * .3 for i in range(700)])
        d = hours(700)
        day = lambda x: x.date()
        full = core.weekly_context(d, h, l, c, period=day)
        cut = core.weekly_context(d[:500], h[:500], l[:500], c[:500], period=day)
        self.assertEqual(full['wk_adx'][:500], cut['wk_adx'])
        self.assertIsNotNone(full['wk_adx'][-1])
        self.assertEqual(len(set(full['wk_adx'][-7:])), 1, 'every hour of one day reads the same completed day')

    def test_hourly_scan_is_separate_and_reads_in_its_own_timeframe(self):
        with tempfile.TemporaryDirectory() as root:
            daily_trade = Desk().trade(1, None, status='open')        # a DAILY desk trade in the same stock
            desk = self.desk(root, [daily_trade])
            desk._scan()
            self.assertEqual(desk.job['error'], '')
            board = desk.view('board')
            live = [r for r in board['rows'] if r['state'] not in ('watch', 'range')]
            self.assertTrue(live)
            self.assertTrue(board['auto'])
            self.assertFalse(live[0]['held'], "the daily desk's trade is not this desk's trade")
            self.assertEqual(board['open_trades'], 0)
            self.assertIn(' ', live[0]['bar_date'], 'bars carry a time, not just a date')
            text = str(live[0]['checks']) + str(live[0]['factors'])
            self.assertNotIn('Weekly', text)
            self.assertIn('Daily DMI points', text)
            self.assertIsNone(AdxDesk(desk.service)._saved(), 'the daily desk never sees the hourly board')

    def enter(self, desk):
        """Put a live price just through the trigger of the first trend setup; returns that row."""
        desk._scan()
        row = next(r for r in desk.board()['rows'] if r['state'] in ('armed', 'triggered'))
        sign = 1 if row['direction'] == 'bullish' else -1
        price = row['trigger'] + sign * .1 * abs(row['trigger'] - row['stop'])
        desk.quotes = {}
        desk.service.prices = SimpleNamespace(quotes=lambda symbols: dict(quotes={s: dict(ltp=price) for s in symbols}))
        return row

    def test_an_enter_setup_is_paper_traded_automatically_and_only_once(self):
        with tempfile.TemporaryDirectory() as root:
            paper = []
            desk = self.desk(root, paper)
            row = self.enter(desk)
            calls = []

            def fake_paper(request, auto=False):
                calls.append((request, auto))
                t = Desk().trade(len(calls), None, status='open')
                t['payload'].update(source='adx1h')
                t['payload']['adx'].update(signal=request['signal'], auto=True, setup=row['setup'], direction=row['direction'],
                                           trigger=row['trigger'], stop=row['stop'], target=row['target'])
                t['payload']['plan']['expiry'] = '2099-12-31'
                t['created'] = '2099-01-01T10:00:00+05:30'
                paper.append(t)
                return t

            desk.paper = fake_paper
            with mock.patch('kronos.adx_desk.market_open', return_value=True):
                first = [a for a in desk.pulse() if a['kind'] == 'entry']
                self.assertEqual(calls, [(dict(symbol='TEST', signal=row['id']), True)])
                self.assertEqual([a['action'] for a in first], ['enter'])
                self.assertIn('opened automatically', first[0]['text'])
                desk.pulse()
                self.assertEqual(len(calls), 1, 'a stock with an open trade is not bought again')
                paper[0]['status'] = 'closed'
                paper[0]['payload']['mark'] = dict(net_pnl=100., time='x')
                desk.pulse()
                self.assertEqual(len(calls), 1, 'a setup that was traded and closed is not bought a second time')
                self.assertTrue(desk.trades()['closed'][0]['auto'])

    def test_no_automatic_trade_without_a_live_price_and_a_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as root:
            desk = self.desk(root, [])
            desk.AUTO_WAIT = 0.
            self.enter(desk)
            live = desk.service.prices
            calls = []

            def failing(request, auto=False):
                calls.append(request)
                raise ValueError('Market closed or no two-sided order book for this contract. Fresh prices required.')

            desk.paper = failing
            with mock.patch('kronos.adx_desk.market_open', return_value=True):
                desk.quotes, desk.service.prices = {}, SimpleNamespace(quotes=lambda symbols: dict(quotes={}))
                self.assertEqual(desk.pulse(), [])
                self.assertEqual(calls, [], 'no live stock price: nothing is bought off the last bar close')
                desk.quotes, desk.service.prices = {}, live
                alerts = desk.pulse() + desk.pulse() + desk.pulse() + desk.pulse()
                self.assertEqual(len(calls), 3, 'three attempts, then it stops')
                self.assertEqual([a['action'] for a in alerts], ['missed'])
                row = next(r for r in desk.board()['rows'] if r['verdict'] == 'enter')
                self.assertIn('automatic paper trade failed', row['why'])

    def test_hourly_trades_are_kept_out_of_the_shared_positions_and_have_their_own_backup(self):
        from kronos.options_journal import OptionsJournal, SEPARATE
        from kronos.storage import Store
        with tempfile.TemporaryDirectory() as root:
            journal = OptionsJournal(Store(Path(root) / 'r.sqlite3'))
            for k, (source, symbol) in enumerate((('desk', 'TEST'), ('adx', 'DAILY'), ('adx1h', 'TEST'))):   # the 1-hour trade is in a stock the desk already holds
                journal.open(f'a{k}', f'c{k}', dict(symbol=symbol, source=source, legs=[], plan={}))
            with self.assertRaises(ValueError):          # the shared book still allows one open trade per stock
                journal.open('a9', 'c9', dict(symbol='TEST', source='adx', legs=[], plan={}))
            self.assertEqual(journal.open('a8', 'c8', dict(symbol='OTHER', source='adx1h', legs=[], plan={}))['status'], 'open')
            with self.assertRaises(ValueError):          # ... and so does the 1-hour book
                journal.open('a7', 'c7', dict(symbol='OTHER', source='adx1h', legs=[], plan={}))
            every = journal.paper()
            shown = journal.paper(hide=SEPARATE)
            self.assertEqual(len(every), 4)
            self.assertEqual(sorted(t['payload']['source'] for t in shown), ['adx', 'desk'])
            self.assertEqual(journal.paper(every[0]['id'])['id'], every[0]['id'], 'a lookup by id still finds a hidden trade')
            own = json.loads((journal.backup_dir / 'adx_1h_trades.json').read_text(encoding='utf-8'))
            self.assertEqual([r['payload']['source'] for r in own], ['adx1h', 'adx1h'])
            combined = json.loads((journal.backup_dir / 'paper_trades.json').read_text(encoding='utf-8'))
            self.assertEqual(len(combined), 4, 'the combined backup, which a restore reads, keeps every trade')

    def test_the_daily_desk_never_trades_by_itself(self):
        self.assertFalse(AdxDesk.AUTO)

    def test_hourly_trade_times_out_in_bars_counted_after_entry(self):
        with tempfile.TemporaryDirectory() as root:
            t = Desk().trade(1, None, status='open')
            t['payload'].update(source='adx1h')
            desk = self.desk(root, [t])
            frame = desk.bars.frames['TEST']
            t['created'] = frame.index[-20].isoformat() + '+05:30'
            last = float(frame.close.iloc[-1])
            t['payload']['entry_spot'] = last
            t['payload']['adx'].update(trigger=last, stop=last - 1000., target=last + 2000.)   # far from both, so only time can end it
            t['payload']['plan']['expiry'] = '2099-12-31'
            self.assertEqual(len(desk._after(desk._views('TEST', None)[0], t['created'])), 20)
            out = desk.view('trades')['open'][0]
            self.assertEqual(out['action'], 'exit')
            self.assertIn('hourly bars', out['advice'])

    def test_candles_fall_back_to_definedge_minutes_and_drop_the_forming_hour(self):
        class Api:
            instruments = {'TEST': '1'}
            source = SimpleNamespace(benchmark_id=lambda: '26000', _limiter=SimpleNamespace(wait=lambda: None), session_key='k')

            def ready(self):
                pass

            def _get(self, *a, **k):
                raise ValueError('FYERS login expired.')

        def minutes(hh, mm, count):
            """`count` consecutive 1-minute candles from hh:mm, in the Definedge line format."""
            out = []
            for k in range(count):
                t = hh * 60 + mm + k
                price = 100 + t / 10
                out.append(f'12112026{t // 60:02d}{t % 60:02d},{price},{price + 1},{price - 1},{price + .5},10')
            return out

        def fetch(lines, due=datetime(2026, 11, 12, 10, 15)):
            reply = SimpleNamespace(status_code=200, text='\n'.join(lines), raise_for_status=lambda: None)
            with tempfile.TemporaryDirectory() as root, mock.patch('kronos.adx_hour.requests.get', return_value=reply):
                return adx_hour.HourBars(Api(), Path(root) / 'h.pkl')._fetch('TEST', due)

        # a pre-open minute, two full hours, and a few minutes of the hour still forming
        frame = fetch(minutes(9, 7, 1) + minutes(9, 15, 120) + minutes(11, 15, 5))
        self.assertEqual([str(t) for t in frame.index], ['2026-11-12 09:15:00', '2026-11-12 10:15:00'],
                         'the pre-open minute and the hour after `due` are dropped')
        first = frame.iloc[0]
        for got, want in zip((first.open, first.high, first.low, first.close), (100 + 555 / 10, 100 + 614 / 10 + 1, 100 + 555 / 10 - 1, 100 + 614 / 10 + .5)):
            self.assertAlmostEqual(got, want, places=6)       # open of 09:15, high and low across the hour, close of 10:14
        # an hour built from three minutes is a feed gap, not a candle: it is not accepted
        thin = fetch(minutes(9, 15, 60) + minutes(10, 15, 1) + minutes(10, 30, 1) + minutes(11, 0, 1), due=datetime(2026, 11, 12, 11, 15))
        self.assertEqual([str(t) for t in thin.index], ['2026-11-12 09:15:00'])


if __name__ == '__main__':
    unittest.main()
