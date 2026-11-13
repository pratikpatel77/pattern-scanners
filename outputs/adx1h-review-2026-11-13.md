# ADX 1H review — 13 November 2026

Verdict: useful research and paper-trading groundwork, but not yet a robust unattended trading system. The indicator logic is much stronger than the entry gates and option-selection checks. This review does not establish profitability.

Review only: no application code, settings or real trades changed. Added a synthetic audit script and this report. Existing user edits were preserved. No live broker/API or browser end-to-end trial was run.

## Checks

| Check | Result |
|---|---|
| Existing ADX tests: `python -m pytest tests/test_adx.py tests/test_adx_hour.py -q` | 66 passed |
| Application Python suite: `python -m pytest tests -q` | 329 passed, 1 failed; 22 subtests passed |
| Failed application test | `tests/test_entry_board.py:170`: expected `ripe`, got `watch`; outside the ADX-specific tests |
| Unscoped `python -m pytest -q` | Collection blocked by vendored Kronos tests: NumPy/matplotlib binary mismatch and missing `model` import |
| Added audit: `python outputs/adx1h-audit-2026-11-13.py` | Eight synthetic checks reproduced the current defects; assertions describe defects, not correct behaviour |

## Findings

| Priority | Finding and evidence | Impact | Required change |
|---|---|---|---|
| High | Entry bypasses the decision shown on screen. `adx_desk.py:466-484` never calls `brain.decide`; `_auto_enter` at 636-666 does not enforce grade. Audit reaches the mocked fill boundary for Grade C and event-related credit-spread cases whose displayed call is `Pass`. The real `paper()` method also accepts `armed`. | Auto entry can take rejected trades. Manual entry can buy before the trigger; auto can also proceed if the row changes to Armed between its first check and `paper()`. | Use one shared final entry gate for UI, manual and auto paths, including current trigger, grade, events, quote freshness and selected structure. |
| High | Stale bars do not block entry. `board()` returns `behind`, but `_auto_enter` ignores it. Audit dispatches an entry from a behind board with fresh mocked stock quotes. | A live price can trigger an obsolete setup after a failed hourly refresh. | Require the latest completed stock and benchmark bars, and no unresolved scan error, immediately before entry. |
| High | A nonempty old API response counts as refresh success (`adx_hour.py:152-194`). The scan saves the current time stamp even though its bars remain behind (`adx_desk.py:162-195`). Audit reproduces old data plus a current scan stamp and no error. | The automatic retry is suppressed until the next stamp change; a failed symbol can remain absent or stale without a clear per-symbol error. | Validate the returned latest bar, track each symbol's freshness/error, and retry missing bars independently of the saved stamp. |
| High | Option ranking excludes fees and exit spread/slippage (`adx_options.py:123-151, 237-263`). Any positive score can become `best`; no minimum net reward gate exists. Actual paper entry adds costs and uses order-book depth (`desk.py:570-601`) but does not recheck the chosen trade's target/stop payoff at those fills. | The chosen strike/structure can lose its apparent advantage after execution costs. | Rank net stressed scenarios and recheck them against actual entry fills before saving a trade. |
| High | Short expiry is only a warning (`adx_desk.py:390-419`). Audit gets a recommended position with 2 days left despite the hourly desk wanting 6. `trades()` says exit at 3 days or less. | A fresh entry can already be inside its own mandatory exit window. | Reject expiry below the hard floor when no suitable later expiry exists. |
| High | Stops crossed between watcher samples are not latched. `trades()` uses historical highs/lows for best price but checks the stop only against the latest price (`adx_desk.py:550-570`). Audit: stop 95, post-entry low 94, recovery to 100 yields `hold`. | A restart/outage can lose evidence that the trade had already failed. | Preserve stop/target breach state, replay available post-entry data conservatively, and distinguish unknown intrabar order from a known clean hold. |
| Medium | Definedge aggregation accepts any nonempty hour (`adx_hour.py:139-140`). Audit turns one 09:15 minute into an accepted 09:15 hourly candle. | Truncated history can alter ADX, ATR, levels and daily context while appearing complete. | Validate minute coverage and session boundaries; flag partial data. Do not assume every absent minute is corruption, since feeds may omit no-trade minutes. |
| Medium | Open-option P&L fetch is capped at the first 50 unique legs (`adx_desk.py:515`), with no batching. | Larger books can use old marks or no P&L, weakening the 50%-loss check. | Batch every open leg, show per-leg freshness, and avoid treating old marks as current. |
| Medium | ADX metadata is written after the generic journal open (`adx_desk.py:483-500`). `_trades()` unconditionally reads `payload['adx']`. | A failure or concurrent read between writes can expose an hourly record without required metadata, breaking the desk. This is a source-level failure-window finding, not a crash-injection reproduction. | Save source, setup, stop and signal ID atomically with the trade. |

