"""Kronos guidance for open positions: direction, hold / adjust / exit decisions. No network."""
from datetime import timedelta
import unittest

from kronos.options_market import clock
from kronos.position_watch import direction, evaluate


def leg(qty, strike, kind, entry):
    return dict(qty=qty, lot=100, strike=float(strike), type=kind, entry_fill=entry, symbol=f'T{strike}{kind}')


def trade(legs):
    expiry = str((clock() + timedelta(days=20)).date())
    return dict(id='t1', payload=dict(symbol='TEST', legs=legs, entry_charges=dict(stt=5.), exit_by=expiry,
                                      plan=dict(expiry=expiry, max_profit=500., max_loss=1500., exit_by=expiry)))


def analysis(end_price, bias='Upside bias', lower=900., upper=1100.):
    days = [str((clock() + timedelta(days=i + 1)).date()) for i in range(5)]
    paths = []
    for k in range(32):
        target = end_price + (k - 16) * 2
        paths.append([dict(date=d, open=1000., high=max(1000., 1000 + (target - 1000) * (i + 1) / 5) + 3,
                           low=min(1000., 1000 + (target - 1000) * (i + 1) / 5) - 3, close=1000 + (target - 1000) * (i + 1) / 5)
                      for i, d in enumerate(days)])
    history = [dict(date=f'2026-10-{d:02d}', open=1000., high=1010., low=990., close=1000.) for d in range(1, 31)]
    return dict(id='a1', iv=25., lower=lower, upper=upper, created=clock().isoformat(), data_mode='Live',
                forecast=dict(last_close=1000., history=history, paths=paths,
                              summary=dict(bias=bias, median_end=end_price, atr=20.)))


PUT_SPREAD = [leg(-1, 960, 'PE', 12.), leg(1, 940, 'PE', 6.)]


class DirectionTests(unittest.TestCase):
    def test_far_otm_credit_spread_is_bullish_not_range(self):
        self.assertEqual(direction(0, [dict(qty=-1, lot=100, strike=660, type='PE'), dict(qty=1, lot=100, strike=630, type='PE')], 702), 'bullish')
        self.assertEqual(direction(0, [dict(qty=-1, lot=100, strike=740, type='CE'), dict(qty=1, lot=100, strike=760, type='CE')], 702), 'bearish')


class GuidanceTests(unittest.TestCase):
    def test_hold_when_kronos_still_supports_the_trade(self):
        g = evaluate(trade(PUT_SPREAD), analysis(1030.), 1000., 50.)
        self.assertEqual(g['action'], 'Hold')
        self.assertGreater(g['stats']['profit_pct'], 80)
        self.assertIsNone(g['stop']['upper'])        # a bullish trade has no upside stop

    def test_adjust_when_paths_finish_beyond_the_short_strike(self):
        g = evaluate(trade(PUT_SPREAD), analysis(955., bias='Mixed outlook'), 1000., -100.,
                     contracts=[dict(type='PE', strike=k) for k in (880, 900, 920, 940, 960)])
        self.assertIn(g['action'], ('Adjust', 'Exit'))
        if g['action'] == 'Adjust':
            self.assertIn('900', g['suggestion'])   # roll beyond the Kronos low zone (900)

    def test_call_backspread_is_not_flagged_when_paths_finish_above_the_short_call(self):
        # INFY case: sell 1 x 1134 CE, buy 2 x 1140 CE. Finishing above the short strike is the profit case.
        backspread = [leg(-1, 1005, 'CE', 30.), leg(2, 1010, 'CE', 27.)]
        g = evaluate(trade(backspread), analysis(1090.), 1000., None)
        self.assertGreater(g['stats']['short_breach_pct'], 90)
        self.assertNotEqual(g['action'], 'Adjust', g['reasons'])

    def test_credit_call_spread_is_still_flagged_when_paths_finish_above_the_short_call(self):
        spread = [leg(-1, 1005, 'CE', 30.), leg(1, 1030, 'CE', 12.)]
        g = evaluate(trade(spread), analysis(1090., bias='Mixed outlook'), 1000., None)
        self.assertIn(g['action'], ('Adjust', 'Exit'))

    def test_exit_when_stock_crosses_the_kronos_stop(self):
        g = evaluate(trade(PUT_SPREAD), analysis(1030.), 950., -300.)
        self.assertEqual(g['action'], 'Exit')
        self.assertIn('Kronos stop', g['reasons'][0])

    def test_book_profit_at_half_of_max_profit(self):
        self.assertEqual(evaluate(trade(PUT_SPREAD), analysis(1030.), 1000., 300.)['action'], 'Book profit')

    def test_missing_live_quotes_cannot_confirm_hold_or_profit_booking(self):
        for pnl in (50., 300.):
            g = evaluate(trade(PUT_SPREAD), analysis(1030.), 1000., pnl, quote_issue='Live quotes unavailable.')
            self.assertEqual(g['action'], 'Watch')
            self.assertIsNone(g['ltp_pnl'])
            self.assertEqual(g['reasons'][0], 'Live quotes unavailable.')

    def test_event_before_exit_is_flagged(self):
        e = dict(upcoming=[dict(date=str((clock() + timedelta(days=1)).date()), title='Financial results', days_away=1)])
        g = evaluate(trade(PUT_SPREAD), analysis(1030.), 1000., 50., events=e)
        self.assertTrue(g['event_before_exit'])
        self.assertEqual(g['action'], 'Adjust')


if __name__ == '__main__':
    unittest.main()
