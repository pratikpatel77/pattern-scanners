# The decision layer

Added 11 November 2026 (app version 2.9.0). One file, `kronos/adx_brain.py`, holds every rule on this page as a pure function. It sits on top of the setups and the option picker; it does not change how a setup is detected or how a position is priced.

It does five things.

| Step | What it answers | Where you see it |
|---|---|---|
| Market read | What are the index and the F&O list doing? | Strip at the top of the ADX tab |
| Score | How much evidence does this setup have? | **Score** column, 0 to 100 with a grade |
| Call | Take, Wait or Pass? | **The desk's call** in the detail panel |
| Management | Should the stop move? Should the position change? | **My ADX trades**: Hold, Adjust, Book, Exit |
| Learning | What did the closed trades teach? | **What your trades have taught so far** |

A background watcher runs the management step every 20 seconds in market hours.

## 1. Market read

Computed once per scan and saved with the board.

- **Nifty**: the same DMI/ADX rules as a stock. Uptrend = +DMI dominant and price above the 20 EMA; downtrend = the mirror; otherwise "no trend" (ADX under 25) or "trend and price disagree".
- **Breadth**: the share of scanned F&O stocks above their 20 EMA. DMI dominance and ADX-above-25 shares are shown but not used, because dominance changes hands slowly.
- **Volatility**: Nifty's 20-day realised volatility and its percentile within the last 250 sessions.

These become one number, the **tilt**, from −1 to +1: 60% index (full weight when ADX is above 25, 0.6 when below) and 40% breadth (50% above the EMA is neutral, 80% or 20% is a full lean). A setup is "with the market" when the tilt is 0.35 or more in its direction, "against" when it is 0.35 or more the other way.

## 2. Score

Every setup starts at 50. Each factor adds or removes points and the detail panel lists them, so a score can always be read back.

| Factor | Trend setups | Range setups |
|---|---|---|
| Rules passed | −12 to +12 (70% passed = 0) | −8 to +8 |
| Weekly trend | +8 with, −8 against | not used |
| Market | tilt × 12 in the setup's direction | +6 when the market has no direction, −6 when it trends |
| Relative strength | up to ±6: 20-day return against Nifty, 5% = full | not used |
| ADX direction | +4 rising, −2 falling | not used |
| Stop distance | −4 when the stop is more than 6% away | not used |
| Length of the range | not used | up to +10 (30 bars = 0, 60 bars = 10) |
| Volatility | not used | −5 when index volatility is above its 80th percentile |
| Your record | up to ±8 per condition (see Learning) | same |

Grade **A** is 70 or more, **B** is 55 to 69, **C** is below 55. The board is ordered by verdict, then score.

The weights are judgement, not fitted values. The earlier stock-price replay found no factor that helped consistently, so the score orders setups by how much agrees with them. **It is not a win probability.**

## 3. Call

| Call | When |
|---|---|
| **Pass** | The setup is Skipped or only a Watch · no option position pays for its risk · grade C · a scheduled event falls inside the trade and the recommended position has sold options |
| **Wait** | Armed but not triggered · the chain is stale or the market is closed · no option position could be priced ("option prices unavailable") · the stock price is old or missing in market hours ("waiting for a live stock price") |
| **Take** | Everything else. The reason says when the setup trades against a strong market |

Below the call are the points for and against: the score factors, whether the recommended position buys cheap or expensive options, today's open-interest lean from the Sensibull feed when it is available, an event inside the trade, an unchecked event calendar and a thin chain.

A setup whose option chain is stale (market closed, or the chain feed down) never shows **Take**: the call reads **Wait — "Setup valid — waiting for live option prices"** instead, so it cannot be mistaken for a trade you can enter right now. An Armed setup keeps its own, more specific "not triggered yet" reason even when the chain is also stale. (Fixed 11 November 2026, version 2.9.2.)

The call is advice. Paper entry is still blocked only by the rules in [4-trade-tracking-and-learning.md](4-trade-tracking-and-learning.md).

## 4. Management

### Trailing stop
Once the stock has moved one risk-unit (entry to stop) in your favour, the stop follows one risk-unit behind the best price since entry and never sits below the entry price. Example: entry 100, stop 95. At 105 the stop moves to 100. At 107 it moves to 102. A trade that falls back to the raised stop shows Exit.

"Best price since entry" is read from any full day's bar strictly after entry, the stock price stored with each mark, and the live price now. The entry day's own daily bar is never used — it spans the whole session, so its high or low can belong to the time before the trade was even opened. Instead the entry day is covered by the watcher's own poll every 20 seconds in market hours, each one folded into the running best.

