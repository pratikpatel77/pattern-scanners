# Live Options desk

Run `python kronos_dashboard.py`, then open **Options desk**, or visit `/options` on the same local server. The existing pattern scanner and research tools stay available.

## Daily flow

The desk covers **F&O stocks only**. The stock picker starts with the saved pattern scanner stocks that have F&O contracts, not only stocks with completed options analyses; non-F&O stocks are not listed, scanned or analysed. A stock click opens its saved analysis immediately; use Refresh forecast for new prices and predictions. A stock without a saved analysis starts one during market hours. Pattern, timeframe and text filters make the list searchable. The **All F&O stocks** list keeps the full-universe workflow available. On desktop the analysis is on the left and the stock picker is on the right.

Each analysis shows the **best two distinct structures** the desk can build from the liquid chain, ranked by modelled net reward after costs. Nothing is hidden because of a weak result, so a poor outlook is shown as such. Each idea is:

- **Ready**: passes every check.
- **Research only**: has advisory warnings (reward below your thresholds, negative IV-stressed average, too few profitable paths, missing IV percentile or event coverage). Paper entry needs an acknowledgement.
- **Review only**: has a hard block, such as a scheduled event in the trade period or a failed liquidity check. Entry is disabled.

Timing issues (EOD data, stale quotes, an old forecast, a stock move over 0.5%) never hide an idea but block entry until fresh live data.

The first idea opens automatically, including its payoff, strike editor and paper-entry controls. Click the other card to compare it. Each strike dropdown contains current chain contracts for the same stock, expiry and option type. Lot ratios stay fixed. A change refreshes LTP, bid, ask, costs, model outcomes and payoff from the server. LTP is informational; fills still use bid/ask plus slippage. Manual edits preserve the original analysis and forecast age, and cannot bypass entry checks.

1. Refresh your existing FYERS login. Credentials stay on the server.
2. Optionally adjust reward warning thresholds, slippage and liquidity limits in **Rules & data**. There is no paper capital or loss budget: each idea shows its one-lot maximum loss.
3. Load the current F&O list. Analyse one stock or scan all stocks, independently of pattern detections.
4. If IV percentile is missing, check Sensibull and save today's reading with its source link. Analyse again. Daily ATM IV is also collected going forward; at least 60 prior sessions are required for a local percentile. This is not a constant-maturity IV series.
5. Review the event flag. NSE scheduled board meetings/results and corporate actions are checked. If the service is unavailable, a manual dated calendar review must cover the planned holding date. Unknown event coverage is an explicit research warning. A known scheduled event still blocks entry when Avoid events is enabled. Unscheduled news cannot be ruled out.
6. Use the **Scanner** tab to rank every analysed stock, then open the best one. Compare strategies and the net expiry payoff. **Refresh LTP & payoff** fetches depth and recalculates both curves. **Paper trade this strategy** independently fetches fresh depth and reruns checks before recording one lot. Missing IVP, unknown event coverage or weak model results require explicit acknowledgement as a research paper trade. The warning list is stored with the trade. Stale prices, poor liquidity, an old forecast and avoided events cannot be bypassed.
7. In **Paper journal**, refresh marks or close trades with current opposite-side bid/ask fills and nonzero slippage. No broker order is sent. Marks are snapshots, not a streaming P&L feed.
8. Use **Score due forecasts** to compare saved forecasts with observed stock closes after their fixed due date. This scores forward records, not a historical options backtest. Trading-calendar dates are estimated weekdays; holiday due dates remain unscored rather than shifting the target.

## Ready to enter tab (fast desk, `/options`)

A ranked list of **F&O stocks only**, bullish and bearish, where a saved scanner pattern and the newest saved Kronos forecast agree. It never runs the model; it reads what is already saved, plus live prices (FYERS, Definedge fallback). The badge on the tab counts Ready stocks.

| Verdict | Needs all of |
|---|---|
| **Ready** | Price within 2% before the trigger, or up to 3% past it · Kronos view matches the pattern direction (upside for bullish, downside for bearish) · forecast no more than 4 days old and price within 3% of its last close · no result or corporate event inside the trade period |
| **Watch** | Price in the entry zone but one check above is missing or unconfirmed (no forecast, mixed outlook, old forecast, event ahead), or a Kronos-confirmed setup within 6% of its trigger |
| **Not yet** | Pattern still far from its trigger and nothing confirms it |
| **Skipped** | Kronos leans the other way, price is more than 3% past the trigger (chasing), or price is past the stop |

