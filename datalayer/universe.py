"""Which symbols to scan.

1. `--symbols A,B,C`               -> exactly those
2. TradingView market-cap export   -> top N rows. Drop the export CSV into this project's
                                      folder or its `universe/` subfolder (any `*.csv` with a
                                      `Symbol` column; `Untitled screen_*.csv` in the parent
                                      folder also still works). Newest wins.
3. NSE NIFTY Total Market list     -> ~750 names, cached 24h
4. `--universe all`                -> every NSE EQ symbol the broker serves
"""
from __future__ import annotations

import csv

from .util import API_ROOT, PROJECT_ROOT, cached_download

TOTAL_MARKET_URL = "https://archives.nseindia.com/content/indices/ind_niftytotalmarket_list.csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def _read_symbols(path, top: int) -> list[str]:
    out = []
    with path.open(newline="", encoding="utf-8-sig", errors="ignore") as f:
        for row in csv.DictReader(f):
            sym = (row.get("Symbol") or "").strip()
            if sym:
                out.append(sym)
            if len(out) >= top:
                break
    return out


def _find_exports() -> list:
    """TradingView screener exports, newest first: project folder, ./universe, legacy parent folder."""
    found = []
    for folder, pattern in ((PROJECT_ROOT / "universe", "*.csv"), (PROJECT_ROOT, "Untitled screen_*.csv"),
                            (PROJECT_ROOT, "*screen*.csv"), (API_ROOT, "Untitled screen_*.csv")):
        if folder.is_dir():
            found += [p for p in folder.glob(pattern) if p.name not in ("results.csv",) and _has_symbol_column(p)]
    unique = {p.resolve(): p for p in found}.values()
    return sorted(unique, key=lambda p: p.stat().st_mtime, reverse=True)


def _has_symbol_column(path) -> bool:
    try:
        with path.open(newline="", encoding="utf-8-sig", errors="ignore") as f:
            return "Symbol" in (next(csv.reader(f), []) or [])
    except OSError:
        return False


def market_cap_universe(top: int) -> tuple[list[str], str]:
    exports = _find_exports()
    if exports:
        return _read_symbols(exports[0], top), f"TradingView market-cap export ({exports[0].name}), top {top}"
    try:
        path = cached_download(TOTAL_MARKET_URL, "nifty_total_market.csv", headers=HEADERS)
        return _read_symbols(path, top), "NSE NIFTY Total Market list"
    except Exception:
        return [], "broker's full symbol list (no market-cap list reachable)"


def resolve(instruments: dict[str, str], symbols: list[str] | None, universe: str, top: int) -> tuple[dict[str, str], str]:
    """Returns ({symbol: broker_id}, human-readable universe label)."""
    if symbols:
        wanted = [s.strip().upper() for s in symbols if s.strip()]
        missing = [s for s in wanted if s not in instruments]
        if missing:
            print(f"  not served by this source: {', '.join(missing)}")
        return {s: instruments[s] for s in wanted if s in instruments}, f"{len(wanted)} requested symbols"

    if universe == "all":
        return dict(instruments), "all NSE EQ symbols from the source"

    names, label = market_cap_universe(top)
    if not names:
        return dict(list(instruments.items())[:top]), label
    picked = {}
    for s in names:
        for variant in (s, s.replace("_", "-")):  # TradingView writes BAJAJ_AUTO for BAJAJ-AUTO
            if variant in instruments:
                picked[variant] = instruments[variant]
                break
    return picked, label
