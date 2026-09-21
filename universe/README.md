# Market-cap universe

`nse_by_market_cap.csv` is the bundled list of NSE stocks, largest market cap first (snapshot: 14 Sep 2026).
The scanner takes the first N rows for `--top N`.

To refresh it, drop a newer TradingView screener export into this folder (any `*.csv` with a
`Symbol` column, sorted by market cap); the newest file wins. Other CSVs here are git-ignored.
Without any file, the scanner falls back to NSE's NIFTY Total Market list (~750 names).
