"""
Pattern scanners -- Stage 1->2, Darvas Box, Cup & Handle -- on Daily and Weekly
timeframes, over any broker/data source in the common data layer.

Bars are downloaded once per symbol (daily), cached on disk, and reused for
both timeframes and all three scanners.

Examples:
    python scan.py                                   # all scanners, D+W, Definedge, top 1200 by market cap
    python scan.py --source kotak --open
    python scan.py --scanners cup --tf weekly
    python scan.py --symbols DIVISLAB,RELIANCE --asof 2026-07-08
    python scan.py --offline                         # re-scan cached bars, no API calls
    python scan.py --source local --data-dir ../rs-strategy-backtest/.cache/definedge_ohlcv
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
import webbrowser
from datetime import datetime

import pandas as pd

from analysis import analyse
from datalayer import BENCHMARK, SOURCES, AuthError, BarStore, get_source, resample
from datalayer.delivery import DeliveryStore
from datalayer.timeframe import TIMEFRAMES
from datalayer.universe import resolve
from scanners import REGISTRY
from scanners.common import liquid_enough

BENCH_KEY = f"__{BENCHMARK}__"


def log(msg=""):
    print(msg, file=sys.stderr, flush=True)


def parse_list(value: str, allowed: dict, what: str) -> list[str]:
    items = [x.strip().lower() for x in value.split(",") if x.strip()]
    if items == ["all"]:
        return list(dict.fromkeys(allowed.values())) if what == "timeframe" else list(allowed)
    bad = [x for x in items if x not in allowed]
    if bad:
        sys.exit(f"unknown {what}: {', '.join(bad)} (choose from {', '.join(allowed)})")
    return list(dict.fromkeys(allowed[x] if what == "timeframe" else x for x in items))


def load_bars(args):
    kwargs = {"data_dir": args.data_dir} if args.source == "local" else {}
    try:
        src = get_source(args.source, **kwargs)
    except (FileNotFoundError, ValueError) as e:
        sys.exit(str(e))
    store = BarStore(src, history_days=args.history_days, refresh=args.refresh, offline=args.offline)

    if args.offline:
        instruments = {sym: sym for sym in store.symbols() if sym != BENCH_KEY}
        if not instruments:
            sys.exit("--offline: no cached bars yet -- run once online first.")
    else:
        if src.needs_auth:
            log(f"Connecting to {src.name}...")
            try:
                src.authenticate()
            except AuthError as e:
                sys.exit(f"ERROR: {e}")
        instruments = src.instruments()

    universe, label = resolve(instruments, args.symbols.split(",") if args.symbols else None, args.universe, args.top)
    log(f"Universe: {len(universe)} symbols -- {label}")

    bench = None
    bench_id = src.benchmark_id()
    if bench_id:
        try:
            bench = store.get(BENCH_KEY, bench_id)
        except Exception as e:
            log(f"  Nifty 50 benchmark unavailable ({e}); RS scoring disabled")

    t0 = time.time()
    bars = store.load_many(universe, workers=args.workers)
    s = store.stats
    log(f"Bars ready for {len(bars)} symbols in {time.time() - t0:.0f}s "
        f"(cache {s['cached']}, updated {s['updated']}, full fetch {s['fetched']}, failed {s['failed']})")
    return src, bars, bench, label


def run_scanners(bars, bench, scanner_names, tfs, args, delivery=None):
    asof = pd.Timestamp(args.asof) if args.asof else None
    if asof is not None and bench is not None:
        bench = bench.loc[:asof]
    bench_tf = {tf: (resample(bench, tf) if bench is not None and len(bench) else None) for tf in tfs}

    signals, skipped_illiquid, errors = [], 0, 0
    for symbol, daily in bars.items():
        if asof is not None:
            daily = daily.loc[:asof]
        if len(daily) < 60:
            continue
        if not liquid_enough(daily, args.min_turnover):
            skipped_illiquid += 1
            continue
        if delivery is not None:
            daily = delivery.attach(daily, symbol)
        for tf in tfs:
            tf_bars = resample(daily, tf)
            for name in scanner_names:
                try:
                    sig = REGISTRY[name].scan(symbol, tf_bars, tf, bench_tf[tf])
                except Exception as e:
                    errors += 1
                    if errors <= 5:
                        log(f"  {name}/{tf} error on {symbol}: {e}")
                    continue
                if sig and sig.score >= args.min_score:
                    try:
                        sig.analysis = analyse(sig, tf_bars)
                    except Exception as e:
                        errors += 1
                        if errors <= 5:
                            log(f"  analysis error on {symbol}/{name}/{tf}: {e}")
                    signals.append(sig)
    signals.sort(key=lambda s: (s.scanner, -s.score))
    return signals, skipped_illiquid


def print_summary(signals, scanner_names, tfs, limit=15):
    for name in scanner_names:
        for tf in tfs:
            rows = [s for s in signals if s.scanner == name and s.tf == tf]
            tf_label = "Daily" if tf == "D" else "Weekly"
            print(f"\n=== {REGISTRY[name].TITLE} -- {tf_label}: {len(rows)} setups ===")
            if not rows:
                continue
            print(f"{'Symbol':<14}{'Score':>6}{'Close':>11}{'Pivot':>11}{'Dist%':>7}{'Risk%':>7}  {'Volume+Delivery':<20}Label")
            for s in rows[:limit]:
                verdict = (s.analysis or {}).get("verdict", "")
                print(f"{s.symbol:<14}{s.score:>6.0f}{s.close:>11.2f}{s.pivot:>11.2f}{s.distance_pct:>7.2f}{s.risk_pct:>7.1f}  {verdict:<20}{s.label}")
            if len(rows) > limit:
                print(f"  ... {len(rows) - limit} more in the report")


def write_csv(path, signals):
    metric_keys = list(dict.fromkeys(k for s in signals for k in s.metrics))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["scanner", "timeframe", "symbol", "date", "score", "close", "pivot", "distance_pct", "support", "risk_pct",
                    "label", "verdict", "pressure", *metric_keys, "notes", "analysis"])
        for s in signals:
            a = s.analysis or {}
            w.writerow([s.scanner, s.tf, s.symbol, s.date, s.score, round(s.close, 2), round(s.pivot, 2), round(s.distance_pct, 2),
                        round(s.support, 2), round(s.risk_pct, 2), s.label, a.get("verdict", ""), a.get("pressure", ""),
                        *[s.metrics.get(k, "") for k in metric_keys], "; ".join(s.notes), a.get("headline", "")])


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="Stage 1->2, Darvas Box and Cup & Handle pre-breakout scanners")
    ap.add_argument("--source", "--broker", dest="source", choices=list(SOURCES), default="definedge")
    ap.add_argument("--scanners", default="all", help=f"comma list of {', '.join(REGISTRY)} (default all)")
    ap.add_argument("--tf", default="daily,weekly", help="daily, weekly or daily,weekly (default both)")
    ap.add_argument("--top", type=int, default=1200, help="top-N by market cap (default 1200)")
    ap.add_argument("--universe", choices=["marketcap", "all"], default="marketcap")
    ap.add_argument("--symbols", help="scan only these, e.g. DIVISLAB,RELIANCE")
    ap.add_argument("--data-dir", help="folder of per-symbol CSV/JSON files for --source local")
    ap.add_argument("--min-turnover", type=float, default=2.0, help="min 20-day avg traded value in Rs crore (default 2)")
    ap.add_argument("--min-score", type=float, default=0, help="hide setups scoring below this")
    ap.add_argument("--history-days", type=int, default=1000, help="daily history kept in cache (default 1000)")
    ap.add_argument("--refresh", action="store_true", help="ignore cache freshness and re-download (e.g. intraday)")
    ap.add_argument("--no-delivery", action="store_true", help="skip NSE delivery data (volume-only analysis)")
    ap.add_argument("--delivery-days", type=int, default=730, help="days of NSE delivery history to keep (default 730)")
    ap.add_argument("--offline", action="store_true", help="scan cached bars only; no login, no API calls")
    ap.add_argument("--asof", help="evaluate as of a past date YYYY-MM-DD (uses bars up to that day)")
    ap.add_argument("--workers", type=int, help="parallel download threads (default: per source)")
    ap.add_argument("--html", default="results.html")
    ap.add_argument("--csv", default="results.csv")
    ap.add_argument("--no-html", action="store_true")
    ap.add_argument("--open", action="store_true", help="open the HTML report when done")
    args = ap.parse_args()

    if args.source == "local" and not args.data_dir:
        sys.exit("--source local needs --data-dir")
    scanner_names = parse_list(args.scanners, {k: k for k in REGISTRY}, "scanner")
    tfs = parse_list(args.tf, TIMEFRAMES, "timeframe")

    t0 = time.time()
    src, bars, bench, universe_label = load_bars(args)

    delivery = None
    if not args.no_delivery:
        delivery = DeliveryStore(history_days=args.delivery_days)
        if not args.offline:
            try:
                delivery.ensure()
            except Exception as e:
                log(f"  delivery update failed ({e}); using what is cached")
        log(f"Delivery coverage: {delivery.coverage()}")

    t1 = time.time()
    signals, illiquid = run_scanners(bars, bench, scanner_names, tfs, args, delivery)
    log(f"Scanned {len(bars)} symbols x {len(tfs)} timeframes x {len(scanner_names)} scanners in {time.time() - t1:.1f}s "
        f"({illiquid} skipped below Rs {args.min_turnover:g} Cr turnover)")

    print_summary(signals, scanner_names, tfs)

    if args.csv:
        write_csv(args.csv, signals)
        log(f"\nCSV  -> {os.path.abspath(args.csv)}")
    if not args.no_html:
        from report import write_html

        write_html(args.html, signals, scanner_names, tfs, meta={
            "source": src.name,
            "universe": universe_label,
            "scanned": len(bars),
            "generated": datetime.now().strftime("%a %d %b %Y, %H:%M"),
            "asof": args.asof,
            "min_turnover": args.min_turnover,
            "delivery": delivery.coverage() if delivery else "off",
        })
        log(f"HTML -> {os.path.abspath(args.html)}")
        if args.open:
            webbrowser.open(f"file://{os.path.abspath(args.html)}")
    log(f"Done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