Each row shows the best pattern per direction (Daily and Weekly merged), trigger, stop and measured-move target, the Kronos view with its median move and age, the best saved options idea, and the reason for the verdict. **Open** loads the stock in the chain and builder. Rows are ordered by pattern score, Kronos conviction and closeness to the trigger; this is an ordering, not a probability or a measured win rate. Patterns confirm on a closing basis. Stocks with no forecast stay on Watch until you open them and run Kronos. The levels and thresholds live at the top of `kronos/entry_board.py`.

## Scanner tab

One sortable table with the latest saved analysis for every stock: status, Kronos view and median return, best idea, credit/debit, max loss, IV-stressed net, net ÷ max loss, profitable model paths, IVP, next event (⚠ when inside the trade period) and analysis age. Status chips and filters (status, forecast view, age, no event in trade period, pattern stocks only) narrow the list; click a column to sort and a row to open the stock. **Scan F&O pattern stocks** analyses only pattern-scanner stocks that have F&O contracts, which is much faster than **Scan all F&O**. **Export CSV** saves the filtered rows. Rows are saved snapshots, not live quotes.

## Journal analytics and alerts

- **Mark all open** fetches fresh bid/ask marks for every open trade; one failing trade does not stop the others. The optional **Auto-mark** setting repeats this every 60 seconds while the page is open during market hours (it is a browser-side timer, not a background service).
- Each mark is kept in the trade's history (last 500) and shown as a P&L sparkline. The journal shows P&L as a share of max loss, days held, and distance from the underlying stop.
- Open trades with an exit review or EXIT NOW signal raise a banner, a count on the Paper journal tab, and the browser-tab title. Filter the journal by All / Open / Needs action / Closed.
- **Performance** shows realised results from closing marks only: win rate, realised P&L and return on planned risk, profit factor, expectancy, average win/loss, max drawdown, best/worst, average holding time, an equity curve, and results by strategy and research vs standard trades. Small samples are not evidence of an edge.
- **Export CSV** saves the journal with legs and fills.

## Price sources: permanent Definedge fallback

Live prices come from FYERS first. **Whenever FYERS cannot supply a price** (rate limit, expired login, connection failure), the desk automatically reads the Definedge API instead: stock and option bid/ask books, last price, day open/high/low and daily candles. After a FYERS failure the desk keeps using Definedge for 20 seconds before trying FYERS again, so a rate-limited FYERS is not hammered. Definedge is logged in and its option master loaded in the background when the app starts, so a failover is immediate. Price rows show their source.

Rules that do not change: the market must be open, the contract needs a real two-sided book, and a closed market is never turned into a fresh price. Definedge's REST quote carries only a time of day for the last trade, so a missing or zero time is left empty rather than guessed. The option chain (IV, open interest, volume) is FYERS-only; Definedge cannot replace it, so a chain request still fails if FYERS is down and no recent copy is cached.

## Paper trade permanence

Every paper trade (open or closed) is kept permanently and listed in the Paper journal in every session, including after closing the app or restarting the computer.

- Stored in `.cache/kronos_research/research.sqlite3` with full-sync writes. The database refuses to delete paper trades.
- After each entry, mark or close, a readable copy is written to `.cache/kronos_research/paper_backup/paper_trades.json`, plus one dated file per day.
- If the database file is ever lost or replaced, the next start restores all trades from `paper_trades.json`.
- Back up the `.cache/kronos_research` folder if you want protection against disk failure; it is not committed to git.

## Storage

Repriced strikes keep one replaceable draft per idea and reference the parent analysis's forecast instead of copying its sampled paths. Drafts older than two days are pruned at startup unless a paper trade was priced from one. List views read compact summaries (stored in a `summary` column, backfilled once on first start) rather than full forecast payloads.

## Implemented structures

Put/call credit spreads, call/put debit spreads, iron condors, iron butterflies, butterflies, broken-wing butterflies and 1x2 call/put backspreads. All legs use the same stock and expiry. Unbounded-loss structures are rejected. Calendars and diagonals are deliberately excluded from this version because their risk and valuation require separate treatment across expiries.

## Price, risk and model rules

