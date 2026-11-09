"""Entry board: pattern state, Kronos agreement and the ripe / watch / wait / skip verdicts. No network."""
from datetime import date
from types import SimpleNamespace
import unittest

from kronos.desk import FastDesk
from kronos.entry_board import evaluate, kronos_view, pattern_state

TODAY = date(2026, 11, 5)


def row(symbol='AAA', direction='long', pivot=100., stop=95., score=80., timeframe='D', **extra):
    target = pivot + (pivot - stop) if direction == 'long' else pivot - (stop - pivot)
    return dict(symbol=symbol, scanner='stage', title='Stage 2', direction=direction, timeframe=timeframe, score=score,
                date='2026-11-04', label='Breakout', close=pivot * .99, pivot=pivot, stop=stop, target=target, **extra)


def forecast(bias='Upside bias', asof='2026-11-05', last=100., median=103., events=None, checked=True):
    return dict(forecast_summary=dict(bias=bias, asof=asof, last_close=last, median_end=median),
                events=dict(checked=checked, events=events or []), forecast_end='2026-11-12',
                best=dict(name='Bull call spread', status='Paper candidate'))


def one(rows, analyses, quotes):
    return evaluate(rows, analyses, quotes, TODAY)[0]


class StateTests(unittest.TestCase):
    def test_long_states(self):
        self.assertEqual(pattern_state('long', 90, 100, 95)[0], 'invalidated')
        self.assertEqual(pattern_state('long', 95, 100, 95)[0], 'invalidated')
        self.assertEqual(pattern_state('long', 90.5, 100, 85)[0], 'waiting')
        self.assertEqual(pattern_state('long', 99, 100, 95)[0], 'near')
        self.assertEqual(pattern_state('long', 101, 100, 95)[0], 'triggered')
        self.assertEqual(pattern_state('long', 104, 100, 95)[0], 'extended')

    def test_short_states_mirror_the_long_ones(self):
        self.assertEqual(pattern_state('short', 105, 100, 105)[0], 'invalidated')
        self.assertEqual(pattern_state('short', 101, 100, 105)[0], 'near')
        self.assertEqual(pattern_state('short', 99, 100, 105)[0], 'triggered')
        self.assertEqual(pattern_state('short', 96, 100, 105)[0], 'extended')
        self.assertEqual(pattern_state('short', 110, 100, 120)[0], 'waiting')


class KronosTests(unittest.TestCase):
    def test_alignment_follows_pattern_direction(self):
        up, down = forecast('Upside bias'), forecast('Downside bias')
        self.assertEqual(kronos_view('long', up, 100, TODAY)['align'], 'agree')
        self.assertEqual(kronos_view('short', up, 100, TODAY)['align'], 'against')
        self.assertEqual(kronos_view('short', down, 100, TODAY)['align'], 'agree')
        self.assertEqual(kronos_view('long', forecast('Mixed outlook'), 100, TODAY)['align'], 'mixed')
        self.assertIsNone(kronos_view('long', None, 100, TODAY))

    def test_old_or_drifted_forecast_is_not_fresh(self):
        self.assertTrue(kronos_view('long', forecast(), 100, TODAY)['fresh'])
        self.assertFalse(kronos_view('long', forecast(asof='2026-10-28'), 100, TODAY)['fresh'])
        self.assertFalse(kronos_view('long', forecast(), 104, TODAY)['fresh'])
        self.assertEqual(kronos_view('long', forecast(), 100, TODAY)['expected_pct'], 3.0)


