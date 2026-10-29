# Pre-breakout Pattern Scanners

Three scanners that find NSE stocks **still inside a pattern, close to the
breakout point, but not broken out yet**, on **Daily and Weekly** bars, over
**any broker** through one shared data layer.

Every setup also gets an **Analysis** verdict -- is this pattern being quietly
bought (accumulation) or sold into (distribution)? -- worked out from volume,
NSE delivery quantity and price action across the whole pattern.

| Scanner | Finds | Pivot (trigger) | Stop |
|---|---|---|---|
| **Stage 1 -> Stage 2** | Weinstein Stage 1 bases: price chopping around a flattening 30-week MA (150-day on daily), close above the MA, near the base high | base high | recent swing low |
| **Darvas Box** | Confirmed Darvas boxes (top and bottom each held for 3 bars) whose top is at or near the 52-week high, with price coiling under the top | box top | box bottom |
| **Cup & Handle** | O'Neil cups (prior advance of 20% or more, 12-35% deep, U-shaped, rims at matching heights) with a shallow handle forming in the upper half | handle high / right rim | handle low |

## Setup (any computer, one time)

This folder is fully self-contained -- copy or clone it anywhere.

**Windows**
1. Install [Python 3.10+](https://www.python.org/downloads/) (tick *Add python.exe to PATH*).
2. Double-click **`setup.bat`** -- installs the packages and opens `.env` for your broker details.

**macOS / Linux**
```bash
./setup.sh          # installs packages, creates .env
```

Then edit **`.env`** and fill in **only the broker you use** (see `.env.example`;
`.env` is git-ignored and never leaves your machine). No broker at all? NSE delivery
data, `--offline` and `--source local` need no credentials.

## Run it

Double-click **`Run Pattern Scanners.bat`** (or `./run.sh`) and press Enter three
times: Definedge, all scanners, Daily + Weekly. The results open in your browser.

A first run downloads about 3 years of daily bars (~1.5 min on Definedge) plus 2 years
of NSE delivery data (~7 min, once). After that everything is cached, so re-running
takes **~10 seconds**.

## The report (`results.html`)

- One tab per scanner, with a **Both / Daily / Weekly** switch.
- Sorted by **Score** (0-100): how well-formed the pattern is and how close price is to the pivot.
- **Pattern** sparkline: the shaded part is the pattern, green dashed = pivot, red dashed = stop.
- **To pivot %**: how far price has to rise to break out. **Risk %**: close to stop.
- **D+W** badge: the same setup shows on both timeframes. **+N** badge: N other scanners also flag the stock.
- **Signals**: what supports the setup (MA turning up, higher lows, volume drying up, RS vs Nifty rising...).
- **Analysis** (last column): a verdict badge -- Strong accumulation / Accumulation / Mixed / Caution /
  Distribution -- plus the two numbers that matter most (delivery % vs its normal level, and up-day vs
  down-day volume). **Click the cell** for the full read (see below).
- Min-score and max-distance filters, symbol search, click a symbol to open TradingView, and a
  **TradingView list** box (`NSE:A,NSE:B,...`) with a Copy button that pastes straight into a watchlist.
- `results.csv` has the same rows for Excel, including the verdict, its score and the one-line summary.

## The Analysis popup

Clicking an Analysis cell opens a short, plain-language breakdown:

- **Headline** and a buying/selling pressure meter (-100 to +100).
- **Volume** -- how the pattern compares with the move before it, whether up
  days or down days carry the volume, whether volume is drying up near the
  pivot, and where the heavy days landed.
- **Delivery** -- the share of volume actually taken into demat, against the
  stock's own normal level; delivery on up days vs down days; whether it is
  rising as price nears the pivot; high-conviction days (heavy volume *and*
  high delivery) and churn days (heavy volume, almost no delivery).
- **Price action** -- position in the pattern range, higher lows, pivot tests,
  how tight the recent bars are, and how much stock was bought above today's price.
- **Latest day/week** -- what just happened, with delivery for that bar.
- **What to watch** -- the volume and delivery a breakout needs to be believable,
  the level that invalidates the pattern, and any specific weak point.

Every line is arithmetic over the pattern bars -- deterministic, instant, no AI
call and no extra cost. The rules live in `analysis.py`; the thresholds that
turn numbers into words are all in `score()` and `_bullets()`.

The verdict **describes** what volume and delivery did; it is **not a predictor**.
A replay of ~4,000 setups (Sep 2025 - Jul 2026, next 40 trading days) found no
meaningful difference in breakout rate or forward return between "Accumulation" and
"Caution" setups. Use it as context when reading a chart, not as a ranking or a buy/sell filter.

## Pattern Scanner with Kronos AI

Double-click **Run Pattern Scanners.bat**. Choose whether to refresh prices or
open the saved scanner report. Keep the launcher window open and use
http://127.0.0.1:8765.

Pattern Scanner is the home screen. Click **AI** beside a stock to open its
symbol and timeframe in the AI prediction tab. SAI Research includes sampled
price forecasts, interactive charts, daily monitoring, historical tests and
forecast-versus-actual quality checks. It uses one shared forecast profile.

Daily Watch checks the scanner cache for completed candles; run the scanner to
fetch new prices. Forecasts are sampled scenarios, and their frequencies are
not calibrated market probabilities. Model weights do not retrain automatically.

Install AI dependencies manually if needed:

```bash
pip install -r requirements-kronos.txt
python kronos_dashboard.py
```

See [KRONOS.md](KRONOS.md) for usage, limitations and legacy CLI options.

## Command line

```bash
python scan.py                                    # all scanners, D+W, Definedge, top 1200 by market cap
python scan.py --source kotak --open              # Kotak Neo, open report when done
python scan.py --scanners cup,darvas --tf weekly  # choose scanners / timeframe
python scan.py --symbols DIVISLAB,PVRINOX         # just these symbols
python scan.py --offline                          # re-scan cached bars, no login, no API calls
python scan.py --asof 2026-07-10                  # what the scanners would have shown on a past date
python scan.py --min-score 60 --min-turnover 5    # stricter
python scan.py --refresh                          # intraday: re-download to include today's live bar
```

| Option | Default | |
|---|---|---|
| `--source` | `definedge` | `definedge`, `kotak`, `fyers`, `local` |
| `--scanners` | `all` | `stage`, `darvas`, `cup` (comma list) |
| `--tf` | `daily,weekly` | `daily`, `weekly` |
| `--top` / `--universe all` | 1200 | top-N by market cap, or every NSE EQ symbol |
| `--min-turnover` | 2 | minimum 20-day average traded value, Rs crore |
| `--min-score` | 0 | hide weaker setups |
| `--history-days` | 1000 | daily history kept in cache |
| `--no-delivery` | off | skip NSE delivery data (volume-only analysis) |
| `--delivery-days` | 730 | days of delivery history to keep (2 years) |
| `--asof`, `--offline`, `--refresh`, `--open`, `--html`, `--csv`, `--no-html`, `--workers` | | |

Universe: drop a TradingView market-cap screener export (any `*.csv` with a `Symbol`
column, sorted by market cap) into the `universe/` folder and the newest one gives the
top-N ranking. Without one, NSE's NIFTY Total Market list (~750 names) is used.

## Data sources

| Source | Login | Speed (first fill / daily top-up, 1,150 symbols) | Notes |
|---|---|---|---|
| **Definedge** | automatic -- `DEFINEDGE_API_TOKEN`, `_API_SECRET`, `_TOTP_SECRET` in `.env` | ~1.5 min / ~1.5 min | fastest, long history per request |
| **Kotak Neo** | none -- only `KOTAK_NEO_CONSUMER_KEY` in `.env` | ~30 min / ~5 min | 180 days per request, limited to 4 req/s |
| **Fyers** | `FYERS_*` in `.env`; token expires daily -- refresh with `python tools/fyers_login.py` | limited to 8 req/s, 365 days per request | run `fyers_login.py` once each morning |
| **local** | none | instant | `--data-dir` folder of `SYMBOL.csv` (date,open,high,low,close,volume) or JSON rows. Use it for any other vendor |

### Delivery data (NSE)

Delivery quantity is not in any broker API. It comes from NSE's daily
`sec_bhavdata_full_DDMMYYYY.csv`, one file per trading day, no login needed.

- First run backfills **2 years** (~500 files, about 7 minutes), cached in `.cache/delivery.pkl`.
- After that it is one small file each evening; NSE publishes it after ~6 PM IST,
  so a scan run before then uses the previous session for delivery.
- Holidays are remembered, so a missing file is never re-requested.
- `--no-delivery` skips it entirely; the analysis then runs on volume and price only
  and says so.

**The cache is shared across brokers** (`.cache/bars.pkl`). Daily NSE bars are
the same whichever broker serves them, so once any broker has filled the cache,
the others only fetch the last few days. For example, filling with Definedge
and then running Kotak takes ~5 minutes, not 30. If a broker's recent prices
disagree with the cache (a split or bonus adjustment), that symbol's full
history is re-downloaded automatically.

Cached bars count as fresh once fetched after the last 15:30 close, so evening
re-runs make no API calls. Intraday, use `--refresh` to pull the live bar.

## Project layout

```
scan.py                 CLI: load bars once -> run every scanner on D and W -> analyse -> report
report.py               HTML report (table, sparklines, Analysis popup)
analysis.py             the volume + delivery + price-action rules
datalayer/              the common data layer
  base.py               DataSource contract: instruments(), benchmark_id(), _fetch_range()
                        + chunking to broker request limits, rate limiting, retries
  store.py              shared incremental cache, parallel loading
  timeframe.py          daily -> weekly (Sat-Fri weeks, so Muhurat/Budget sessions join the next week)
  delivery.py           NSE delivery-quantity download + cache
  universe.py           top-N market cap / symbol list
  config.py             reads .env (the only place credentials are read)
  sources/              definedge.py  kotak.py  fyers.py  local.py
scanners/
  common.py             Signal type, indicators, relative strength vs Nifty 50
  stage.py  darvas.py  cup_handle.py
tests/test_scanners.py  synthetic textbook patterns (python -m unittest discover tests)
tests/test_analysis.py  synthetic accumulation / distribution reads
tools/fyers_login.py    daily Fyers token refresh
setup.bat / setup.sh    one-time install     run.sh / Run Pattern Scanners.bat  launchers
.env.example            credentials template (copy to .env)
universe/               optional market-cap CSV for the top-N ranking
```

**Add a broker**: one file in `datalayer/sources/` implementing the three
methods above, plus one line in `datalayer/__init__.py`.
**Tune a scanner**: every threshold is in the `PARAMS = {"D": ..., "W": ...}`
block at the top of each scanner file, with the rules documented in the
module docstring.

## Notes

- Scanners look at price and volume; the Analysis column adds delivery. Confirm every setup on the chart.
  This is a screening tool, not investment advice.
- The weekly scan includes the current, still-forming week.
- Relative strength vs the Nifty 50 feeds into the scores whenever the source serves the index (all three brokers do).
