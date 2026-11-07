from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import tempfile
import unittest

from kronos.sensibull import parse_oi, combine_months, SensibullFeed, strike_changes
from kronos.storage import Store
from kronos.options_oi import build_context, collect_context, apply_context, history_context, UNAVAILABLE, CONFLICT
from kronos.options_market import IST
from kronos.options import classify


def raw(expiry='2026-11-23', stamp='2026-11-06T04:30:00Z', calls=1000, puts=1100, pc=1000, pp=900):
    return {'success': True, 'payload': {
        'input': {'date': '2026-11-06', 'expiries': {e: {'is_weekly': False, 'is_enabled': e == expiry}
                   for e in ('2026-11-23','2026-12-29')}},
        'intraday_last_available_timestamp': stamp, 'current_ltp': 101, 'prev_ltp': 100, 'atm_strike': 100,
        'total_call_oi': calls, 'total_put_oi': puts, 'total_call_oi_change': calls-pc, 'total_put_oi_change': puts-pp,
        'per_strike_data': {'100': {'call_oi': calls, 'put_oi': puts, 'prev_call_oi': pc, 'prev_put_oi': pp}}}}


def snapshot(**kwargs):
    return combine_months([parse_oi(raw(**kwargs), 'TEST'), parse_oi(raw(expiry='2026-12-29', **kwargs), 'TEST')])


