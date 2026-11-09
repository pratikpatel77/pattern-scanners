"""Background Kronos forecasts for stocks near their trigger, the person-first rule, and the daily pattern refresh. No network."""
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from kronos import autoforecast
from kronos.autoforecast import AutoForecast, STOCK_RETRY
from kronos.freshness import MarketRefresh
from kronos.options import OptionsDesk
from kronos.options_market import IST
from kronos.service import BusyError
from kronos.storage import Store


def entry(symbol, state='near', fresh=None):
    kronos = None if fresh is None else dict(fresh=fresh)
    return dict(symbol=symbol, state=state, kronos=kronos)


class Harness:
    """A service whose single job slot and job records are plain Python objects."""
    def __init__(self, entries, outcome=None):
        self.entries = entries
        self.jobs, self.submitted = {}, []
        self.outcome = outcome or (lambda symbol: dict(status='complete', result=dict(stocks=[dict(has_forecast=True)])))
        self.busy = False
        self.service = SimpleNamespace(
            active=None, desk=SimpleNamespace(entry_board=lambda: dict(entries=self.entries)),
            options=SimpleNamespace(submit=self.submit), store=SimpleNamespace(get=lambda key: self.jobs[key]))

    def submit(self, request, auto=False):
        if self.busy:
            raise BusyError('A Kronos job is already running.')
        key = f"job{len(self.submitted)}"
        self.submitted.append((request['symbol'], auto))
        self.jobs[key] = dict(id=key, **self.outcome(request['symbol']))
        return dict(id=key, status='queued')


class CandidateTests(unittest.TestCase):
    def test_only_stocks_near_or_past_the_trigger_without_a_current_forecast(self):
        auto = AutoForecast(None)
        entries = [entry('NEAR'), entry('TRIG', 'triggered'), entry('FAR', 'waiting'), entry('LATE', 'extended'),
                   entry('DEAD', 'invalidated'), entry('FRESH', fresh=True), entry('STALE', fresh=False), entry('NEAR')]
        self.assertEqual(auto.candidates(entries, 1000.), ['NEAR', 'TRIG', 'STALE'])   # order kept, one row per stock

    def test_a_stock_just_tried_waits_before_the_next_attempt(self):
        auto = AutoForecast(None)
        auto.tried['NEAR'] = 1000.
        self.assertEqual(auto.candidates([entry('NEAR')], 1000. + STOCK_RETRY - 1), [])
        self.assertEqual(auto.candidates([entry('NEAR')], 1000. + STOCK_RETRY), ['NEAR'])


class StepTests(unittest.TestCase):
    def watcher(self, entries, **kw):
        h = Harness(entries, **kw)
        return h, AutoForecast(h.service)

    def test_runs_the_best_candidate_as_a_background_job(self):
        h, auto = self.watcher([entry('AAA'), entry('BBB')])
        self.assertTrue(auto.step(100.))
        self.assertEqual(h.submitted, [('AAA', True)])
        self.assertEqual(auto.status()['message'], 'Forecast saved for AAA.')
        self.assertEqual(auto.status()['last']['symbol'], 'AAA')

    def test_next_pass_moves_on_to_the_next_stock(self):
        h, auto = self.watcher([entry('AAA'), entry('BBB')])
        auto.step(100.); auto.step(101.)
        self.assertEqual([s for s, _ in h.submitted], ['AAA', 'BBB'])
        self.assertFalse(auto.step(102.))     # both tried: nothing left until the retry delay
        self.assertEqual(auto.status()['queued'], 0)

    def test_nothing_to_do_when_every_near_stock_has_a_current_forecast(self):
        h, auto = self.watcher([entry('AAA', fresh=True), entry('FAR', 'waiting')])
        self.assertFalse(auto.step(100.))
        self.assertEqual(h.submitted, [])
        self.assertIn('current forecast', auto.status()['message'])

    def test_does_not_start_while_another_job_holds_the_slot(self):
        h, auto = self.watcher([entry('AAA')])
        h.service.active = 'someone-elses-job'
        self.assertFalse(auto.step(100.))
        h.service.active = None; h.busy = True       # lost the race for the slot
        self.assertFalse(auto.step(100.))
        self.assertEqual(h.submitted, [])

    def test_a_failed_run_pauses_every_stock(self):
        h, auto = self.watcher([entry('AAA'), entry('BBB')],
                               outcome=lambda s: dict(status='failed', error='FYERS login expired'))
        self.assertTrue(auto.step(100.))
        self.assertIn('FYERS login expired', auto.status()['message'])
        self.assertFalse(auto.step(autoforecast.time.monotonic() + 1))
        self.assertEqual(len(h.submitted), 1)

    def test_a_person_cancelling_it_pauses_the_watcher_and_keeps_the_stock_due(self):
        h, auto = self.watcher([entry('AAA')], outcome=lambda s: dict(status='cancelled'))
        self.assertTrue(auto.step(100.))
        self.assertNotIn('AAA', auto.tried)
        self.assertIn('your own forecast', auto.status()['message'])
        self.assertFalse(auto.step(autoforecast.time.monotonic() + 1))

    def test_a_run_that_saved_no_forecast_says_why_and_is_retried_later(self):
        h, auto = self.watcher([entry('AAA')], outcome=lambda s: dict(
            status='complete', result=dict(stocks=[dict(has_forecast=False, reason='Stock moved more than 0.5%')])))
        auto.step(100.)
        self.assertIn('Stock moved more than 0.5%', auto.status()['message'])
        self.assertEqual(auto.tried['AAA'], 100.)

    def test_an_error_in_the_loop_never_kills_the_thread(self):
        h, auto = self.watcher([entry('AAA')])
        h.service.desk.entry_board = Mock(side_effect=ValueError('F&O stock list unavailable. Check the FYERS login.'))
        auto.delay = 0
        with patch.object(autoforecast, 'IDLE_OPEN', .01), patch.object(autoforecast, 'IDLE_CLOSED', .01):
            auto.start()
            for _ in range(100):
                if 'FYERS login' in auto.status()['message']:
                    break
                threading.Event().wait(.02)
            auto.close(); auto.thread.join(2)
        self.assertIn('FYERS login', auto.status()['message'])
        self.assertFalse(auto.thread.is_alive())


class PersonFirstTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name)/'db.sqlite3')

    def desk(self, **service):
        values = dict(store=self.store, lock=threading.Lock(), active=None, auto_job=None, events={},
                      pool=SimpleNamespace(submit=Mock()), setups=lambda: [])
        values.update(service)
        return OptionsDesk(SimpleNamespace(**values), SimpleNamespace(universe=lambda: ['AAA', 'BBB']))

    def test_the_watchers_job_is_marked_and_a_persons_job_is_not(self):
        desk = self.desk()
        job = desk.submit(dict(symbol='AAA'), auto=True)
        self.assertEqual(desk.service.auto_job, job['id'])
        self.assertTrue(job['request']['auto'])
        desk.service.active = None
        job = desk.submit(dict(symbol='BBB'))
        self.assertIsNone(desk.service.auto_job)
        self.assertNotIn('auto', job['request'])

    def test_a_person_cancels_the_background_run_and_takes_its_place(self):
        desk = self.desk(active='bg', auto_job='bg')
        cancelled = []
        def cancel(key):
            cancelled.append(key)
            threading.Timer(.05, lambda: setattr(desk.service, 'active', None)).start()   # the job ends shortly after
        desk.service.cancel = cancel
        job = desk.submit(dict(symbol='BBB'))
        self.assertEqual(cancelled, ['bg'])
        self.assertEqual(desk.service.active, job['id'])

    def test_a_person_is_not_stopped_by_someone_elses_job_and_gets_the_normal_busy_error(self):
        desk = self.desk(active='manual-batch', auto_job=None, cancel=Mock())
        with self.assertRaises(BusyError):
            desk.submit(dict(symbol='AAA'))
        desk.service.cancel.assert_not_called()

    def test_the_watcher_never_cancels_anything(self):
        desk = self.desk(active='manual-batch', auto_job=None, cancel=Mock())
        with self.assertRaises(BusyError):
            desk.submit(dict(symbol='AAA'), auto=True)
        desk.service.cancel.assert_not_called()


class PatternRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.refresh = MarketRefresh(SimpleNamespace(directory=self.dir, setups=lambda: json.loads((self.dir/'setups.json').read_text())))

    def mark(self, when):
        marker = self.dir/'patterns.done'
        marker.touch()
        os.utime(marker, (when.timestamp(), when.timestamp()))

    def test_due_when_no_scan_ever_finished(self):
        self.assertTrue(self.refresh.due(datetime(2026, 11, 9, 10, tzinfo=IST)))

    def test_due_again_after_each_weekday_close_but_not_before(self):
        self.mark(datetime(2026, 11, 6, 17, tzinfo=IST))                      # Friday evening scan
        self.assertFalse(self.refresh.due(datetime(2026, 11, 7, 18, tzinfo=IST)))   # Saturday: Friday's is the latest session
        self.assertFalse(self.refresh.due(datetime(2026, 11, 9, 10, tzinfo=IST)))   # Monday morning, market not closed yet
        self.assertFalse(self.refresh.due(datetime(2026, 11, 9, 15, 59, tzinfo=IST)))
        self.assertTrue(self.refresh.due(datetime(2026, 11, 9, 16, 1, tzinfo=IST)))  # Monday after the close

    def test_a_scan_started_before_the_close_does_not_count_as_post_close(self):
        self.mark(datetime(2026, 11, 9, 9, 42, tzinfo=IST))
        self.assertTrue(self.refresh.due(datetime(2026, 11, 9, 16, 5, tzinfo=IST)))

    def test_early_fno_publish_replaces_those_stocks_and_keeps_the_rest(self):
        (self.dir/'setups.json').write_text(json.dumps([dict(symbol='OLDFNO', pivot=1), dict(symbol='SMALLCAP', pivot=2)]))
        fresh = [dict(symbol='NEWFNO', pivot=3)]
        with patch('kronos.freshness.signal_dict', side_effect=lambda s: s):
            self.refresh._publish_partial(fresh, {'OLDFNO', 'NEWFNO'})
        saved = json.loads((self.dir/'setups.json').read_text())
        self.assertEqual(sorted(s['symbol'] for s in saved), ['NEWFNO', 'SMALLCAP'])
        self.assertTrue(self.refresh.due(datetime(2026, 11, 9, 16, 5, tzinfo=IST)))   # still due: only a full scan writes the marker

    def test_a_publish_failure_never_breaks_the_scan(self):
        refresh = MarketRefresh(SimpleNamespace(directory=self.dir/'missing', setups=lambda: []))
        refresh._publish_partial([], set())    # directory does not exist: swallowed


if __name__ == '__main__':
    unittest.main()