class VerdictTests(unittest.TestCase):
    def test_ripe_needs_trigger_zone_fresh_agreeing_forecast_and_no_event(self):
        e = one([row()], {'AAA': forecast()}, {'AAA': dict(ltp=99.5, change_pct=.4, fresh=True, source='FYERS')})
        self.assertEqual((e['verdict'], e['direction'], e['state']), ('ripe', 'bullish', 'near'))
        self.assertTrue(e['live'])
        self.assertEqual(e['best_idea']['name'], 'Bull call spread')

    def test_bearish_ripe(self):
        r = row(direction='short', pivot=100., stop=105.)
        e = one([r], {'AAA': forecast('Downside bias', median=97.)}, {'AAA': dict(ltp=99.4,fresh=True)})
        self.assertEqual((e['verdict'], e['direction'], e['state']), ('ripe', 'bearish', 'triggered'))

    def test_kronos_against_is_skipped_with_the_reason(self):
        e = one([row()], {'AAA': forecast('Downside bias')}, {'AAA': dict(ltp=99.5)})
        self.assertEqual(e['verdict'], 'skip')
        self.assertIn('against', e['reasons'][0])

    def test_missing_mixed_and_old_forecasts_only_reach_watch(self):
        q = {'AAA': dict(ltp=99.5)}
        for analysis in (None, forecast('Mixed outlook'), forecast(asof='2026-10-20')):
            e = one([row()], {'AAA': analysis} if analysis else {}, q)
            self.assertEqual(e['verdict'], 'watch', analysis)
            self.assertTrue(e['reasons'])

    def test_event_in_trade_period_blocks_ripe(self):
        event = dict(title='Quarterly results', date='2026-11-09')
        e = one([row()], {'AAA': forecast(events=[event])}, {'AAA': dict(ltp=99.5)})
        self.assertEqual(e['verdict'], 'watch')
        self.assertIn('Quarterly results', ' '.join(e['reasons']))
        self.assertEqual(e['event']['date'], '2026-11-09')

    def test_extended_and_invalidated_are_skipped(self):
        for price, text in ((104, 'chasing'), (94, 'stop')):
            e = one([row()], {'AAA': forecast()}, {'AAA': dict(ltp=price)})
            self.assertEqual(e['verdict'], 'skip')
            self.assertIn(text, e['reasons'][0])

    def test_far_from_trigger_waits_unless_kronos_confirms_a_close_setup(self):
        self.assertEqual(one([row(stop=85.)], {'AAA': forecast()}, {'AAA': dict(ltp=90.)})['verdict'], 'wait')
        self.assertEqual(one([row()], {'AAA': forecast()}, {'AAA': dict(ltp=97.5)})['verdict'], 'watch')
        self.assertEqual(one([row()], {}, {'AAA': dict(ltp=97.5)})['verdict'], 'wait')

    def test_scan_close_is_used_when_there_is_no_live_price(self):
        e = one([row()], {'AAA': forecast()}, {})
        self.assertFalse(e['live'])
        self.assertEqual(e['source'], 'scan close')
        self.assertEqual(e['price'], 99.)


class RankingTests(unittest.TestCase):
    def test_groups_sort_ripe_first_then_by_strength(self):
        rows = [row('LOW', score=50), row('HIGH', score=90), row('BADK', score=99), row('FAR', score=95, stop=85.)]
        analyses = {s: forecast() for s in ('LOW', 'HIGH', 'FAR')} | {'BADK': forecast('Downside bias')}
        quotes = {s: dict(ltp=99.5,fresh=True) for s in ('LOW', 'HIGH', 'BADK')} | {'FAR': dict(ltp=90.)}
        out = evaluate(rows, analyses, quotes, TODAY)
        self.assertEqual([e['symbol'] for e in out], ['HIGH', 'LOW', 'FAR', 'BADK'])
        self.assertEqual([e['verdict'] for e in out], ['ripe', 'ripe', 'wait', 'skip'])

    def test_one_entry_per_stock_and_direction_with_the_best_pattern(self):
        rows = [row(score=60, timeframe='D'), row(score=85, timeframe='W', pivot=100., stop=94.),
                row(direction='short', pivot=90., stop=96.)]
        out = evaluate(rows, {}, {}, TODAY)
        self.assertEqual(len(out), 2)
        long = next(e for e in out if e['direction'] == 'bullish')
        self.assertEqual((long['pattern']['score'], long['pattern']['timeframe'], long['pattern']['more']), (85, 'D+W', 1))