## Analysis and strike selection

| Area | Assessment |
|---|---|
| ADX/DMI maths | Uses Wilder smoothing with explicit 13-period DMI and 8-period ADX. Existing tests check a reference movement example, bullish/bearish behaviour and detection without future bars. These settings differ from a typical 14-period chart; compare identical settings before calling a chart mismatch a bug. |
| Bigger timeframe | The hourly desk uses the last completed day. Its prefix-consistency test passes, supporting no use of the current day's future close in that context. |
| Session bars | Starts at 09:15. The final 15:15 candle is only 15 minutes, yet counts as one full indicator/holding bar. This is a deliberate convention, not seven equal one-hour periods. Holidays and special sessions are not calendar-aware. |
| Volatility input | `_day_closes` includes the current partial day's last hourly close. It can change the daily-volatility estimate and IV/HV ranking intraday. Prefer completed days or explicitly model the partial session. |
| Long-option strikes | Tests ATM and two listed rows ITM/OTM. Delta is available in the chain but is not used to target exposure. Different strike spacing can produce very different delta and premium risk. |
| Spreads | Debit short is near the stock target; credit short is beyond the stock stop with a hedge. This is a sensible starting rule, not proof that the selected strikes are best. Only a small candidate set is compared. |
| Liquidity | Requires positive two-sided prices and minimum OI. Wide spread is a score penalty, not a hard rejection. Entry fills do check available depth. Exit liquidity and changing spreads still need stress tests. |
| Scenario assumptions | Target, stop and flat are priced at one fixed future horizon and today's IV. The stop could occur much earlier, and IV may change sharply. These are scenarios, not expected returns or calibrated win probabilities. |
| Holding horizon | Fifteen bars is a little over two normal sessions; the pricing estimate is two calendar days. Weekends, holidays and the short closing bar can make the actual holding horizon quite different. |
| Learning | Small trade counts change rankings and management rules. The changes are bounded in places, but remain unvalidated and can fit noise. Freeze/version rules when measuring out-of-sample results. |
| Portfolio risk | One open trade per stock per book is enforced. There is no aggregate capital, maximum total loss, sector exposure, correlated-position or daily-loss gate in hourly auto entry. One lot is not equal money risk across stocks. |
| Exits | Entry is automatic; exits and adjustments are advice, with manual Close. The watcher is not an automatic stop-execution system. Slow sequential entry-chain calls can also delay later monitoring passes. |

ADX measures trend strength; DMI supplies direction. The use of both is conceptually sound, but the indicator alone cannot validate a trading edge. Reference: [Fidelity ADX guide](https://www.fidelity.com/viewpoints/active-investor/average-directional-index-ADX).

## What is needed before calling it solid

| Order | Work |
|---|---|
| 1 | Fix and regression-test entry gates, candle freshness, expiry floor, cost-aware ranking and persisted breach handling. |
| 2 | Make entry metadata atomic; add full-book quote coverage, portfolio loss limits, and visible watcher failure status. |
| 3 | Run controlled market-hours paper trials covering broker failure, partial candles, restart, fast price moves and spread/depth changes. |
| 4 | Test frozen hourly rules on unseen data and multiple market regimes. Report options results after fees and nonzero slippage, with actual strike/expiry and IV assumptions. Include drawdown, exposure and sample size. A stock-only backtest is not an options profitability result. |

Until then, use it only as a supervised research/paper tool. Passing the existing tests does not clear the defects reproduced here.
