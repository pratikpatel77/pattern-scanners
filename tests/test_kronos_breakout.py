"""Regression tests for Kronos outcome ordering, sampling and historical isolation."""
import copy
from datetime import date
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scanners.common import Signal
from kronos_breakout import (BreakoutRules, calculate_target_price, classify_path,
    evaluate_breakout, generate_future_timestamps, predict_signal, predict_signals,
    resolve_model_names, signal_history)
from kronos_report import validate_forecasts, write_reports
from kronos_scan import main, load_signals_from_csv


def signal(**kwargs):
    values = dict(scanner="darvas", symbol="TEST", tf="D", date=date(2026, 9, 28),
                  close=100., pivot=105., support=95., score=75., label="Setup",
                  chart={"closes": [99., 100.]})
    return Signal(**{**values, **kwargs})


def candles(closes, highs=None, lows=None, start="2026-09-29"):
    c = np.asarray(closes, float)
    return pd.DataFrame(dict(open=c, high=np.array(highs) if highs is not None else c+1,
                             low=np.array(lows) if lows is not None else c-1,
                             close=c, volume=np.full(len(c), 10000.)),
                        index=pd.bdate_range(start, periods=len(c)))


def history():
    frame = candles(np.linspace(90, 100, 80), start="2026-06-09")
    frame.index = pd.bdate_range(end="2026-09-28", periods=80)
    return frame


class CapturingPredictor:
    max_context = 512
    device = "test"

    def __init__(self):
        self.calls = []

    def predict_samples(self, **kwargs):
        self.calls.append(kwargs)
        df = candles(np.linspace(106, 114, kwargs['pred_len']))
        df.index = pd.DatetimeIndex(kwargs['y_timestamp'])
        return df, [df.copy() for _ in range(kwargs['sample_count'])]


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.s = signal()
        self.rules = BreakoutRules()

    def outcome(self, path):
        return classify_path(self.s, path, 115., self.rules)[0]

    def test_target_uses_pattern_levels(self):
        self.assertEqual(calculate_target_price(105, 95), 115)
        self.assertEqual(calculate_target_price(100, 98), 105)

    def test_touch_is_not_confirmation(self):
        self.assertEqual(self.outcome(candles([104, 104, 104], highs=[110, 110, 110])), 'no_breakout')

    def test_confirmation_streak_resets(self):
        self.assertEqual(self.outcome(candles([106, 104, 106])), 'no_breakout')

    def test_pivot_cross_without_target_is_unresolved(self):
        self.assertEqual(self.outcome(candles([106, 107, 110])), 'unresolved')

    def test_target_after_confirmation_succeeds(self):
        self.assertEqual(self.outcome(candles([106, 107, 115])), 'success')

    def test_success_is_absorbing(self):
        self.assertEqual(self.outcome(candles([106, 107, 115, 90])), 'success')

    def test_stop_before_confirmation_invalidates(self):
        self.assertEqual(self.outcome(candles([94, 106, 107, 115])), 'breakdown')

    def test_stop_on_confirmation_bar_invalidates(self):
        self.assertEqual(self.outcome(candles([106, 107, 115], lows=[105, 94, 114])), 'breakdown')

    def test_same_bar_stop_target_is_failure(self):
        self.assertEqual(self.outcome(candles([106, 107, 110], highs=[107, 108, 116], lows=[105, 106, 94])), 'fakeout')

    def test_close_back_inside_pivot_fails(self):
        self.assertEqual(self.outcome(candles([106, 107, 104, 115])), 'fakeout')

    def test_entry_candle_target_not_counted(self):
        self.assertEqual(self.outcome(candles([106, 107, 108], highs=[107, 120, 109])), 'unresolved')

    def test_extended_entry_not_counted_as_profit(self):
        self.assertEqual(self.outcome(candles([110, 120, 120])), 'extended')

    def test_already_above_trigger_is_continuation(self):
        s = signal(close=107)
        outcome, entry, _ = classify_path(s, candles([115]), 115, self.rules)
        self.assertEqual((outcome, entry), ('success', 0))

    def test_success_rate_is_path_frequency(self):
        good = candles([106, 107, 115])
        bad = candles([94, 94, 94])
        res = evaluate_breakout(self.s, good, [good]*8 + [bad]*24)
        self.assertEqual(res['success_rate'], 25)
        self.assertEqual(sum(res['outcome_counts'].values()), 32)
        self.assertEqual(res['outcome_counts']['success'], 8)
        self.assertEqual(res['verdict'], 'Breakdown Risk')
        self.assertFalse(res['calibrated'])
        self.assertLess(res['success_interval'][0], 25)
        self.assertGreater(res['success_interval'][1], 25)

    def test_mean_path_cannot_override_samples(self):
        mean = candles([106, 107, 115])
        bad = candles([94, 94, 94])
        result = evaluate_breakout(self.s, mean, [bad]*32)
        self.assertEqual(result['success_rate'], 0)
        self.assertEqual(result['verdict'], 'Breakdown Risk')

    def test_single_path_does_not_invent_odds(self):
        res = evaluate_breakout(self.s, candles([106, 107, 115]))
        self.assertEqual(res['success_rate'], 100)
        self.assertEqual(res['verdict'], 'Insufficient samples')

    def test_invalid_inputs_fail(self):
        for path in (pd.DataFrame(), candles([np.nan]), candles([-1])):
            with self.assertRaises(ValueError):
                evaluate_breakout(self.s, path)
        with self.assertRaises(ValueError):
            evaluate_breakout(signal(support=101), candles([106]))

    def test_decoder_bounds_repairs_are_reported(self):
        path = candles([106, 107, 115], highs=[100, 100, 100])
        res = evaluate_breakout(self.s, path)
        self.assertEqual(res['repaired_candles'], 3)


