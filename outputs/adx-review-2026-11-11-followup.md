# ADX changes: follow-up review

Reviewed 11 November 2026. This review checked the current local code and docs, including the new decision layer. No strategy source was changed. No broker or paper trades were placed. Entry checks used a mock that stopped before journal writes.

## Verdict

The new market context, clear score factors, and background watcher improve research and monitoring. They do not establish a profit edge. Important correctness gaps remain, including a new trailing-stop failure.

## Tests

| Check | Result |
|---|---|
| ADX unit tests | 30 passed |
| Full Python suite | 293 passed; 1 failed |
| JavaScript syntax | `node --check kronos/static/desk-trend.js` passed |
| Existing failure | `test_entry_board.TrackingTests.test_first_pass_is_a_baseline_then_changes_are_logged`: expected ripe, got watch, as in the earlier review |
| Live browser, alerts, market fills | Not verified |

Test output: `outputs/adx-review-tests-2026-11-11-followup.txt`.

## Findings, in priority order

| Priority | Finding | Evidence and fix |
|---|---|---|
| High | Trailing stops can move backwards and miss an exit | In an isolated two-read test, entry/trigger 100, original stop 95, target 110: live price 107 produced stop 102; the next price 101 produced stop 95 and Hold. `adx_desk.py:450` reconstructs the best price from completed later daily bars, manual marks, and only the current quote. It saves neither the observed live peak nor stop. Persist the best price and stop per trade; stops must only tighten, across polls and restarts. Test both directions and the entry day. |
| High | Entry safeguards still allow an untriggered trade | `adx_desk.py:378` rejects skip/watch but accepts armed. A mock confirmed armed reaches execution. UI buttons also allow armed (`desk-trend.js:180`). Require a fresh underlying quote and a crossed trigger at execution time. |
| High | A Take call can appear when the option plan failed or is stale | `brain.decide(row, None)` returns Take for a grade A Enter row. `detail()` passes None after a pricing ValueError. A stale plan can also produce Take. Use Wait/Unavailable when planning fails, quotes are stale, or required market data cannot be checked. Distinguish a stock-only opinion from an executable option call. |
| High | Missing live prices can silently become old closes | `_live_quotes` uses one cache for all symbol requests. Request A, then B within eight seconds: B is absent and no second fetch occurs. Board/trades fall back to daily closes. Entry also does not enforce the board's stale-bars warning. Key the cache per symbol, fetch missing symbols, expose source/age/errors, and block new entries on stale required data. Keep protective expiry advice visible during outages. |
| Medium | Range rules still do not require no dominance | `adx_core.py:375` gates on low ADX duration but not `s.dom[i] == 0`. A controlled-state probe with dominant DMI and a qualifying low-ADX streak still produced a Range row. Failed checklist items only lower quality. Decide the intended rule explicitly and enforce it; low ADX alone is not evidence that a range will hold. |
| Medium | Learning changes exit rules after only two examples | `adx_brain.py:312` changes booking to 65% and trailing activation to 0.75R after two gave-back labels, across all trades. The six-trade prior applies to score effects, not these policy changes. Ordinary favourable-price reversals are not proof of a trading mistake. Keep lessons advisory; freeze and version exit policies and compare their future results before adopting changes. |

## Advice versus enforcement

The docs deliberately make Take/Wait/Pass advisory. A Pass is therefore not an enforced trade block. A mocked grade C trade with a scheduled event and stale plan still reached execution. The server also does not itself enforce the UI's one-open-position-per-stock rule. If these are intended as limits, enforce them on the server, inside the entry lock, and check the selected structure rather than only the recommended one.

## Remaining analysis limits

| Area | What remains weak |
|---|---|
| Option ranking | Estimated scenarios still exclude brokerage, charges and extra execution slippage; future IV is held fixed. The paper journal charging costs does not fix the ranking. |
| Score | Market, weekly trend, relative strength and ADX can overlap. The weights are judgement; no new validation establishes that higher scores earn better net returns. |
| Learning | The same few trades can affect setup penalties, structure multipliers, several condition scores and global exit policy. This can amplify noise despite shrinkage in one layer. |
| Tracking | Live P&L uses last trades and omits charges; a stale leg price can weaken profit/loss alerts. Periodic executable bid/ask marks need freshness checks. |
| Monitoring | `_watch` silently swallows exceptions. Show the last successful pass, error, and data age; an alive thread alone does not prove monitoring works. |
| Profit evidence | The earlier replay's no-edge result has not been replaced by a reproducible positive study. More features and passing unit tests do not establish profitability. |

## Next steps

1. Persist trailing state and add server entry guards.
2. Make missing/stale data explicit and prevent Take on unavailable plans.
3. Enforce the chosen range definition and compare net option scenarios under IV and spread stress.
4. Keep adaptive policy changes advisory during a forward paper trial. Log every signal and skipped trade, freeze rule versions, and measure net expectancy, drawdown and uncertainty by setup and structure.
