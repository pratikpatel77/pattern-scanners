# ADX trend tab — overview

A section of the Options desk (`/options` → **ADX trend**) that analyses F&O stocks with the ADX/DMI indicator, picks the option position that fits, paper-trades it, tracks it to the exit, and learns from your own closed trades.

It is **separate from Kronos**. No Kronos forecast is read anywhere in it, and the Kronos position monitor skips ADX trades. The other desk tabs are unchanged apart from the tab button and one hook.

## What it does, in one pass

1. **Scans** every F&O stock (217 with saved daily bars) on daily candles.
2. **Finds setups**: six trend setups from the book *ADXcellence* (both directions), plus a "range" row for stocks with no direction.
3. **Gives a verdict** per stock: Enter, Armed, Range, Watch or Skipped.
4. **Picks the option position** from the live chain: nine structures compared, one recommended, with rupee figures for the target, the stop and a stock that goes nowhere.
5. **Paper-trades it** at live bid/ask.
6. **Tracks it** every 20 seconds in market hours and tells you when to Exit, Book profit or Adjust.
7. **Learns** from your closed ADX trades: it adjusts the score, and repeated mistakes change the exit rules.

Each setup also gets a score against the market, the weekly trend and your own results, and a Take, Wait or Pass call. See [6-decision-layer.md](6-decision-layer.md).

## Reading order

| File | Read it for |
|---|---|
| [1-design-and-reasoning.md](1-design-and-reasoning.md) | Why it is built this way: the book review, what was changed, what the research found |
| [2-stock-analysis.md](2-stock-analysis.md) | Indicators, every setup's rules, verdicts, ranking |
| [3-option-selection.md](3-option-selection.md) | How a position, strike and expiry are chosen |
| [4-trade-tracking-and-learning.md](4-trade-tracking-and-learning.md) | Paper trades, exit rules, alerts, the learning loop |
| [5-technical-reference.md](5-technical-reference.md) | Files, API, storage, constants, tests, limits |
| [6-decision-layer.md](6-decision-layer.md) | Market read, the 0-100 score, Take/Wait/Pass, trailing stop and adjustments, the 20-second watcher, learning from mistakes |
| [book-strategies/](book-strategies/README.md) | The six strategies from the book, entry and exit rules only |

## Using it

1. Refresh prices on the main workspace so daily bars are current, then press **Rescan** in the ADX tab. (The tab warns when bars are a session behind.)
2. Filter to **Enter** or **Armed**. Click a stock.
3. Read **What to trade**: the recommended position, what it makes at target, loses at stop and loses if the stock stalls, and the stop-loss and exit plan.
4. Press **Paper trade** during market hours. The trade moves to **My ADX trades**.
5. Check **My ADX trades**. Each open trade shows Hold, Book or Exit with the reason. Use **Mark** for an exact bid/ask P&L and **Close** to exit.

## The rule it follows

KISS. One recommendation with its reason, a plain exit plan, and nothing that cannot be explained in a sentence. Where the book was vague (trendlines, "near the 20 EMA"), a measurable rule replaced it. Where the data could not decide (there is no expired-option history), the desk waits for your own trades instead of pretending.
