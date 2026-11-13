# Technical reference

## Files

| File | Role |
|---|---|
| `kronos/adx_core.py` | Pure functions: Wilder smoothing, DMI/ADX, dominance, weekly context, setup detection. No network, no clock |
| `kronos/adx_options.py` | Pure functions: realised volatility, expected move, chain reading, structure pricing, scoring, expiry-days estimate |
| `kronos/adx_brain.py` | Pure functions: market read, score, call, trailing stop and adjustments, trade review, lessons |
| `kronos/adx_desk.py` | The service: background scan, board and verdicts, detail, expiry choice, paper trade, trade tracking, learning |
| `kronos/static/desk-trend.js` | The tab: board, detail panel, chart, comparison table, My ADX trades, alerts |
| `kronos/static/desk-trend.css` | Styling for the tab |
| `tests/test_adx.py` | 30 tests (synthetic data only) |

Small edits in existing files:

| File | Change |
|---|---|
| `kronos/service.py` | Creates `service.adx` |
| `kronos/server.py` | Four ADX routes; app version 2.8.0 |
| `kronos/position_watch.py` | Skips trades whose payload `source` is `adx` |
| `kronos/static/desk.html`, `desk.js` | The ADX tab button and page, version 2.8.0, tab switching |
| `.claude/launch.json` | A preview configuration for the desk (port 8771) |

## API (all local, loopback only)

| Route | Method | Purpose |
|---|---|---|
| `/api/trend/board` | GET | Verdicts for every setup, counts, data date, whether bars are behind, scan status, **alerts for open trades**. Starts a rescan when saved bars changed |
| `/api/trend/detail?symbol=&signal=` | GET | Chart bars, indicator state, the option plan (expiry choice, nine structures, chain reading), event check |
| `/api/trend/trades` | GET | Open trades with Hold/Book/Exit advice, closed trades, grouped record |
| `/api/trend/alerts?after=` | GET | Alerts logged by the watcher after number `after`. Fetches no prices |
| `/api/trend/scan` | POST | Start a rescan |
| `/api/trend/paper` | POST | `{symbol, signal, idea}`: open a one-lot paper trade |

Writes need the session token like every other desk write.

## Data and storage

- **Prices**: daily bars from the scanner cache (`.cache/bars.pkl` plus live bars). The tab never invents a bar, and a stock whose bars end earlier than the market's latest date is excluded from today's signals.
- **Board cache**: table `adx_cache` in `.cache/kronos_research/research.sqlite3`. A new scan runs when the saved bars or `RULES_VERSION` change, at most once every 10 minutes automatically; the Rescan button forces it.
- **Trades**: the existing paper journal table, tagged `source: adx` with an `adx` block in the payload. Paper trades are permanent and backed up like every other desk trade.
- **Nothing else** is stored. There is no separate learning database: learning is recomputed from the closed trades each time.

## Constants (change in one place)

| Constant | Value | Where | Meaning |
|---|---|---|---|
| `DMI_N`, `ADX_N` | 13, 8 | core | Indicator periods |
| `STRONG` | 25 | core | Trend level |
| `BASE_BARS`, `BASE_BARS_DUAL` | 30, 20 | core | Base length (20 when the weekly ADX is also low) |
| `STOP_BUFFER` | 0.5 ATR | core | Stop beyond the pivot |
| `TARGET_R` | 2 | core | Target multiple of the risk |
| `MIN_RISK`, `MAX_RISK` | 0.3, 3.5 ATR | core | Allowed entry-to-stop distance |
| `RULES_VERSION` | 4 | core | Bump to force a fresh scan after a rule change |
| `MIN_DTE` | 10 days | options | Minimum days an expiry should offer |
| `HOLD_DAYS`, `RANGE_DAYS` | 7, 21 | options | How far ahead outcomes are estimated |
| `MIN_OI`, `MAX_SPREAD`, `MIN_STRIKES` | 100, 12%, 6 | options | Liquidity |
| `RICH_IV`, `CHEAP_IV` | 1.3, 1.0 | options | Implied ÷ realised thresholds |
| `OUTCOMES` | 5 weighted points | options | Range scoring |
| `LEARN_MIN` | 5 | desk | Closed trades before learning acts |
| `MAX_DAYS` | 21 | desk | Days before a trade is called stale |
| `WATCH_SECONDS` | 20 | desk | How often the watcher re-checks open trades and triggers |
| `PRIOR`, `MIN_SEEN` | 6, 3 | brain | Break-even trades mixed into a learned average; closed trades before it counts |
| `LEAN` | 0.35 | brain | Market tilt that counts as with or against a setup |
| `POLICY` | book 80%, trail 1R, stall 7 days | brain | Exit rules before any lesson changes them |

## Running and testing

```bash
python kronos_dashboard.py            # then open the Options desk, ADX trend tab
```

```bash
python -m unittest discover -s tests -p "test_adx.py"
python -m unittest discover -s tests -p "test_*.py"
```

`test_adx.py` covers: DMI maths and dominance rules; weekly context cannot see the current week; setup detection and no look-ahead; the nine structures' payoff bounds, breakevens and legs; cheap/expensive volatility and event tilts; liquidity exclusion; the expiry-days estimate; scan, verdict, learning and exit-advice behaviour on a fake market.

The full suite currently has one **pre-existing failure** unrelated to the ADX tab: `tests/test_entry_board.py::TrackingTests::test_first_pass_is_a_baseline_then_changes_are_logged`, whose fixture forecast is dated 5 November 2026 and is now older than the entry board's 4-day limit. `test_market_freshness` has a documented timing-sensitive test that can fail intermittently.

## Known limits

- Rules are a published method turned into measurable rules; they are **not a proven edge**. A one-off stock-price replay found none, and no option backtest is possible.
- Outcome figures are Black-Scholes estimates at today's implied volatility, before brokerage and charges; real fills will differ.
- Realised volatility is a 20-day close-to-close figure. There is no IV percentile or term-structure input in the ADX tab.
- Daily bars are only as current as the scanner cache; the tab warns when they are a session behind.
- The watcher logs alerts whenever the app is running in market hours. A desktop notification needs the page open and permission granted; there is no phone notification.
- One lot, one position per stock; no portfolio sizing or margin.
- The score weights are judgement, not fitted values. See [6-decision-layer.md](6-decision-layer.md).
- Calendars and diagonals are not offered.
- Entry and the live tracking of real paper trades were covered by unit tests, but a real paper trade has not yet been placed through the tab.

## Ideas worth doing next

1. Place a few real paper trades and read the "what your trades have taught" panel.
2. Phone notifications for alerts.
3. Position sizing and a portfolio risk limit.
4. IV percentile and term structure for the cheap/expensive call.
5. Calendars and diagonals, when valuation across expiries is ready.