- Underlying inputs are fetched from FYERS daily candles plus today's explicitly forming live candle. No old scanner option price is used.
- FYERS instrument masters define current stock eligibility, exact contract symbols, strike, expiry, lot size and tick size. Indices are excluded using the cash equity master.
- The desk operates before 15:30 IST during normal weekday trading hours. A price is **fresh** when it is fetched live during those hours and the contract has a real two-sided order book (bid and ask, ask at or above bid). The last-trade age is shown for information but is not required, so rarely traded strikes can still be paper-traded. A stale or one-sided book, a holiday or a closed session fails the check.
- The chain provides comparison snapshots without per-leg book timestamps. These are labelled previews. Fresh depth is mandatory before a paper fill. Each leg must meet spread, OI, volume and top-of-book quantity requirements; ratio structures require quantity for the full leg ratio. Contracts failing these limits are not considered when building ideas.
- Sell fills use bid less slippage, rounded down to tick; buys use ask plus slippage, rounded up. Slippage defaults to 1% per side and cannot be zero.
- Model inference may take time. The underlying is fetched again after inference. A move over 0.5% requires a new forecast; analyses older than 10 minutes also cannot open a paper trade.
- Forecast path lows/highs provide estimated boundaries, never guaranteed maxima. Individual paths are retained instead of averaging away tails.
- Candidate scoring uses the worst average net outcome across unchanged IV, -25% IV and +25% IV at the forecast holding date. These Black–Scholes projections use a 6% annual rate and future spread estimates. They are model scenarios, not executable quotes, calibrated probabilities, or proven profitability.
- Expiry payoff is exact piecewise option intrinsic payoff, less entry costs and a disclosed estimated exit reserve. Maximum loss is found at payoff breakpoints, not at a coarse chart grid. Open-ended profit is labelled uncapped. A separate dashed curve estimates net value at the planned holding date with unchanged IV and the same future spread assumptions as ranking. It is not an executable early-exit mark or a stock-delivery settlement model.
- There is no capital or loss-budget cap; one-lot maximum loss is displayed per idea and open planned loss is summed in the journal. A second open trade in the same stock and duplicate paper entries are blocked atomically. Sector correlation and broker margin are not calculated.
- Review exits at 50% of credit earned, half the planned loss, or the forecast holding deadline (14:45 IST). These are review alerts, not automatic background orders or fills. Closing is not blocked by new-entry OI/volume rules, but needs fresh executable depth. Expired positions require settlement reconciliation; no zero or synthetic settlement price is invented.
- Forecasts, analyses, source checks and paper trades persist in dedicated SQLite tables in `.cache/kronos_research/research.sqlite3`. Every generated forecast is recorded even if the chain later fails or the trade is skipped. Forecast accuracy counts forecast observations, which can overlap; it is not an independent-trade win rate.

## Cost estimates

Zero brokerage. STT 0.15% of sell premium; NSE option transaction charge 0.03553%; SEBI 0.0001%; GST 18% of exchange and SEBI fees; stamp duty 0.003% of buys. No DP charges for closing options. Actual contract notes can include rounding or levies beyond these estimates. Physical delivery, exercise, expiry settlement, funding and margin penalties are not simulated. Close before expiry.

Sources checked 30 October 2026:

