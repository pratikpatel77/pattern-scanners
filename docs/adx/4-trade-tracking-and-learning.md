# Trade tracking and learning

## Placing a trade

**Paper trade** on the recommended position (or **Trade** on any other row of the comparison) records one lot with the desk's existing paper journal:

- Fills at live bid/ask (buys at the ask, sells at the bid, walking deeper levels if needed), with brokerage, exchange, STT, SEBI, GST and stamp charges.
- No broker order is sent. Entry needs market hours and a two-sided book.
- A confirmation shows the worst case before it is recorded.
- It is blocked if the setup is Skipped or has become only a Watch, or if you already hold an open paper trade in the same stock.
- The record stores the setup, direction, structure type, trigger, stop, target (or the range), quality, planned loss and planned gain. This is what exit guidance and learning use later.

Trades opened here are tagged `source: adx`, so the Kronos monitor ignores them.

## Following the trade

Open trades are re-checked **every minute while the page is open, on any tab**. A banner on the ADX tab, a red tab label and a pop-up message appear when an action is needed.

### Guidance is from the stock, not from a forecast

| Action | Trend trades | Range trades |
|---|---|---|
| **Exit** | Stock beyond the stop · the opposite DMI takes dominance on the daily chart · 3 days to expiry · half the amount at risk is lost · held 21 days without reaching the plan | A sold structure (iron fly/condor): stock leaves its base · 3 days to expiry · half the risk lost · held 21 days |
| **Book profit** | Stock reaches the target · covers 80% of the way to it · 70% of the maximum profit is earned | A bought structure (straddle/strangle): the breakout happens while in profit, or ADX rises above 25 while in profit · 50% of the maximum profit on a sold structure |
| **Hold** | Everything intact | Everything intact |

The order is deliberate: protective exits (stop, dominance, expiry, loss) are checked before profit-taking.

### Why these rules
- **The stop is on the stock**, because the setup is a statement about the stock. If it is wrong, the option position's reason for existing is gone, however the option is priced.
- **DMI dominance** is the book's own signal for a change of trend. It lags in high volatility, so it is a backup to the price stop, not the primary one.
- **3 days before expiry**: options lose value fastest and spreads widen in the final days; a close needs an executable two-sided book.
- **21 days**: a swing trade that has not worked in a month is a different trade.
- **Half the risk lost**: a limit that does not depend on the stock stop alone, useful when the option moves against you faster than the stock.

### P&L shown
- During market hours the P&L is **estimated from last traded prices** every minute (marked "est.").
- **Mark** fetches live bid/ask and records an exact snapshot, with exit charges.
- **Close** records the exit at live opposite-side bid/ask. Expiry itself is not simulated: close before it.

## Learning from your trades

### What is recorded
Every closed ADX trade carries its real option P&L and the planned maximum loss. Return is P&L ÷ planned maximum loss.

### What changes, and when

| After | Effect |
|---|---|
| **5 closed trades in a setup and direction** | If their total P&L is negative, the setup is ranked lower (−30) and flagged "Your trades in this setup are losing so far (x/y won)" |
| **5 closed trades of one structure type in a setup** (bought option, debit spread, credit spread, long volatility, short volatility) | That type's score is ×1.2 if it made money, ×0.7 if it lost |

### My ADX trades tab shows
Open trades (with progress to target for trend trades), all closed trades, and a "what your trades have taught so far" summary: wins, P&L and average return on risk, grouped by setup and by structure.

### Why this design
- **No option backtest exists.** Expired contract prices are not available, so the only trustworthy result is a trade you actually took (paper or real) with real bid/ask fills and charges.
- **A threshold of 5, not 30.** With a handful of trades a profit factor means little, so the learning is deliberately mild (a ×1.2 / ×0.7 nudge and a flag), never a hard block. It lets the record matter early without letting two lucky or unlucky trades flip the system.
- **It never hides a setup.** It reorders and warns. You decide.
- **It learns about structures as well as setups**, because a good setup traded with the wrong structure (a bought call in expensive options) loses for reasons the setup does not explain.

### Honest limits
- Five trades is not statistical proof of anything. The numbers shown are a diary, not an edge estimate.
- Paper fills are optimistic about size: one lot at the best bid/ask.
- Learning is per setup and structure; it does not yet learn about market regime, stock sector, or hold time. Those are the next things worth adding once there are enough trades to support them.