class IntegrationTests(unittest.TestCase):
    def test_forecast_does_not_mutate_original_chart_or_scores(self):
        s, bars, predictor = signal(), history(), CapturingPredictor()
        original = copy.deepcopy(s)
        result = predict_signal(s, bars, predictor, horizon=5, samples=23, batch_size=8)
        self.assertEqual([c['sample_count'] for c in predictor.calls], [8, 8, 7])
        self.assertEqual(result['sample_count'], 23)
        self.assertEqual(s.chart, original.chart)
        self.assertEqual(s.score, original.score)

    def test_fallback_requests_individual_paths(self):
        class Upstream:
            def __init__(self): self.counts = []
            def predict(self, **kwargs):
                self.counts.append(kwargs['sample_count'])
                df = candles([106, 107, 115])
                df.index = pd.DatetimeIndex(kwargs['y_timestamp'])
                return df
        predictor = Upstream()
        result = predict_signal(signal(), history(), predictor, horizon=3, samples=4)
        self.assertEqual(predictor.counts, [1, 1, 1, 1])
        self.assertEqual(result['sample_count'], 4)

    def test_daily_leakage_is_prevented(self):
        bars = pd.concat([history(), candles([999, 999])])
        predictor = CapturingPredictor()
        predict_signals([signal()], {'TEST': bars}, predictor=predictor, samples=2, verbose=False)
        self.assertEqual(float(predictor.calls[0]['df'].close.iloc[-1]), 100)
        self.assertEqual(predictor.calls[0]['x_timestamp'].iloc[-1].date(), date(2026, 9, 28))

    def test_weekly_history_cut_before_resampling(self):
        bars = pd.concat([history(), candles([999, 999])])
        out = signal_history(signal(tf='W'), bars)
        self.assertEqual(out.close.iloc[-1], 100)
        self.assertEqual(out.index[-1].date(), date(2026, 9, 28))

    def test_future_weekly_dates_skip_partial_week(self):
        dates = generate_future_timestamps('2026-09-28', 2, 'W')
        self.assertEqual(str(dates[0].date()), '2026-10-09')

    def test_same_series_scenarios_reused_for_different_patterns(self):
        first, second = signal(), signal(scanner='stage', pivot=110)
        predictor = CapturingPredictor()
        predict_signals([first, second], {'TEST': history()}, predictor=predictor,
                        samples=4, horizon_d=5, verbose=False)
        self.assertEqual(len(predictor.calls), 1)
        self.assertNotEqual(first.kronos['target'], second.kronos['target'])

    def test_missing_history_is_visible_and_stale_result_cleared(self):
        s = signal()
        s.kronos = {'success_rate': 100}
        predict_signals([s], {}, predictor=CapturingPredictor(), verbose=False)
        self.assertEqual(s.kronos['status'], 'error')
        self.assertNotIn('success_rate', s.kronos)

    def test_history_close_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            signal_history(signal(close=200), history())

    def test_mini_checkpoint_pair(self):
        self.assertEqual(resolve_model_names('mini')[1], 'NeoQuasar/Kronos-Tokenizer-2k')

    def test_horizon_and_samples_validation(self):
        for kw in ({'samples': 0}, {'horizon': 0}, {'horizon': 513}):
            with self.assertRaises(ValueError):
                predict_signal(signal(), history(), CapturingPredictor(), **kw)

    def test_reports_include_errors_and_escape_html(self):
        s = signal(symbol='<script>')
        s.kronos = {'status': 'error', 'error': '<boom>', 'headline': '<boom>', 'verdict': 'Unavailable'}
        with tempfile.TemporaryDirectory() as folder:
            files = [str(Path(folder)/('result.'+ext)) for ext in ('csv','json','html')]
            write_reports([s], {'model': 'test'}, *files)
            self.assertIn('&lt;script&gt;', Path(files[2]).read_text(encoding='utf-8'))
            self.assertEqual(json.loads(Path(files[1]).read_text())['signals'][0]['kronos']['status'], 'error')

    def test_protect_original_outputs_before_loading_model(self):
        with patch('kronos_scan.load_bars') as loader:
            self.assertEqual(main(['--csv', 'results.csv']), 1)
            loader.assert_not_called()

    def test_malformed_csv_is_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)/'bad.csv'
            p.write_text('symbol,score\nTEST,100\n')
            with self.assertRaises(ValueError): load_signals_from_csv(p)

    def test_historical_label_and_pending(self):
        s = signal()
        path = candles([106, 107, 115])
        s.kronos = evaluate_breakout(s, path, [path]*32)
        full = pd.concat([history(), path])
        metrics = validate_forecasts([s], {'TEST': full}, BreakoutRules())
        self.assertEqual(metrics['observed_success_rate'], 100)
        self.assertEqual(metrics['brier_score'], 0)
        metrics = validate_forecasts([s], {'TEST': history()}, BreakoutRules())
        self.assertEqual(metrics['pending'], 1)


if __name__ == '__main__':
    unittest.main()