- [FYERS option chain](https://fyers.in/api-docs/v3/api/market-data/option-chain)
- [FYERS depth schema](https://fyers.in/api-docs/v3/api/market-data/market-depth)
- [FYERS instrument master](https://fyers.in/api-docs/v3/api/market-information/symbol-master/symbol-master-file/)
- [NSE STT](https://www.nseindia.com/static/products-services/equity-derivatives-securities-transaction-tax)
- [Published charges](https://zerodha.com/charges/)
- [Sensibull stock option chain](https://web.sensibull.com/option-chain)

Sensibull IVP data access returned HTTP 403 during this integration. The public Sensibull event feed responded without authentication and is used as a results fallback. These website interfaces are not a supported commercial API contract and can change. No broker credentials are sent to context sources.

## Verification

`python -m unittest discover -s tests -p "test_kronos*.py"`

`node --check kronos/static/options.js`

Tests use synthetic fixtures for payoff math, exact loss caps, costs, persistence, liquidity, stale quote rejection and opposite-side exits. Synthetic data is never served as live trade data. `tests/test_market_freshness.py::test_renewed_env_is_used_without_restart_and_external_env_wins` is timing-sensitive and can fail intermittently.

## Desk overhaul verification — 30 October 2026

- Saved pattern table includes pattern name, timeframe, detection date, breakout level, support and score. Non-F&O status is explicit.
- Prediction chart includes observed closes, sampled paths, median and 10th–90th percentile band. A later option-chain failure preserves the forecast.
- Payoff remains visible on a quote failure. Failed strike edits revert to the last original priced structure. Quotes show per-leg last-trade times; stale values are display-only. No stale entry fill is allowed.
- The entry panel shows the strategy's maximum loss, warnings and direct fix actions.
- Browser smoke test on the isolated temporary fixture verified manual 950→960 strike edit, recalculated prices/payoff, explicit research acknowledgement, paper entry, mark, and close. Test trades never enter the live journal.
- Live FYERS ICICIBANK forecast, chain and depth were displayed before market close. Stale option legs correctly blocked live paper entry. This was not a live profitability test.
- UI fixture: `python tests/options_ui_fixture.py` serves only synthetic TEST contracts at port 8767 in a temporary database; stop it after manual testing.

Layout references: [Sensibull payoff table](https://blog.sensibull.com/2023/07/06/payoff-table-on-strategy-builder-analyse-widgets/) and [Opstra user guide](https://www.definedgesecurities.com/user-manuals/opstra-user-guide/).

Underlying stop update: payoff profits are green and losses red. Each strategy carries directional invalidation levels derived from recent five-bar support/resistance plus a 0.1 ATR buffer, capped at one ATR from forecast spot. Neutral trades also use nearer short strikes. Missing ATR uses an explicit 1% fallback. Stops are shown on the graph and stored on new paper trades. Refresh mark checks the fresh underlying and prioritises an EXIT NOW alert on a breach. Alerts are manual checks, not automatic fills or guaranteed loss caps. Existing historical paper records without stops are not silently changed.

After-hours EOD analysis: forecasts use completed FYERS daily candles. Today's bar is allowed after 16:00 IST; earlier closed-market periods use the prior session. EOD date and mode are retained in forecast metadata and analysis. Option prices remain labelled reported snapshots. EOD analyses cannot open paper entries, including with research acknowledgement; a new live forecast is required.

Server reload note: Python backend changes require restarting the actual launcher process (`python kronos_dashboard.py`). Reload the browser after restart to obtain the new session token.

## Automatic context update — 30 October 2026

- Automatic IVP fallback reads EquityTimer's public EOD table and retains its actual as-of date, sample count, source and fetch time. This is NOT labelled Sensibull data. At least 60 observations and no more than one weekday of lag (and five calendar days) are required for ranking. Short-history values are displayed separately and excluded from ranking. Manual dated readings remain an override.
- NSE event-calendar plus corporate-actions feeds are fetched independently; Sensibull public single-stock events provide a second results source. Fixed the exchange symbol mapping (`bm_symbol`) and eliminated the failing mandatory homepage warmup. ISO dates are parsed explicitly.
- Shared raw calendars and IVP tables persist in SQLite once per calendar day, rather than issuing one calendar request for every scanned stock. Failed requests have a five-minute retry cooldown. This refreshes when the app requests context; it is not an unattended scheduled service.
- Show the next 90 days even if an event is after the holding date. Events within the holding period retain the entry flag. Each row shows date, days away, trade-period relationship and source links. Individual source failures show partial coverage, never an all-clear calendar.
- Saved analyses load current context without rerunning the model. Repricing also uses current IVP context. Existing recorded paper trades retain their original assumptions.
- Verified DIVISLAB IVP 94.33 (29 Oct, 247 observations) and results on 7 Nov; SBIN IVP 73.68 and results on 3 Nov. ANANDRATHI's reported IVP 5 has only 20 observations and is excluded from ranking. These source observations are not profitability evidence.

## ADX trend tab — 11 November 2026

A separate, Kronos-independent tab: ADX/DMI analysis of F&O stocks, option position selection, paper trading with exit guidance, and learning from your own closed trades. Full documentation is in [docs/adx/](docs/adx/README.md).

Version 2.9.0 adds a decision layer: a market read, a 0-100 score and a Take/Wait/Pass call for each setup, a trailing stop and adjustment advice for open trades, a background watcher that checks every 20 seconds, and learning from reviewed mistakes. See [docs/adx/6-decision-layer.md](docs/adx/6-decision-layer.md).
