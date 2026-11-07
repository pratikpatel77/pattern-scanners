from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from kronos.options_market import IST
from kronos.sensibull import SensibullFeed, parse_oi, strike_changes, clean_symbol, combine_months
from kronos.storage import Store


def payload(call=1000, put=500, stamp='2026-11-06T09:00:00Z'):
    return {'success': True, 'payload': {
        'input': {'date': '2026-11-06', 'expiries': {'2026-11-23': {'is_enabled': True, 'is_weekly': False}, '2026-12-29': {'is_enabled': False, 'is_weekly': False}}},
        'intraday_last_available_timestamp': stamp, 'current_ltp': 2520, 'prev_ltp': 2510, 'atm_strike': 2500, 'pcr': .45,
        'total_call_oi': call+947100, 'total_put_oi': put+74525, 'total_call_oi_change': call+947100-224475, 'total_put_oi_change': put+74525-14150,
        'per_strike_data': {'2500': {'call_oi': call, 'put_oi': put, 'prev_call_oi': 900, 'prev_put_oi': 400},
                            '2700': {'call_oi': 947100, 'put_oi': 74525, 'prev_call_oi': 223575, 'prev_put_oi': 13750}}}}


class SensibullTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.feed = SensibullFeed(Store(Path(self.tmp.name) / 't.sqlite3'))
        self.now = datetime(2026, 11, 6, 10, 0, tzinfo=IST)
        self.clock = patch('kronos.sensibull.clock', side_effect=lambda: self.now)
        self.clock.start()
    def tearDown(self): self.clock.stop(); self.tmp.cleanup()

    def test_parse_normalises_and_rejects_bad_shapes(self):
        snap = parse_oi(payload(), 'MPHASIS')
        self.assertEqual(snap['strikes']['2700'], [947100, 74525, 223575, 13750])
        self.assertEqual(snap['expiries'], ['2026-11-23'])
        for bad in ({'success': False}, {'success': True, 'payload': {}}, None):
            with self.assertRaises(ValueError): parse_oi(bad, 'X')

    def test_symbol_validation(self):
        self.assertEqual(clean_symbol(' mphasis '), 'MPHASIS')
        with self.assertRaises(ValueError): clean_symbol('../etc')

    def test_strike_changes_between_snapshots(self):
        a, b = parse_oi(payload(1500, 700), 'X'), parse_oi(payload(1000, 500), 'X')
        r = strike_changes(a, b)
        self.assertEqual((r['call_change'], r['put_change']), (500, 200))

    def test_identical_exchange_time_is_not_stored_twice_and_changes_use_history(self):
        def combined(call, stamp):
            a = parse_oi(payload(call, 500, stamp), 'MPHASIS')
            b = parse_oi(payload(call, 500, stamp), 'MPHASIS')
            b['expiries'] = ['2026-12-29']
            return combine_months([a, b])
        first = combined(1000, '2026-11-06T04:00:00Z')
        self.feed._save(first)
        self.feed._save(first)
        self.assertEqual(len(self.feed.snapshots('MPHASIS')), 1)
        self.assertFalse(self.feed.changes('MPHASIS', 30)['ready'])
        self.feed._save(combined(1500, '2026-11-06T04:03:00Z'))
        self.feed._save(combined(1800, '2026-11-06T04:06:00Z'))
        self.assertFalse(self.feed.changes('MPHASIS', 30)['ready'])
        c = self.feed.changes('MPHASIS', 5)
        self.assertTrue(c['ready'])
        self.assertEqual((c['call_change'], c['put_change']), (1600, 0))
        self.assertEqual(c['covered_minutes'], 6)
        with self.assertRaises(ValueError): self.feed.changes('MPHASIS', 0)

    def test_block_pauses_requests(self):
        class R:
            status_code = 429
        class S:
            def get(self, *a, **k): r = R(); r.status_code = 200; r.raise_for_status = lambda: None; return r
            def post(self, *a, **k): return R()
        self.feed.session = S()
        with patch('kronos.sensibull.time.sleep'):
            with self.assertRaises(ValueError): self.feed._post('u', {})
            with self.assertRaisesRegex(ValueError, 'pausing'): self.feed._post('u', {})
        self.assertGreater(self.feed.status()['paused_seconds'], 800)

    def test_watchlist_defaults_and_validation(self):
        self.assertEqual(self.feed.watch(), ['BANKNIFTY', 'NIFTY'])
        self.assertEqual(self.feed.set_watch(['mphasis', 'MPHASIS', 'tcs']), ['MPHASIS', 'TCS'])
        with self.assertRaises(ValueError): self.feed.set_watch([])


if __name__ == '__main__':
    unittest.main()
