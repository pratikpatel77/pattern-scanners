"""Read scanner data without changing its detection rules or outputs."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pickle
import threading
import numpy as np
import pandas as pd
from datalayer import resample
from scanners import REGISTRY
from .core import _candles

ROOT = Path(__file__).resolve().parent.parent
IST = timezone(timedelta(hours=5, minutes=30))


def signal_dict(signal):
    if signal is None:
        return None
    return {"scanner": signal.scanner, "symbol": signal.symbol, "timeframe": signal.tf,
            "date": str(signal.date), "close": signal.close, "pivot": signal.pivot,
            "support": signal.support, "score": signal.score, "label": signal.label}


def candles_json(frame):
    return [{"date": t.strftime('%Y-%m-%d'), **{c: float(row[c]) for c in ('open','high','low','close','volume')}}
            for t, row in frame.iterrows()]


class MarketData:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.path = self.root / '.cache/bars.pkl'
        self._stamp = None
        self._entries = {}
        self._lock = threading.RLock()

    def reload(self):
        with self._lock:
            stamp = self.path.stat().st_mtime_ns if self.path.exists() else None
            if stamp != self._stamp:
                # Same trusted local cache the original scanner reads. No uploaded pickles.
                with self.path.open('rb') as f:
                    entries = pickle.load(f)
                self._entries, self._stamp = entries, stamp
            return self._entries

    def symbols(self):
        entries = self.reload()
        return sorted(k for k, v in entries.items() if not k.startswith('__') and len(v[1]))

    def daily(self, symbol, asof=None):
        entries = self.reload()
        if symbol not in entries:
            raise ValueError('No cached bars for this symbol. Run the scanner to load market data.')
        frame = entries[symbol][1].copy()
        if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
            raise ValueError('Market bars must have unique, increasing dates.')
        if frame.index.tz is not None:
            frame.index = frame.index.tz_convert(IST).tz_localize(None)
        # Do not treat a still-forming daily candle as a completed observation.
        current = datetime.now(IST)
        cutoff = pd.Timestamp(asof).normalize() if asof else pd.Timestamp(current.date())
        if cutoff.date() >= current.date() and current.hour*60 + current.minute < 15*60+30:
            cutoff = pd.Timestamp(current.date()) - pd.Timedelta(days=1)
        frame = frame.loc[frame.index.normalize() <= cutoff]
        if frame.empty:
            raise ValueError('No bars at or before the selected date.')
        frame, _ = _candles(frame)
        if 'volume' not in frame:
            frame['volume'] = 0.
        if not np.isfinite(frame.volume).all() or (frame.volume < 0).any():
            raise ValueError('Invalid volume in cached data.')
        return frame

    def bars(self, symbol, timeframe='D', asof=None):
        if timeframe not in ('D','W'):
            raise ValueError('Unsupported timeframe')
        return resample(self.daily(symbol, asof), timeframe)

    def detect(self, symbol, timeframe='D', asof=None, scanner=None):
        bars = self.bars(symbol, timeframe, asof)
        bench = None
        if '__NIFTY50__' in self.reload():
            bench = self.bars('__NIFTY50__', timeframe, bars.index[-1])
        found = []
        for name, module in REGISTRY.items():
            if scanner and scanner != 'all' and name != scanner:
                continue
            signal = module.scan(symbol, bars, timeframe, bench)
            if signal is not None:
                found.append(signal)
        return sorted(found, key=lambda s: (-s.score, s.scanner))

    def context(self, symbol, timeframe='D', asof=None):
        bars = self.bars(symbol, timeframe, asof)
        signals = self.detect(symbol, timeframe, asof)
        last = bars.index[-1].date()
        age = (datetime.now(IST).date()-last).days
        partial = timeframe == 'W' and last.weekday() != 4
        return {"symbol": symbol, "timeframe": timeframe, "asof": str(last),
                "history": candles_json(bars.tail(300)), "available_bars": len(bars),
                "signals": [signal_dict(s) for s in signals], "stale_days": age,
                "partial_week": partial, "source": "Scanner cache",
                "change_pct": float((bars.close.iloc[-1]/bars.close.iloc[-2]-1)*100) if len(bars)>1 else 0}

    def saved_setups(self):
        path = self.root/'results.csv'
        if not path.exists():
            return []
        frame = pd.read_csv(path)
        rows = []
        for _, r in frame.iterrows():
            try:
                rows.append({"symbol": str(r.symbol), "scanner": str(r.scanner),
                    "timeframe": str(r.timeframe), "date": str(pd.Timestamp(r.date).date()),
                    "close": float(r.close), "pivot": float(r.pivot), "support": float(r.support),
                    "score": float(r.score), "label": str(r.get('label',''))})
            except (ValueError, KeyError, AttributeError):
                continue
        return rows
