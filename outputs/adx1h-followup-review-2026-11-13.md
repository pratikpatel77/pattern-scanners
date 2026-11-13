# ADX 1H follow-up review — 13 November 2026

Verdict: improved, but several material gaps remain. Review only; application code and trading settings were not changed. Tests used synthetic inputs and temporary stores, not live broker orders.

| Validation | Result |
|---|---|
| `python -m pytest tests -q` | 375 passed, 1 failed, 22 subtests passed |
| Remaining suite failure | `tests/test_entry_board.py:170`: expected `ripe`, got `watch`; same failure as before |
| `node --check kronos/static/desk-trend.js` | Passed |
| New independent probes | Five remaining gaps reproduced; see `adx1h-followup-probes-2026-11-13.py` |

## What improved

| Earlier issue | Current status |
|---|---|
| Auto entry ignores Pass/Wait | Fixed for auto: `paper(auto=True)` now calls `_decide` and refuses anything except Take. |
| Armed manual entry | Blocked on the strict hourly desk. |
| Behind board auto entry | Blocked; entry also checks existing stock/benchmark timestamps and scan errors. Missing benchmark remains a gap below. |
| Expiry inside exit window | Hard entry floor now five days, with six preferred. Two-day entry is blocked. |
| ADX record saved after trade | Metadata now passed to the generic fill path and saved in the same journal open. |
| First 50 legs only | Quotes now requested in batches for all open legs. Failure handling remains incomplete. |
| Missed original-stop touch in a full post-entry bar | Historical lows/highs now checked against the original stop. Live/trailed stop persistence remains incomplete. |
| One-minute fallback accepted as an hour | A minimum minute count now rejects this case. The 50% threshold still accepts half-hours and counts duplicate minutes; it is not full integrity validation. |
| Current partial day used in daily volatility | Latest incomplete day is excluded in the normal multi-day history case. |
| Portfolio cap | Ten open trades now capped in the normal path. No aggregate money-risk cap; count check happens outside the final fill lock and can race across concurrent requests. |
| Entire board stale after successful old response | Behind board now triggers retry with backoff. Partial refresh remains incomplete below. |

## Remaining findings

| Priority | Source | Finding and evidence | Fix needed |
|---|---|---|---|
| High | `kronos/adx_options.py:123-151, 237-263`; `kronos/adx_desk.py:532-562` | Strike/structure rankings still omit fees and exit slippage, and assume the same future IV. Any positive score can qualify. Real depth fills are not fed back into a final net target/stop test. This earlier finding is unchanged. | Add cost and IV stress scenarios, a minimum net reward floor, and final checks using executable fills. |
| High | `kronos/adx_desk.py:539-542`; `kronos/static/desk-trend.js:194` | Only auto calls `_decide`. A manual Grade C trade still reaches the fill boundary while the real decision says Pass. UI trade buttons do not require Take. Manual selection can also bypass stale-chain/event decisions. | Share the final gate across manual and auto entry, using the selected structure. If manual override is intended, make it explicit and record the override. |
| High | `kronos/adx_desk.py:506-512` | Missing benchmark is accepted: `have is not None` skips the check when `_bench()` returns None. Independent probe removes Nifty history and still reaches the fill boundary. | Refuse entry if required stock, benchmark or saved-scan data are absent, not just old. |
| High | `kronos/adx_desk.py:619-648` | A live stop breach is not persisted. Probe feeds 94 with original stop 95, then 100 before any complete post-entry bar/mark exists: advice changes Exit to Hold. Original-stop replay does not cover the entry hour or historical trailing-stop breaches. | Persist the first confirmed exit breach and its time/reason. Use chronological replay for trailing levels and flag unknown ordering. |
| Medium | `kronos/adx_desk.py:578-591` | Missing option quotes still leave the old mark in `t['pnl']`, despite the comment saying no P&L is shown. Probe retains an old -700 mark and triggers the half-loss exit rule. A stale positive mark can likewise trigger profit advice. | Clear or explicitly flag old P&L when any leg lacks a current quote; do not use it as live risk evidence. |
| Medium | `kronos/adx_hour.py:230-231, 264-267`; `kronos/adx_desk.py:312-317` | Retry depends on the board's maximum data timestamp. If any stock is current, a stale/missing Nifty or another stock can remain behind without another fetch until the next hour. Probe shows coverage reports Nifty behind but `board()` starts no retry. | Make partial coverage and required benchmark freshness part of retry conditions; refresh coverage after failures too. |

The five synthetic probes test current behaviour, so their assertions pass when the listed defects exist. The previous audit script was written against the older fixture behaviour and should not be used as a current pass/fail suite without updating it.

No market-hours end-to-end run or options profitability validation was performed. Exits remain manual. The current evidence supports further supervised paper testing after the remaining execution checks are fixed, not unattended use or a claim of profitability.