That running best is stored on the trade itself (`high_water` in its `adx` block), and the journal enforces that it only ever moves in the trade's favour: every write to a paper trade (the watcher's update, Mark, Mark all, Close) re-reads the saved peak inside the same database write lock and keeps the better of the saved and incoming values. So a Mark that read the trade, waited on the network, and wrote back an older snapshot cannot loosen a stop the watcher tightened in the meantime. (Fixed in 2.9.5; before that only the watcher's own updates were protected.) A stock that goes 100 → 107 → 101 with no Mark taken at 107 still trails the stop from 107, not from 101, and this holds even when the watcher and a page load check the same trade at nearly the same moment. (Fixed 11 November 2026, versions 2.9.1–2.9.3; trades opened before 2.9.1 start tracking from the first read after the upgrade.)

### Adjust
A new action between Hold and Exit. One sentence says what to change.

| Situation | Advice |
|---|---|
| Bought option, in profit, stock at least half way to the target | Sell the strike nearest the target against it. It becomes a debit spread |
| Bought option or debit spread, losing, stock within 25% of entry after 7 days | Close now rather than waiting for the stop |
| Iron condor: the stock has used up 70% of the room to a sold strike | Close that side, or the whole position |
| Any position with sold options and a scheduled event within 2 days | Close before the event |

Adjustments are suggestions. The desk does not place or record the adjusting trade; to act on one, close the paper trade and open the new position.

### Watcher and alerts
`adx-watch` is a server thread. Every 20 seconds in market hours it re-checks open trades and Armed setups and writes an alert when:

- an open trade first changes to Exit, Book or Adjust, or
- a grade A or B setup goes from Armed to Enter.

Each alert is logged once (last 300 kept, table `adx_cache`, key `alerts`). The page reads the log every 15 seconds and shows a message, plays a short tone and, if you press **🔔 Alerts** and allow it, raises a desktop notification. The log is written whether or not the page is open, but a notification needs the page open in a browser tab.

## 5. Learning

### What is stored at entry
The score, the market regime and the conditions: with, against or flat to the market; with or against the weekly trend; the grade; whether options were cheap, fair or expensive.

### Review of each closed trade
Uses the P&L and stock price saved with every mark, and the first Exit alert logged for the trade.

| Mistake | Test |
|---|---|
| Closed after the exit signal | Closed on a later day than the first Exit alert, and more than 10% of the maximum loss worse |
| Gave back a profit | Peak marked profit was at least 25% of the maximum loss and the close was under half the peak |
| Right on the stock, wrong position | The stock moved 0.5R or more your way and the position still lost |
| Stock went nowhere | The stock finished within 0.5R of entry and the position lost |
| Lost more than planned | The loss was over 1.3 times the planned loss at the stop |

A loss inside the plan with the stock going against you is not a mistake.

### What changes

| Evidence | Change |
|---|---|
| 3 or more closed trades sharing a condition | That condition moves the score by up to ±8 points |
| 2 "gave back a profit" | Book at 65% of the way to the target (was 80%); trailing starts after 0.75R (was 1R) |
| 2 "stock went nowhere" | Bought positions are flagged after 5 flat days (was 7) |
| 2 bought options that were "right on the stock, wrong position" | Bought options are ranked 15% lower against spreads |
| 1 late exit | The cost is shown. No rule changes |

The score change is the group's average return on risk with six imaginary break-even trades mixed in, times 30, capped at 8. Three losing trades at −60% of risk move the score by 6 points; it takes many more to reach 8. The earlier rules (a setup flagged after 5 losing trades, a structure type marked up or down after 5) still apply.

Learning is recomputed from the trade journal every time. Nothing is stored separately, so a rule changed in `adx_brain.py` is applied to every past trade the next time the page loads.

## Limits

- The score weights and thresholds are judgement. They have not been fitted or backtested, and option prices cannot be backtested here.
- Trades opened before version 2.9.0 have no stored conditions. They are reviewed for mistakes but do not count toward condition scores.
- A mistake review needs marks. A trade closed without ever being marked can only be judged on its final P&L.
- The trailing stop's running high-water mark is persisted on the trade and merged atomically by the journal on every write, so a peak is kept even without a Mark and survives two near-simultaneous reads of the same trade. Trades opened before version 2.9.1 start tracking from their first read after the upgrade, not from their true entry-day peak. An entry-day peak that lasts less than the watcher's 20-second poll, with no Mark taken during it, can still be missed — the entry day's own daily bar is deliberately never used as a fallback, since it can hold prices from before the trade was opened.
- Market read uses the saved daily bars. It is as current as the last scan and does not update intraday.
- A failed live-price refresh never passes silently (version 2.9.4). The last known quote is still used, but it carries its age and a stale flag: the board and My ADX trades show "stale 42s" (or "no live price" / "close" when nothing arrived), the call becomes Wait, and exit advice on an open trade adds a warning that the price is old. Outside market hours no quote is expected, so nothing is flagged.
- Live stock quotes are cached per symbol for 8 seconds. Before version 2.9.2 the cache was one shared batch keyed only by time: a request for a different set of symbols moments later (the board asking for every row, then the trades panel asking only for open positions) could silently reuse the first batch and leave the rest showing yesterday's close. Fixed: each symbol now has its own 8-second freshness, and a symbol missing from the cache is always fetched.
- One lot per trade. There is still no position sizing, portfolio limit or margin figure.
- Alerts need the app running. There is no phone notification.
