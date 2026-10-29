"""Pattern-conditioned evaluation of independent Kronos candle scenarios.

Rates describe model samples, not calibrated market probabilities. The original
scanner's patterns, scores and analysis are never used as synthetic probabilities.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import math
from typing import Any

import numpy as np
import pandas as pd

from scanners.common import Signal

DEFAULT_MODEL = "NeoQuasar/Kronos-small"
DEFAULT_TOKENIZER = "NeoQuasar/Kronos-Tokenizer-base"
MODEL_ALIASES = {
    "mini": ("NeoQuasar/Kronos-mini", "NeoQuasar/Kronos-Tokenizer-2k"),
    "small": (DEFAULT_MODEL, DEFAULT_TOKENIZER),
    "base": ("NeoQuasar/Kronos-base", DEFAULT_TOKENIZER),
}
_PREDICTOR_CACHE: dict = {}
PRICE_COLS = ["open", "high", "low", "close"]
OUTCOMES = ("success", "fakeout", "breakdown", "unresolved", "no_breakout", "extended")


@dataclass(frozen=True)
class BreakoutRules:
    buffer_pct: float = 0.1
    confirmation_bars: int = 2
    target_r: float = 1.0
    min_target_pct: float = 5.0

    def __post_init__(self):
        if not all(math.isfinite(v) and v >= 0 for v in
                   (self.buffer_pct, self.min_target_pct)):
            raise ValueError("Buffer and minimum target must be finite and nonnegative")
        if self.confirmation_bars < 1 or self.target_r <= 0 or not math.isfinite(self.target_r):
            raise ValueError("Confirmation bars and target multiple must be positive")


def is_kronos_available() -> bool:
    try:
        from kronos_model import Kronos, KronosTokenizer, KronosPredictor
        return True
    except ImportError:
        return False


def resolve_model_names(model_name: str | None) -> tuple[str, str]:
    name = (model_name or "small").strip()
    return MODEL_ALIASES.get(name.lower(), (name, DEFAULT_TOKENIZER))


def get_kronos_predictor(model_name="small", device=None, tokenizer_name=None):
    import torch
    from kronos_model import Kronos, KronosTokenizer, KronosPredictor
    model_id, tokenizer_id = resolve_model_names(model_name)
    tokenizer_id = tokenizer_name or tokenizer_id
    device = device or ("cuda:0" if torch.cuda.is_available() else "cpu")
    key = (model_id, tokenizer_id, device)
    if key not in _PREDICTOR_CACHE:
        tokenizer = KronosTokenizer.from_pretrained(tokenizer_id).eval()
        model = Kronos.from_pretrained(model_id).eval()
        context = 2048 if model_id == MODEL_ALIASES["mini"][0] else 512
        _PREDICTOR_CACHE[key] = KronosPredictor(model, tokenizer, device=device, max_context=context)
        _PREDICTOR_CACHE[key].model_id = model_id
        _PREDICTOR_CACHE[key].tokenizer_id = tokenizer_id
    return _PREDICTOR_CACHE[key]


def calculate_target_price(pivot, support, close=None, rules=None):
    rules = rules or BreakoutRules()
    return float(pivot + max((pivot - support) * rules.target_r,
                             pivot * rules.min_target_pct / 100))


def generate_future_timestamps(last_date, periods, tf="D"):
    if periods < 1 or tf not in ("D", "W"):
        raise ValueError("A positive horizon and D/W timeframe are required")
    dt = pd.Timestamp(last_date)
    if tf == "W":
        # Skip the rest of the current (possibly partial) week.
        friday = dt.normalize() + pd.Timedelta(days=(4 - dt.weekday()) % 7)
        return pd.date_range(friday + pd.Timedelta(days=7), periods=periods, freq="W-FRI")
    return pd.bdate_range(dt + pd.Timedelta(days=1), periods=periods)


def _candles(frame, *, repair=False):
    if frame.empty or not set(PRICE_COLS).issubset(frame.columns):
        raise ValueError("Nonempty OHLC candles required")
    out = frame.copy()
    prices = out[PRICE_COLS].to_numpy(dtype=float)
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("OHLC prices must be finite and positive")
    upper = out[PRICE_COLS].max(axis=1)
    lower = out[PRICE_COLS].min(axis=1)
    inconsistent = (out.high < upper) | (out.low > lower)
    if inconsistent.any() and not repair:
        raise ValueError("Historical OHLC candles have inconsistent high/low bounds")
    # Decoded OHLC features are unconstrained. Expose rather than hide repairs.
    out["high"], out["low"] = upper, lower
    return out, int(inconsistent.sum())


def signal_history(sig, bars):
    """Cut DAILY history before weekly aggregation to avoid future leakage."""
    from datalayer import resample
    if not isinstance(bars.index, pd.DatetimeIndex):
        raise ValueError("Market data needs a DatetimeIndex")
    if bars.index.has_duplicates or not bars.index.is_monotonic_increasing:
        raise ValueError("Market data timestamps must be unique and increasing")
    cutoff = pd.Timestamp(sig.date).date()
    history = bars.loc[bars.index.date <= cutoff].copy()
    history = resample(history, sig.tf)
    if history.empty or history.index[-1].date() != cutoff:
        raise ValueError("No market candle matching the setup date")
    if not np.isclose(float(history.close.iloc[-1]), sig.close, rtol=1e-4, atol=0.011):
        raise ValueError("Setup close does not match its historical candle; re-scan this setup")
    return history


def classify_path(sig, frame, target, rules):
    """Confirmation uses closes; entry is at that close, so no same-bar target.

    Once active, stop wins ties with target (unknown intrabar order). A close
    back below the pivot also fails the breakout. Terminal outcomes are absorbing.
    """
    trigger = sig.pivot * (1 + rules.buffer_pct / 100)
    active = sig.close >= trigger
    entry_bar = 0 if active else None
    streak = 0
    for i, row in enumerate(frame.itertuples(), 1):
        if active and row.open > sig.support and row.open >= target:
            return "success", entry_bar, i
        if row.low <= sig.support:
            return ("fakeout" if active else "breakdown"), entry_bar, None
        if active:
            if row.high >= target:
                return "success", entry_bar, i
            if row.close < sig.pivot:
                return "fakeout", entry_bar, None
        else:
            streak = streak + 1 if row.close >= trigger else 0
            if streak >= rules.confirmation_bars:
                active, entry_bar = True, i
                if row.close >= target:
                    return "extended", entry_bar, None
    return ("unresolved" if active else "no_breakout"), entry_bar, None


def _wilson(successes, count):
    p, z = successes / count, 1.96
    den = 1 + z * z / count
    mid = (p + z * z / (2 * count)) / den
    radius = z * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count)) / den
    return [round(100 * max(0, mid - radius), 1), round(100 * min(1, mid + radius), 1)]


def evaluate_breakout(sig, future_df, sample_dfs=None, target_price=None, rules=None):
    rules = rules or BreakoutRules()
    if sig.tf not in ("D", "W"):
        raise ValueError("Only daily and weekly setups are supported")
    if not all(math.isfinite(x) and x > 0 for x in (sig.pivot, sig.support, sig.close)):
        raise ValueError("Setup prices must be finite and positive")
    if sig.support >= min(sig.pivot, sig.close):
        raise ValueError("Setup is invalidated or support is above the pivot")
    target = target_price if target_price is not None else calculate_target_price(sig.pivot, sig.support, rules=rules)
    trigger = sig.pivot * (1 + rules.buffer_pct / 100)
    if not math.isfinite(target) or target <= max(trigger, sig.close):
        raise ValueError("Target must be above entry and current price; setup may already be extended")
    paths = sample_dfs if sample_dfs is not None else [future_df]
    if not paths or len(future_df) == 0:
        raise ValueError("At least one complete forecast path is required")
    clean, repaired = [], 0
    for path in paths:
        if len(path) != len(future_df) or not path.index.equals(future_df.index):
            raise ValueError("All sampled paths must share the forecast horizon and timestamps")
        normalized, changes = _candles(path, repair=True)
        clean.append(normalized)
        repaired += changes
    outcomes = [classify_path(sig, p, target, rules) for p in clean]
    counts = Counter(o[0] for o in outcomes)
    n = len(paths)
    rates = {name: 100 * counts[name] / n for name in OUTCOMES}
    success = rates["success"]
    interval = _wilson(counts["success"], n)
    if n < 20:
        verdict, tone = "Insufficient samples", "flat"
    elif interval[0] >= 50:
        verdict, tone = "Strong Breakout", "good"
    elif success >= 50:
        verdict, tone = "Likely Breakout", "good"
    elif rates["breakdown"] >= 40:
        verdict, tone = "Breakdown Risk", "bad"
    elif rates["fakeout"] >= 35:
        verdict, tone = "Fakeout Risk", "bad"
    else:
        verdict, tone = "Unconfirmed", "flat"
    closes = np.array([p.close.to_numpy(float) for p in clean])
    highs = np.array([p.high.to_numpy(float) for p in clean])
    lows = np.array([p.low.to_numpy(float) for p in clean])
    confirmed = [o[1] for o in outcomes if o[1] is not None]
    target_bars = [o[2] for o in outcomes if o[2] is not None]
    breakout_rate = 100 * len(confirmed) / n
    reference_entry = max(trigger, sig.close)
    rr = (target - reference_entry) / (reference_entry - sig.support)
    headline = (f"{counts['success']}/{n} sampled paths ({success:.1f}%) confirm a breakout and reach "
                f"{target:.2f} before invalidation within {len(future_df)} "
                f"{'weeks' if sig.tf == 'W' else 'sessions'}. "
                f"95% sampling interval {interval[0]:.1f}–{interval[1]:.1f}%; not a calibrated market probability.")
    sections = [
        {"title": "Success rule", "bullets": [
            {"text": f"{rules.confirmation_bars} consecutive closes at or above {trigger:.2f}; then target {target:.2f} before stop {sig.support:.2f} or a close below pivot {sig.pivot:.2f}.", "tone": "flat"},
            {"text": "Entry occurs at confirmation close. Targets on that candle are not counted; confirmation at/above target is extended. Known opening gaps are evaluated first; stop wins ambiguous intrabar target/stop ties. Setups already above the trigger are evaluated as continuation from the current close. R:R uses trigger/current close as a planning reference, not a simulated fill.", "tone": "flat"}]},
        {"title": "Sampled outcomes", "bullets": [
            {"text": f"{name.replace('_', ' ').title()}: {counts[name]}/{n} ({rates[name]:.1f}%)", "tone": "good" if name == "success" else "flat"} for name in OUTCOMES]},
        {"title": "Interpretation and data quality", "bullets": [
            {"text": "Sample frequencies measure this model's scenarios. Historical out-of-sample validation is required before interpreting them as trading odds. The interval measures sampling uncertainty only.", "tone": "flat"},
            {"text": f"Repaired {repaired} decoded candles to enclose their open/close within high/low. Forecast chart shows median and 10–90% close quantiles, not a tradable path.", "tone": "flat"},
            {"text": "Future dates approximate weekdays (daily) or next-week Fridays (weekly); exchange holidays and partial weeks are not modelled.", "tone": "flat"}]},
    ]
    return {
        "status": "ok", "verdict": verdict, "tone": tone, "score": round(success, 1),
        "probability": round(success, 1), "success_rate": round(success, 1),
        "breakout_prob": round(breakout_rate, 1), "clean_prob": round(success, 1),
        "fakeout_prob": round(rates['fakeout'], 1), "breakdown_prob": round(rates['breakdown'], 1),
        "outcome_counts": {name: counts[name] for name in OUTCOMES},
        "outcome_rates": {name: round(rates[name], 2) for name in OUTCOMES},
        "sample_count": n, "success_interval": interval, "calibrated": False,
        "bars_to_breakout": float(np.median(confirmed)) if confirmed else None,
        "bars_to_target": float(np.median(target_bars)) if target_bars else None,
        "target": round(target, 2), "evaluation_target": target,
        "trigger": round(trigger, 4), "stop": sig.support,
        "rr": round(rr, 2), "gain_pct": round((target / sig.close - 1) * 100, 2),
        "projected_max": round(float(np.median(highs.max(axis=1))), 2),
        "projected_min": round(float(np.median(lows.min(axis=1))), 2),
        "projected_close": round(float(np.median(closes[:, -1])), 2),
        "drawdown_pct": round(float(np.median(np.maximum(0, 1 - lows.min(axis=1) / sig.close))) * 100, 2),
        "headline": headline, "chips": [f"Success: {success:.1f}% ({counts['success']}/{n})", f"Target: {target:.2f}", f"Sampling interval: {interval[0]}–{interval[1]}%"],
        "sections": sections, "rules": asdict(rules), "repaired_candles": repaired,
        "horizon": len(future_df), "forecast_dates": [str(pd.Timestamp(t).date()) for t in future_df.index],
        "pred_closes": np.median(closes, axis=0).round(2).tolist(),
        "pred_highs": np.median(highs, axis=0).round(2).tolist(),
        "pred_lows": np.median(lows, axis=0).round(2).tolist(),
        "pred_q10": np.quantile(closes, .1, axis=0).round(2).tolist(),
        "pred_q90": np.quantile(closes, .9, axis=0).round(2).tolist(), "pred_target": round(target, 2),
    }


def predict_signal(sig, bars, predictor, horizon=None, samples=32,
                   temperature=1.0, top_p=0.9, rules=None, batch_size=8, seed=42,
                   _path_cache=None, on_progress=None):
    if samples < 1 or batch_size < 1 or not math.isfinite(temperature) or temperature <= 0 or not 0 < top_p <= 1:
        raise ValueError("Positive samples, batch size, temperature and valid top_p required")
    pred_len = horizon if horizon is not None else (15 if sig.tf == "D" else 6)
    if not isinstance(bars.index, pd.DatetimeIndex) or bars.index.has_duplicates or not bars.index.is_monotonic_increasing:
        raise ValueError("Market timestamps must be a unique, increasing DatetimeIndex")
    # bars here are already on the signal timeframe. Always enforce its date.
    context = bars.loc[bars.index.date <= pd.Timestamp(sig.date).date()].copy()
    if context.empty or context.index[-1].date() != pd.Timestamp(sig.date).date():
        raise ValueError("Missing setup-date context")
    if not np.isclose(context.close.iloc[-1], sig.close, rtol=1e-4, atol=.011):
        raise ValueError("Setup close does not match context")
    capacity = getattr(predictor, "max_context", 512)
    if not 1 <= pred_len <= capacity:
        raise ValueError(f"Horizon must be between 1 and {capacity}")
    context = context.tail(min(400 if sig.tf == "D" else 150, capacity))
    if len(context) < 30:
        raise ValueError("At least 30 historical candles are required")
    context, _ = _candles(context)
    for col in ("volume", "amount"):
        if col in context and (not np.isfinite(context[col]).all() or (context[col] < 0).any()):
            raise ValueError(f"Invalid historical {col}")
    dates = generate_future_timestamps(context.index[-1], pred_len, sig.tf)
    features = context[[c for c in PRICE_COLS + ['volume', 'amount'] if c in context]].copy()
    timestamps = pd.Series(context.index)
    # Stable per-series seed, unaffected by scanner order. Different patterns on
    # the same series see the same scenarios, evaluated at their own levels.
    identity = f"{sig.symbol}|{sig.tf}|{sig.date}|{seed}"
    actual_seed = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:4], "little")
    try:
        import torch
    except ImportError:
        torch = None
    fingerprint = hashlib.sha256(pd.util.hash_pandas_object(features, index=True).values.tobytes()).hexdigest()
    cache_key = (identity, fingerprint, pred_len, samples, batch_size, temperature, top_p)
    paths = list((_path_cache or {}).get(cache_key, []))
    # Preserve caller's RNG state. Only the optional forecasting layer needs torch.
    from contextlib import nullcontext
    rng = torch.random.fork_rng(devices=list(range(torch.cuda.device_count()))) if torch else nullcontext()
    with rng:
        if torch:
            torch.manual_seed(actual_seed)
        while len(paths) < samples:
            count = min(batch_size, samples - len(paths))
            kwargs = dict(df=features.copy(), x_timestamp=timestamps, y_timestamp=pd.Series(dates),
                          pred_len=pred_len, T=temperature, top_p=top_p, verbose=False)
            if hasattr(predictor, 'predict_samples'):
                _, chunk = predictor.predict_samples(**kwargs, sample_count=count)
            else:
                # Upstream predict() averages paths. Request one at a time.
                chunk = [predictor.predict(**kwargs, sample_count=1) for _ in range(count)]
            if len(chunk) != count or any(len(p) != pred_len or not p.index.equals(dates) for p in chunk):
                raise ValueError("Predictor returned incomplete or misaligned sample paths")
            paths.extend(chunk)
            if on_progress is not None:
                on_progress(len(paths), samples)
    if _path_cache is not None:
        _path_cache[cache_key] = paths
    result = evaluate_breakout(sig, paths[0], paths, rules=rules)
    result.update(asof=str(sig.date), seed=actual_seed, lookback=len(context),
                  temperature=temperature, top_p=top_p)
    sig.kronos = result
    # Original chart and scanner score remain untouched.
    return result


def predict_signals(signals, bars_dict, predictor=None, model="small", device=None,
                    horizon_d=15, horizon_w=6, samples=32, verbose=True, **kwargs):
    if not signals:
        return signals
    if predictor is None:
        predictor = get_kronos_predictor(model, device)
    path_cache = {}
    for i, sig in enumerate(signals, 1):
        sig.kronos = {}
        try:
            if sig.symbol not in bars_dict:
                raise ValueError("Market history unavailable")
            history = signal_history(sig, bars_dict[sig.symbol])
            if verbose:
                print(f"[{i}/{len(signals)}] Evaluating {sig.symbol}/{sig.scanner}/{sig.tf}...", flush=True)
            progress = (lambda done, total: print(f"  Scenarios: {done}/{total}", flush=True)) if verbose else None
            result = predict_signal(sig, history, predictor,
                                    horizon=horizon_w if sig.tf == "W" else horizon_d,
                                    samples=samples, _path_cache=path_cache,
                                    on_progress=progress, **kwargs)
            result['model'] = getattr(predictor, 'model_id', resolve_model_names(model)[0])
            result['tokenizer'] = getattr(predictor, 'tokenizer_id', resolve_model_names(model)[1])
            result['device'] = str(getattr(predictor, 'device', device or 'unknown'))
            if verbose:
                print(f"[{i}/{len(signals)}] {sig.symbol}/{sig.scanner}/{sig.tf}: "
                      f"{result['verdict']} — success {result['success_rate']}% ({samples} paths)", flush=True)
        except Exception as exc:
            sig.kronos = {"status": "error", "verdict": "Unavailable", "tone": "flat",
                          "score": -1, "error": str(exc), "headline": str(exc),
                          "chips": ["Forecast unavailable"], "sections": []}
            if verbose:
                print(f"[{i}/{len(signals)}] {sig.symbol}/{sig.tf}: unavailable — {exc}", flush=True)
    return signals