def plan(bias='bullish'):
    return dict(score=.7, reasons=[], expiry='2026-11-23', name='Call debit spread', bias=bias, legs=[],
                scenario_profit_pct=63, max_loss=500, stressed_mean_net=150)


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026,11,6,10,1,tzinfo=IST)
        p=patch('kronos.options_oi.clock', return_value=self.now); p.start(); self.addCleanup(p.stop)

    def context(self, snap=None, mode='Live', histories=None, windows=None):
        return build_context(snap or snapshot(), windows or {}, histories or {}, 101, mode, '2026-11-06')

    def test_combined_pcr_is_ratio_of_sums_and_day_changes(self):
        a=parse_oi(raw(), 'TEST');b=parse_oi(raw(expiry='2026-12-29',calls=100,puts=500,pc=100,pp=400),'TEST')
        s=combine_months([a,b])
        self.assertAlmostEqual(s['pcr'],1600/1100)
        self.assertAlmostEqual(s['pcr_change'],300/1100)
        self.assertEqual(s['strikes']['100'],[1100,1600,1100,1300])

    def test_bad_chain_values_and_totals_fail_closed(self):
        for field, value in [('call_oi',-1),('call_oi',float('nan')),('call_oi',True),('call_oi',1.5),('put_oi',None)]:
            p=raw();p['payload']['per_strike_data']['100'][field]=value
            with self.assertRaises(ValueError):parse_oi(p,'TEST')
        p=raw();p['payload']['total_call_oi']=999
        with self.assertRaises(ValueError):parse_oi(p,'TEST')

    def test_missing_previous_is_unknown_not_zero(self):
        p=raw();del p['payload']['per_strike_data']['100']['prev_call_oi']
        s=parse_oi(p,'TEST');self.assertIsNone(s['call_oi_change']);self.assertIsNone(s['pcr_change'])

    def test_zero_call_oi_has_no_ratio(self):
        s=parse_oi(raw(calls=0,pc=0),'TEST');self.assertIsNone(s['pcr']);self.assertIsNone(s['pcr_change'])

    def test_scope_and_timestamp_mismatches_fail(self):
        a=parse_oi(raw(),'TEST');b=deepcopy(a)
        with self.assertRaises(ValueError):combine_months([a,b])
        b=parse_oi(raw(expiry='2026-12-29',stamp='2026-11-06T04:34:00Z'),'TEST')
        with self.assertRaises(ValueError):combine_months([a,b])
        b=parse_oi(raw(expiry='2026-12-29',stamp='2026-11-06T04:31:00Z'),'TEST')
        self.assertFalse(self.context(combine_months([a,b]))['available'])

    def test_missing_next_calendar_month_is_rejected(self):
        a=parse_oi(raw(),'TEST');b=parse_oi(raw(expiry='2026-12-29'),'TEST')
        b['expiries']=['2027-01-26']
        with self.assertRaises(ValueError):combine_months([a,b])

    def test_stale_future_and_eod_date_checks(self):
        s=snapshot(stamp='2026-11-06T04:00:00Z');self.assertFalse(self.context(s)['available'])
        self.assertTrue(self.context(s,mode='EOD')['available'])
        s=snapshot(stamp='2026-11-06T05:00:00Z');self.assertFalse(self.context(s)['available'])
        c=build_context(snapshot(),{}, {},101,'EOD','2026-11-05');self.assertFalse(c['available'])
        c=build_context(snapshot(),{}, {},105,'Live',None);self.assertFalse(c['available'])

    def test_price_confirmed_bias_bounded_and_no_probability_change(self):
        c=self.context();self.assertEqual(c['bias'],'bullish')
        p=plan();apply_context(p,c);self.assertGreater(p['score'],.7)
        for k,v in [('scenario_profit_pct',63),('max_loss',500),('stressed_mean_net',150)]:self.assertEqual(p[k],v)
        once=p['score'];apply_context(p,c);self.assertEqual(p['score'],once)
        p=plan('bearish');apply_context(p,c);self.assertIn(CONFLICT,p['reasons']);self.assertEqual(classify(p['reasons']),'Research only')
        self.assertLessEqual(abs(p['oi']['score_adjustment']),.08)

    def test_pcr_alone_cannot_set_direction(self):
        s=snapshot();s['spot_change_pct']=0
        for p in s['by_expiry'].values():p['spot_change_pct']=0
        self.assertEqual(self.context(s)['bias'],'neutral')

    def test_conflicting_windows_do_not_boost(self):
        c=self.context(windows={'30':dict(ready=True,spot_change_pct=-1,pcr_change=-.1)})
        self.assertEqual(c['bias'],'mixed');p=plan();apply_context(p,c);self.assertEqual(p['score'],.7)

    def test_roll_is_described_not_counted_as_new_direction(self):
        a=parse_oi(raw(calls=800,puts=800,pc=1000,pp=1000),'TEST')
        b=parse_oi(raw(expiry='2026-12-29',calls=400,puts=400,pc=200,pp=200),'TEST')
        c=self.context(combine_months([a,b]))
        self.assertTrue(c['rollover']['possible_roll']);self.assertEqual(c['rollover']['combined_change'],0)
        self.assertEqual(c['bias'],'neutral');self.assertAlmostEqual(c['rollover']['next_month_share_pct'],100/3)

    def test_trade_levels_never_use_another_expiry(self):
        c=self.context();p=plan();p.update(name='Put credit spread',expiry='2027-01-26',legs=[dict(qty=-1,type='PE',strike=105)])
        apply_context(p,c);self.assertAlmostEqual(p['oi']['score_adjustment'],.02)
        self.assertIn('outside',p['oi']['notes'][-1])

    def test_history_sorted_scoped_and_daily_futures_not_live_vote(self):
        s=snapshot();exp=s['expiries']
        rows=[dict(at='2026-11-06',ltp=99,oi=1100,expiries=exp),dict(at='2026-11-05',ltp=100,oi=1000,expiries=exp)]
        c=self.context(histories={'fut':{'rows':rows}});self.assertEqual(c['bias'],'bullish')
        c=self.context(mode='EOD',histories={'fut':{'rows':rows}});self.assertEqual(c['bias'],'mixed')
        rows[1]['expiries']=['2026-10-27','2026-11-23']
        self.assertFalse(history_context({'rows':rows},s,'fut')['ready'])

    def test_missing_feed_is_visible_research_only(self):
        c=collect_context(None,'TEST',101);p=plan();apply_context(p,c)
        self.assertIn(UNAVAILABLE,p['reasons']);self.assertEqual(classify(p['reasons']),'Research only')


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.feed=SensibullFeed(Store(Path(self.tmp.name)/'db.sqlite3'))

    def test_live_explicitly_requests_next_and_reuses_cache(self):
        def response(url, body):
            exp=body['expiries']
            choice=next(k for k,v in exp.items() if v['is_enabled']) if exp else '2026-11-23'
            return raw(expiry=choice)
        with patch.object(self.feed,'_post',side_effect=response) as post:
            s=self.feed.live('TEST');self.assertEqual(len(s['by_expiry']),2)
            self.feed.live('TEST');self.assertEqual(post.call_count,2)
        self.assertEqual(len(self.feed.snapshots('TEST')),1)

    def test_wrong_expiry_response_is_rejected(self):
        with patch.object(self.feed,'_post',return_value=raw()):
            with self.assertRaises(ValueError):self.feed.live('TEST')
        self.assertEqual(self.feed.snapshots('TEST'),[])

    def test_long_window_and_recording_gaps_are_not_fabricated(self):
        for minute in (0,3,6):
            self.feed._save(snapshot(stamp=f'2026-11-06T04:{minute:02d}:00Z'))
        self.assertTrue(self.feed.changes('TEST',5)['ready'])
        self.assertFalse(self.feed.changes('TEST',30)['ready'])
        self.feed._save(snapshot(stamp='2026-11-06T04:30:00Z'))
        self.assertFalse(self.feed.changes('TEST',30)['ready'])

    def test_changed_strike_or_expiry_is_not_zero_filled(self):
        a=snapshot();b=deepcopy(a);b['strikes']['105']=b['strikes'].pop('100')
        with self.assertRaises(ValueError):strike_changes(a,b)
        b=deepcopy(a);b['expiries']=['2026-12-29','2027-01-26']
        with self.assertRaises(ValueError):strike_changes(a,b)


if __name__ == '__main__':unittest.main()
