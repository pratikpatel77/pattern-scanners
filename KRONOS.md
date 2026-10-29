# Kronos prediction workspace

## Start

Double-click **Run Pattern Scanners.bat**, keep its window open, then open
http://127.0.0.1:8765. Run the original pattern scanner first for fresh prices.
Kronos reads that cache; **Refresh setups** only reruns existing pattern detectors.

## Use

1. Choose a scanner setup or load a stock symbol.
2. Choose Daily or Weekly and a **Forecast period**.
3. Click **Analyze prediction**.
4. Read the forecast end price, scenario range and sampled breakout frequency.
5. Read **Prediction quality** and inspect actual-price comparisons when available.
6. Open **Backtest**, set dates, and compare up to three separate forecast windows.
7. Reopen prior results or export CSV/JSON from **Run history**.

There is one shared forecast profile. Changing the period updates that profile;
run history stores immutable result snapshots, not extra strategies.
**Historical replay** allows a past origin date. **Latest prices** loads the latest
cached observations. The home page opens current cache data instead of silently
restoring an old forecast. Saved runs retain their original model settings.

## Focus

The interface focuses on predictions, sampled probabilities, and their quality.
Entry levels, stops, position sizing, paper capital, risk controls, paper P&L and
account charts are removed. No orders are sent. Model tuning is not exposed.

New dashboard analyses use Kronos-small, 128 history bars, 32 independent sampled
paths, batch size 4, seed 42, temperature 1 and top-p 0.9. These model defaults are
fixed. The legacy CLI/API remains compatible with its existing options, including
its paper simulation; those results are not shown in the prediction interface.

## Interpret the result

The price line is the median sampled close. The band is the 10th–90th percentile
range across scenarios, not a guaranteed range or calibrated confidence interval.
The directional frequency describes how many paths finish above the origin close.

Breakout frequency is the share of scenarios which confirm above the detected
pivot (two closes, 0.1% buffer), then reach the pattern reference target before
support breaks or the breakout fails. Target distance is the larger of the pattern
range extension and 5%. These levels define an outcome for measurement; the UI
does not present a personal trade plan. Without a detected pattern, no breakout
probability is invented. Sampling intervals measure finite-sample uncertainty,
not the reliability of the model itself.

Quality uses later actual prices: MAE/RMSE/sMAPE, direction accuracy, range coverage
and an unchanged-close baseline. Negative price skill means the model is worse
than that baseline. Breakout Brier error compares sampled frequency with observed
success (lower is better). Missing actuals are labeled unverified; partial actuals
are labeled incomplete. One successful forecast does not establish model quality.

Backtests replay detectors at each origin with no future prices in model input.
Windows do not overlap. Historical periods may overlap model pretraining data;
use fresh forward outcomes to assess live performance. Weekly actuals exclude
the origin week and a final non-Friday week conservatively. Holiday-shortened
weeks can be omitted. Future calendar labels are estimates without an exchange
holiday calendar. A partial weekly input is flagged.

## Install and troubleshoot

```powershell
python -m pip install -r requirements-kronos.txt
python kronos_dashboard.py
```

Missing official weights download on first use. CPU forecasts can take minutes.
The server binds to 127.0.0.1 only. Cached models work without a new download.
If WinError 10013 appears, Python's download connection was blocked; inspect
network/firewall access. No fake forecast replaces a failed job. The previous
result is explicitly labeled when a new analysis fails.

Storage is `.cache/kronos_research/research.sqlite3`; setup scans and downloaded
weights stay in the same Kronos cache directory. The original scanner's files,
reports and data-fetching logic are not edited by this feature.

```powershell
python -m unittest discover -s tests
node tests/test_kronos_workflow.cjs
node --check kronos/static/app.js
node --check kronos/static/charts.js
```

Architecture and license: [upstream notes](kronos/UPSTREAM.md).

## Adaptive daily watch

Choose a stock in Daily view and click **Watch selected stock**. The server checks
its scanner cache once a minute, even if the browser is closed. Keep the launcher
running. Watching resumes after an app restart; Pause stops new automatic jobs.
An already-running forecast finishes normally.

Run the original scanner to fetch prices. This watcher does not fetch a live feed.
A new completed candle, a correction to input bars, or a saved setting change
triggers one new forecast. Unchanged inputs are not rerun. Data older than four
calendar days is held back with a stale-data message (long exchange holidays may
also trigger this guard). Jobs run one at a time. Failed or cancelled inputs are
not retried every minute; use Analyze prediction for a manual retry.

Daily watch uses the one shared profile and forecast horizon, with daily bars.
It stores forecast snapshots, not new strategies. The outlook flags bias changes,
new/lost patterns and shifts of at least 15 percentage points in upside sample
share. These are model changes, not calibrated confidence or trade instructions.
Earlier watch forecasts continue to be scored against later actual prices (within
the newest 1,000 runs). Quality shows the most recent scored watch forecast, or
waits for actuals. Overlapping daily forecasts are not independent test samples.
Kronos weights stay fixed: adapting inputs is different from model retraining.


## One application

Pattern Scanner is the home screen at http://127.0.0.1:8765. The launcher can
refresh prices and scan, or open the saved report immediately. Click AI beside
a stock to carry its symbol and timeframe into the AI prediction tab. The
scanner and AI stay inside the same window and share one server and data cache.
The AI tab includes forecasts, daily watch, backtests and saved runs. Refresh
report reloads finished scanner output; it does not fetch prices. Use Run Pattern Scanners.bat as the single application launcher.
