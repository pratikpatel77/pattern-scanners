# Design and reasoning

## What you asked for

- Technical analysis of F&O stocks with the ADX indicator, trading through **options**.
- Good, robust rules; freedom to add or remove features from your notes and the book.
- An analytical core that **learns from each real trade**.
- Independent of everything else, especially Kronos.
- Simple (KISS), with end-to-end guidance: what to buy, how much risk, the stop, and when to exit.

## The review of your notes and the book

Your sources: the study-guide markdown, the first part of the book in markdown, and the scanned PDF (*ADXcellence: Power Trend Strategies*, C. Schaap, 2006). The PDF was read page by page for the strategy chapters (8–13, 16–18) and the appendix. The notes were mostly right but differ from the book in places that matter for rules:

| Topic | Your notes | The book |
|---|---|---|
| ADXodus | Breakout entry | Enter on the **first pullback to the 20 EMA** after the breakout; needs a 30-bar low-ADX base |
| ADXpress | Momentum continuation | Needs a 20-day high, a crossover high, DMI pivot above 25, ADX above 15 and rising, and a **2–5 bar pause near the 5/10 SMA** |
| ADXit | Defined exit rules | **Not in the book.** Chapter 18 is about trader psychology. The exit rules in this desk are its own |
| Core rule | ADX above 25 = trend | Correct. Also: **ADX under 25 → enter on breakouts; above 25 → enter on retracements** |
| DMI dominance | Crossover = signal | A crossover alone fails often. Dominance needs a **crossover high** (above 25 and above the other line's preceding peak) or a **cross and hold** |

### What was kept
The 13/8 settings, the 25 level, DMI dominance, the six setups, entering with the trend only, trading on price first and the indicator second, and the book's own caveat that countertrend trades are for swing traders.

### What was changed, and why

| Change | Reason |
|---|---|
| Hand-drawn trendlines → high/low of the low-ADX base | A rule a computer cannot test is not a rule |
| "Near the 20 EMA" → measured in ATRs | Same |
| ADXtra breakout needs a **daily close** beyond the base | Avoids intraday false breaks |
| Stop is 0.5 ATR beyond the pivot, target 2R | Needed one consistent plan per setup. 0.1 ATR was stopped out by noise in testing |
| Added a **range** view for stocks with no direction | You asked for straddles/condors "if no clear direction with evidence"; low-ADX bases are that evidence |
| Added nine option structures with capped risk | The book is about stock trades; you want options |
| Dropped the idea of backtesting options | See below |

## Why there is no backtest and no history gate

Two separate findings drove the final design.

**1. The stock-price replay found no edge.** A one-off research run replayed all six setups over 217 F&O stocks (mostly 2024–2026, 2,766 trades, measured as stock moves in R). Results per setup sat between about **−0.20R and +0.13R** per trade. Changing the stop width and target made little difference, and none of the book's quality sub-rules (weekly alignment, deep base, wide DMI separation) helped consistently across both halves of the data. Results also swung sharply from quarter to quarter with the market.

**2. It was the wrong gate anyway.** The first version promoted a setup to "Enter" only if that replay showed an edge. With 200+ stocks that meant the board was empty, and you correctly pointed out it cannot be right that nobody is ready to trade. More fundamentally, option prices cannot be backtested here: expired-contract history is not available, and a stock-price result says little about an option's profit (time decay, implied volatility and the spread all matter).

So the design became:

- **Setups are shown when the ADX rules are met.** They are rules from a published method, not a proven edge, and the docs say so.
- **The record that matters is yours.** Every ADX paper trade's real option P&L feeds the ranking after enough results (see [4-trade-tracking-and-learning.md](4-trade-tracking-and-learning.md)).
- **No fake certainty.** The desk never shows a win rate it has not measured.

## Independence from Kronos

You wanted trades based on ADX analysis of the underlying only.

- Setups, verdicts, option choice and exits read only daily bars, the live option chain and scheduled events.
- ADX paper trades are placed by the ADX tab, so the Kronos pre-entry forecast check never runs.
- `kronos/position_watch.py` skips trades marked `source: adx`, so the Kronos monitor does not manage or alert on them.
- The tab reuses shared plumbing only: the price cache, FYERS/Definedge quotes, the option chain and the paper journal's storage. It does not touch Kronos tables.

## Why options are chosen the way they are (summary)

Detail in [3-option-selection.md](3-option-selection.md). The reasons in one place:

- **Capped risk only.** Naked short options have open-ended loss. Every structure offered is hedged and its worst case is a known number.
- **Compare, don't guess.** "Buy a call or a spread?" has no universal answer. It depends on how far the target is, how much options cost versus how much the stock actually moves, and how much time is left. So all candidates are priced for the stock's own plan and compared on the same terms.
- **Use what the stock actually does.** Expected moves come from the stock's realised volatility, because comparing option prices only against themselves cannot tell cheap from expensive.
- **Time matters.** An expiry is used only if it gives the move enough days and the chain is tradable; otherwise the next one is used.
- **Events matter.** A results date inside the trade can gap a stock through a short strike, so sold structures are marked down and bought ones up.

## Principles used throughout

1. **Price first, indicator second** (the book's own rule): every setup requires a price structure (pivot, base, pullback), not just an indicator reading.
2. **One copy of each rule.** Bearish setups run the bullish rules on a mirrored (negated) price series, so a fix applies to both sides.
3. **No look-ahead.** Every rule reads only bars at or before the bar being judged; a test checks this.
4. **Fail visibly.** Stale bars, closed markets, missing quotes, an unchecked event calendar and thin chains each produce a visible warning, never a silent default.
5. **Estimates are labelled estimates.** Outcome figures are Black-Scholes estimates about a week ahead (21 days for ranges), not quotes.
