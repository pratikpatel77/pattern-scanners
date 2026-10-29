"""Standalone Kronos evaluation; never overwrites original scanner results."""
from __future__ import annotations
import argparse
import math
from pathlib import Path
import sys
import webbrowser
import pandas as pd
from datalayer import SOURCES
from kronos_breakout import BreakoutRules, get_kronos_predictor, predict_signals
from kronos_report import write_reports, validate_forecasts
from scan import load_bars, log, run_scanners
from scanners import REGISTRY
from scanners.common import Signal


def load_signals_from_csv(csv_path="results.csv"):
    frame = pd.read_csv(csv_path)
    required = {"scanner", "symbol", "timeframe", "date", "close", "pivot", "support", "score"}
    if not required.issubset(frame.columns):
        raise ValueError(f"Setup CSV missing columns: {sorted(required - set(frame.columns))}")
    signals = []
    for number, row in frame.iterrows():
        try:
            values = [float(row[key]) for key in ("close", "pivot", "support", "score")]
            if not all(math.isfinite(v) for v in values):
                raise ValueError("Nonfinite setup values")
            if str(row.timeframe) not in ("D", "W") or pd.isna(row.date) or pd.isna(row.symbol):
                raise ValueError("Invalid symbol, timeframe or date")
            signals.append(Signal(scanner=str(row.scanner), symbol=str(row.symbol), tf=str(row.timeframe),
                date=pd.Timestamp(row.date).date(), close=values[0], pivot=values[1],
                support=values[2], score=values[3],
                label=str(row.get("label", "")) if pd.notna(row.get("label", "")) else ""))
        except Exception as exc:
            raise ValueError(f"Invalid setup CSV row {number + 2}: {exc}") from exc
    return signals


def build_parser():
    ap = argparse.ArgumentParser(description="Evaluate pattern breakout success using Kronos scenarios")
    ap.add_argument("--input", default="results.csv")
    ap.add_argument("--rescan", action="store_true", help="run existing scanners in memory")
    ap.add_argument("--source", "--broker", dest="source", choices=list(SOURCES), default="definedge")
    ap.add_argument("--symbols", help="scan these symbols instead of loading the setup CSV")
    ap.add_argument("--model", default="small")
    ap.add_argument("--tokenizer", help="tokenizer checkpoint for a custom model")
    ap.add_argument("--device", default=None)
    ap.add_argument("--samples", type=int, default=32, help="independent scenarios (CPU can be slow)")
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--top-p", type=float, default=.9)
    ap.add_argument("--horizon-d", type=int, default=15)
    ap.add_argument("--horizon-w", type=int, default=6)
    ap.add_argument("--buffer-pct", type=float, default=.1)
    ap.add_argument("--confirmation-bars", type=int, default=2)
    ap.add_argument("--target-r", type=float, default=1.0)
    ap.add_argument("--min-target-pct", type=float, default=5.0)
    ap.add_argument("--offline", action="store_true", default=True)
    ap.add_argument("--online", dest="offline", action="store_false")
    ap.add_argument("--html", default="kronos_results.html")
    ap.add_argument("--csv", default="kronos_results.csv")
    ap.add_argument("--json", default="kronos_results.json")
    ap.add_argument("--no-html", action="store_true")
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--validate", action="store_true", help="compare forecasts with later cached bars")
    ap.add_argument("--history-days", type=int, default=1000)
    ap.add_argument("--data-dir")
    ap.add_argument("--min-turnover", type=float, default=2.0)
    ap.add_argument("--min-score", type=float, default=0)
    ap.add_argument("--asof", help="historical scan date (implies --rescan)")
    ap.add_argument("--workers", type=int)
    ap.add_argument("--top", type=int, default=1200)
    ap.add_argument("--universe", default="marketcap")
    ap.add_argument("--refresh", action="store_true")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    try:
        rules = BreakoutRules(args.buffer_pct, args.confirmation_bars, args.target_r, args.min_target_pct)
        if min(args.samples, args.batch_size, args.horizon_d, args.horizon_w) < 1:
            raise ValueError("Samples, batch size and horizons must be positive")
        if not math.isfinite(args.temperature) or args.temperature <= 0 or not 0 < args.top_p <= 1:
            raise ValueError("Invalid sampling temperature or top-p")
        if args.asof:
            pd.Timestamp(args.asof)
        outputs = [p for p in (args.csv, args.json, None if args.no_html else args.html) if p]
        reserved = {Path(args.input).resolve(), Path("results.csv").resolve(), Path("results.html").resolve()}
        if any(Path(p).resolve() in reserved for p in outputs):
            raise ValueError("Kronos outputs must not overwrite original scanner results or the input CSV")
        if len(set(Path(p).resolve() for p in outputs)) != len(outputs):
            raise ValueError("Output paths must be distinct")
        signals = None
        if not (args.rescan or args.symbols or args.asof):
            signals = load_signals_from_csv(args.input)
            if not signals:
                log("No setups in the input CSV.")
                return 0
            args.symbols = ",".join(dict.fromkeys(s.symbol for s in signals))
        args.scanners, args.tf = "all", "daily,weekly"
        args.no_delivery, args.delivery_days = True, 730
        src, bars, bench, universe_label = load_bars(args)
        if signals is None:
            signals, _ = run_scanners(bars, bench, list(REGISTRY), ["D", "W"], args, delivery=None)
        if not signals:
            log("No pattern setups to forecast.")
            return 0
        log(f"Evaluating {len(signals)} setups with {args.samples} scenarios each; loading {args.model}...")
        predictor = get_kronos_predictor(args.model, args.device, args.tokenizer)
        predict_signals(signals, bars, predictor=predictor, model=args.model,
                        horizon_d=args.horizon_d, horizon_w=args.horizon_w,
                        samples=args.samples, rules=rules, batch_size=args.batch_size,
                        seed=args.seed, temperature=args.temperature, top_p=args.top_p)
        validation = validate_forecasts(signals, bars, rules) if args.validate else None
        metadata = {"source": src.name, "universe": universe_label, "model": args.model,
                    "model_checkpoint": predictor.model_id, "tokenizer": predictor.tokenizer_id,
                    "device": str(predictor.device), "batch_size": args.batch_size,
                    "seed": args.seed, "validation": validation}
        write_reports(signals, metadata, csv_path=args.csv, json_path=args.json,
                      html_path=None if args.no_html else args.html)
        ok = sum(s.kronos.get("status") == "ok" for s in signals)
        log(f"Kronos: {ok}/{len(signals)} setups evaluated. Outputs saved separately from scanner results.")
        if args.open and not args.no_html:
            webbrowser.open(Path(args.html).resolve().as_uri())
        return 0 if ok else 1
    except (ValueError, FileNotFoundError, ImportError, RuntimeError, OSError) as exc:
        log(f"Kronos error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
