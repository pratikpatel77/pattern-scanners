# Calculation and analysis review — 5 November 2026

Completed the code review, fixes, regression checks, and local app checks. The existing layout, scanner thresholds and Kronos model weights were kept. The fixes improve calculation reliability; they do not establish a profitable trading edge.

| Area | What changed |
| --- | --- |
| Pattern inputs | Reject invalid OHLC, negative volume, duplicate dates and unsorted bars. |
| Volume and delivery | Missing daily delivery remains unknown in weekly totals. Fixed empty-volume totals, zero close-strength handling, and bullish/bearish analysis colours. |
| AI calculations | Check signal/date/price alignment. Compute screen percentages and option P&L percentiles from the actual forecast samples. Do not reconstruct a distribution from three quantiles. |
| Option risk | Calculate expiry bounds at zero, every strike and the call tail. Correct unlimited risk and breakevens outside the old plotting range. |
| Option pricing | Correct European put IV bounds and expiry time. Builder estimates buy at ask and sell at bid; missing/crossed quotes cannot produce a trade preview. |
| Costs | Include configured brokerage, taxes, fees and exit costs in scenario results. Custom plans and the builder include an estimated exit-cost reserve. Actual paper fills use available book depth. |
| Strategy ranking | Evaluate intervening underlying stops, including opening gaps, before a later price recovery. Check profitable scenarios under IV stress. Keep reward and risk floors; weak ideas remain Research only. |
| Trade guidance | Fix same-day event handling and forecasts with no usable bars before exit. Missing live stock/exit quotes cannot confirm Hold or Book profit. Saved ideas say Passed at scan, not current readiness. |
| Login refresh | Detect changed credential-file contents even if Windows reports an unchanged file timestamp. Real environment values still take priority. |

| Check | Result |
| --- | --- |
| Final Python suite | 231 tests passed. |
| Browser calculation tests | Passed: actual sample shares, P&L quantiles, fees, exact expiry bounds, and bid/ask entry prices. |
| Prediction workflow checks | 12 passed. |
| Shortlist calculation checks | 6 passed. |
| Cached-universe audit | 1,159 symbols; 13,908 scanner runs; 281 signals; no recorded calculation/invariant errors. |
| Actual Kronos model | Installed Kronos-small ran successfully; 32 paths and valid ordered bands. |
| Live read-only integration | FYERS stock data, option chain, model and ranking worked in a temporary journal. 28 candidate structures were analysed; the two retained ideas were Research only. |
| Final app | Started version 2.5.0 at http://127.0.0.1:8765. Scanner, option chain, builder and saved AI view rendered; no browser console errors observed. Final screen check was after market close and correctly showed last prices. |
| Journal | Backup saved before starting the app. No test trades were entered in the user's journal and no broker orders were placed. |

Evidence: [cached/model audit](calculation-audit-2026-11-05.json), [live integration snapshot](live-options-audit-2026-11-05.json), [screen](calculation-audit-screen-2026-11-05.jpg).

Reproduction: `python -m unittest discover -s tests`; `node tests/test_desk_math.cjs`; `node tests/test_kronos_workflow.cjs`; `node tests/shortlist-model.test.cjs`. The audit scripts are `tools/audit_calculations.py` and `tools/audit_live_options.py`. Live snapshots are dated evidence, not present trade recommendations.

| Remaining limit | Meaning |
| --- | --- |
| Profitability | No walk-forward options backtest or forward paper-performance study was completed. Model scenario shares are not calibrated win rates. No best-return or error-free guarantee is possible. |
| Model assumptions | Option projections use Black–Scholes, an assumed interest rate, model IV and estimated spread/slippage. They do not model dividends, a full changing volatility surface, or actual future liquidity. |
| Calendar | Forecast dates use estimated weekdays/weekly Fridays, not a complete exchange holiday calendar. A live partial-day forecast is labelled as such. |
| Data age | The cached audit found 17 symbols older than 4 November. Their old bars are not evidence of a fresh setup. Current quotes and completed bars still need refreshing. |
| Sample repairs | The cached model run repaired 11 generated candles; the live run repaired 9. This is disclosed in model metadata and is not evidence of forecast accuracy. |
| Stops and costs | Daily OHLC cannot determine every intraday execution sequence. Modelled stops and cost reserves remain estimates; gaps and poor fills can exceed them. |
| Settlement | Physical delivery and exercise charges are not modelled. Plans exit before expiry; payoff curves are theoretical expiry references. |
| Saved results | Re-run old analyses to use the corrected calculations. Existing entry fills and trade history remain recorded snapshots. |

Charge references checked during the review: [NSE option STT](https://www.nseindia.in/static/products-services/equity-derivatives-securities-transaction-tax) and [NSE circular FA73061](https://nsearchives.nseindia.com/content/circulars/FA73061.pdf). The transaction-fee estimate combines exchange charges and IPFT. Settlement boundary: [Zerodha physical settlement policy](https://support.zerodha.com/category/trading-and-markets/trading-faqs/f-otrading/articles/policy-on-physical-settlement).