class BoardAssemblyTests(unittest.TestCase):
    def test_board_filters_to_fno_attaches_forecast_and_survives_quote_failure(self):
        setups = [dict(symbol=s, scanner='stage', timeframe='D', date='2026-11-04', label='Breakout', score=80,
                       close=99., pivot=100., support=95.) for s in ('AAA', 'NOFNO')]
        journal = SimpleNamespace(analyses=lambda: [dict(symbol='AAA', **forecast(), candidates=[])])
        options = SimpleNamespace(journal=journal, market=SimpleNamespace(universe=lambda: ['AAA']),
                                  compact=lambda a: dict(best=dict(name='Bull call spread', status='Paper candidate')))

        def down(symbols):
            raise ValueError('down')
        service = SimpleNamespace(store=SimpleNamespace(settings=lambda: SimpleNamespace(target_r=1., min_target_pct=5.)),
                                  setups=lambda: setups, options=options, prices=SimpleNamespace(quotes=down))
        board = FastDesk(service).entry_board()
        self.assertEqual([e['symbol'] for e in board['entries']], ['AAA'])
        self.assertEqual(board['quotes_failed'], 1)
        self.assertEqual(board['with_forecast'], 1)
        self.assertEqual(board['entries'][0]['best_idea']['name'], 'Bull call spread')
        self.assertEqual(board['counts']['ripe'] + board['counts']['watch'] + board['counts']['wait'] + board['counts']['skip'], 1)


class TrackingTests(unittest.TestCase):
    """Transitions into and out of Ready are logged while the app runs; the first pass is only a baseline."""

    def desk(self):
        setups = [dict(symbol='AAA', scanner='stage', timeframe='D', date='2026-11-04', label='Breakout', score=80,
                       close=99., pivot=100., support=95.)]
        self.price = 99.5
        self.bias = 'Upside bias'
        journal = SimpleNamespace(analyses=lambda: [dict(symbol='AAA', **forecast(self.bias), candidates=[])])
        options = SimpleNamespace(journal=journal, market=SimpleNamespace(universe=lambda: ['AAA']),
                                  compact=lambda a: dict(best=None))
        service = SimpleNamespace(store=SimpleNamespace(settings=lambda: SimpleNamespace(target_r=1., min_target_pct=5.)),
                                  setups=lambda: setups, options=options,
                                  prices=SimpleNamespace(quotes=lambda symbols: dict(quotes={s: dict(ltp=self.price,fresh=True) for s in symbols})))
        desk = FastDesk(service)
        desk.BOARD_TTL = 0
        return desk

    def test_first_pass_is_a_baseline_then_changes_are_logged(self):
        desk = self.desk()
        first = desk.entry_board()
        self.assertEqual(first['events'], [])                     # already Ready at start: not announced
        self.assertEqual(first['entries'][0]['verdict'], 'ripe')
        self.bias = 'Downside bias'
        lost = desk.entry_board()['events']
        self.assertEqual([(e['kind'], e['symbol']) for e in lost], [('lost', 'AAA')])
        self.bias = 'Upside bias'
        board = desk.entry_board()
        self.assertEqual([e['kind'] for e in board['events']], ['lost', 'ready'])
        self.assertEqual([e['id'] for e in board['events']], [1, 2])
        self.assertTrue(board['entries'][0]['since'])

    def test_unchanged_verdict_keeps_its_since_time_and_logs_nothing(self):
        desk = self.desk()
        a = desk.entry_board()['entries'][0]['since']
        self.assertEqual(desk.entry_board()['entries'][0]['since'], a)
        self.assertEqual(desk.board_events, [])

    def test_board_reports_the_auto_forecast_status_and_never_asks_for_a_manual_run(self):
        desk = self.desk()
        desk.service.autoforecast = SimpleNamespace(status=lambda: dict(running='AAA', queued=2, message='Running Kronos for AAA.'))
        board = desk.entry_board()
        self.assertEqual(board['auto']['running'], 'AAA')
        entry = one([row()], {}, {'AAA': dict(ltp=99.5, fresh=True)})
        self.assertEqual(entry['verdict'], 'watch')
        self.assertEqual(entry['reasons'], ['No Kronos forecast yet: the app runs one automatically'])
        far = one([row(stop=80.)], {}, {'AAA': dict(ltp=92., fresh=True)})
        self.assertIn('once price is within 2% of the trigger', far['reasons'][0])
        self.assertNotIn('open the stock', ' '.join(entry['reasons'] + far['reasons']))

    def test_board_without_a_watcher_has_no_auto_status(self):
        self.assertIsNone(self.desk().entry_board()['auto'])

    def test_background_loop_starts_and_stops_with_the_app(self):
        desk = self.desk()
        desk.start_board_watch()
        self.assertTrue(desk.board_thread.is_alive())
        desk.close_board_watch()
        desk.board_thread.join(2)
        self.assertFalse(desk.board_thread.is_alive())


if __name__ == '__main__':
    unittest.main()
