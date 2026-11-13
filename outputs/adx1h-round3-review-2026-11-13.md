# ADX 1H third review — 13 November 2026

The earlier gaps have substantial fixes, but three reproducible issues still need attention. Review only: no application code, settings or real trades changed.

| Check | Result |
|---|---|
| `python -m pytest tests -q` | 386 passed, 1 failed, 22 subtests passed |
| Failure | Same `tests/test_entry_board.py:170` failure: `watch` instead of `ripe` |
| `node --check kronos/static/desk-trend.js` | Passed |
| Independent script | `python outputs/adx1h-round3-probes-2026-11-13.py` reproduced the three issues below |

## Findings

| Priority | Source | Finding | Required change |
|---|---|---|---|
| High | `kronos/adx_desk.py:680-688, 730-736` | The new breach latch treats the fallback last completed close as a live price. If entry occurs after that candle and live stock quotes fail, a pre-entry close beyond the stop creates a permanent breach. Probe: entry 100, stop 95, pre-entry close 94, no post-entry bars or live quote. Breach is saved with source `live price`; when the live price returns at 100, advice remains Exit. | Latch from a fresh post-entry observation or validated post-entry history only. Do not persist a confirmed breach from stale/unknown-time quotes or a pre-entry close. Keep an explicit unknown state. |
| High | `kronos/adx_desk.py:573-588`; `kronos/adx_brain.py:162-200` | Manual entry still evaluates `made['best']`, not the selected `idea`. Probe selects an event-sensitive credit spread while the recommended debit spread receives Take. The selected credit spread's real decision is Pass, but the fill boundary is reached without an override. | Evaluate the selected structure by supplying it as the decision candidate. The UI should show that structure's call and request/record any allowed override. |
| Medium | `kronos/adx_options.py:260-263`; `kronos/static/desk-trend.js:207, 217` | The new charges calculation overwrites `idea['cost']`, which previously meant premium paid/received and is still displayed as that amount. Probe: premium 3,710 becomes displayed cost 92.50 with costs enabled and zero with costs disabled. This also affects the daily desk. | Preserve premium in `cost`; store fees in a separate field such as `round_trip_cost`. Update the display labels and tests. |

The independent script uses mocked feeds and fill boundaries plus real planner/decision/management code. Its assertions document the defects and pass when they are reproduced; they are not a readiness test.

## Fixes verified in the current source and passing tests

| Earlier concern | Status |
|---|---|
| Missing Nifty data permitted | Now rejected by the entry gate |
| Partial scan did not retry | Coverage now feeds retry decisions, including missing benchmark |
| Missing option quote retained old P&L in market hours | Old mark is separated and current P&L cleared |
| Confirmed live stop could revert to Hold | Breach is now persisted and preserved against older payload updates; the false-latch case above needs fixing |
| Manual Pass/Wait silently ignored | Explicit recorded override now required for the recommended structure; alternate-structure gap remains |
| No cost/IV checks in ranking | Adds round-trip cost estimate, exit half-spread, +/-15% IV stress and minimum 1:1 scenario ratio |
| Actual fills not rechecked | A callback now rechecks directional target/stop outcomes before journal open |
| Open-trade count could race | Cap now checked inside the journal transaction |

## Remaining model limits

| Area | Limit |
|---|---|
| Final cost calculation | `fee(buy + sell, buy + sell, ...)` counts exit turnover on both sides after entry charges were already deducted. This is conservative but inconsistent with actual exit-side buy/sell charges. Planner also assumes fixed 20/order rather than journal preferences. |
| Minimum reward | Ratio floor exists, but no absolute minimum rupee gain. No explicit configured extra slippage allowance beyond depth fills and estimated exit half-spread. |
| Range positions | Actual-fill callback skips neutral structures, so range fills do not get the same final scenario validation. |
| Risk | Ten-position cap is not an aggregate rupee-risk or sector-exposure limit. |
| Validation | No live market-hours test or out-of-sample options profitability study was run. Exits remain manual. |

Verdict: materially improved, but fix the three confirmed issues before calling the system solid. Existing tests alone do not establish profitability or unattended readiness.
