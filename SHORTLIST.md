# Trading shortlist

Restart **Run Pattern Scanners.bat**, then open **Shortlist** in the top bar.
The original scanner, options desks, AI research and paper journal remain available.

1. Use **All setups**, filter by direction or timeframe, and pin stocks with the star.
2. Add a note with your entry condition, reason to wait or invalidation rule.
3. Use **Pinned stocks** for daily review. Notes and pins survive restarts, and remain visible when a stock no longer has a scanner setup.
4. Click **Refresh quotes**. This reloads setups and checks quotes for stocks matching the search, list, direction and timeframe filters. It does not rescan the market; use the main **Refresh prices** button for that.
5. Filter by **Near trigger**, **Past trigger**, **Past stop** or **Check quote**. Open the chart to review the setup; bullish rows also link to AI research.
6. **Export CSV** saves exactly the displayed rows, including notes, setup dates, quote times and sources.

| Review | Meaning |
|---|---|
| Near trigger | Last price is within 1% before the trigger |
| Past trigger | Last price crossed the trigger; candle confirmation is still needed |
| Past stop | Last price crossed the saved invalidation level |
| Waiting | Price is more than 1% before the trigger |
| Check quote | Saved, undated, old or closed-market price; reference only |
| No setup | A saved stock no longer has a current scanner row |
| Check levels | Missing or inconsistent trigger/stop levels |

Only provider-marked fresh quotes with both a dated price and a fetch time within 60 seconds, during a reported open session, get current review states. Browser display age is rechecked every 15 seconds and before CSV export. Quotes refresh on request; this is not an alert service. Daily and weekly rows stay separate. Stocks with both directions carry a warning. Counts are unique stocks across the full loaded list; the table count follows filters.

These are price-level checks against saved patterns, not entry recommendations, confirmed breakouts, or proven profit signals. Always review the setup date. No new backtest, performance claim, order flow, fee assumption or broker configuration is introduced.

Pins and notes use the `review_shortlist` table in `.cache/kronos_research/research.sqlite3`. Back up that database with your research folder. The existing paper-trade JSON backup does not include these notes. Filters are saved in this browser only. Unpinning does not erase a note.

Validation: `python -m unittest discover -s tests -q` and `node --test tests/shortlist-model.test.cjs`.
Browser smoke test: `node tests/shortlist-browser.test.cjs` with Playwright on the Node module path and Microsoft Edge installed. It starts an isolated local fixture with temporary data; it never uses broker logins or the real journal.
