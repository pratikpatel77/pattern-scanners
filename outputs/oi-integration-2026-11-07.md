# Options OI integration - 7 November 2026

## What changed

- Sensibull defaults were verified against public responses. The original strike request enabled only the front month; daily option/futures history used two months.
- Fetch the first two available monthly expiries separately, require the response to honour each selection, reconcile each full strike chain, then sum OI. Keep expiry-level records as well as combined totals. After front expiry rolls off, use the next available pair.
- PCR = sum(put OI) / sum(call OI), never average the two PCRs. Daily changes and previous PCR use the same expiry basket and previous strike OI. Missing previous data stays unknown.
- Separate v2 snapshot storage preserves legacy recordings without mixing one-month and two-month scopes. Exchange time, rather than collection time, defines windows. Require a full requested window, baseline within three minutes before the target, no recording gap above six minutes, same session, same expiries and same strike coverage.
- Stocks fetched are added to the recording watchlist up to 60 symbols. Keep the app running for intraday history. Existing recordings cannot create missing windows retrospectively.
- Validate nonnegative finite integer OI, positive strike/spot, totals, daily changes, expiry selection, exchange date/time, alignment between expiries and spot agreement.

## How it affects ideas

- Require price and daily combined PCR changes to agree before using directional OI context. Thresholds (0.15% price, 0.02 PCR) are disclosed heuristics, not statistically calibrated signals.
- Recorded 30/60 minute windows form one confirmation. Opposing windows or monthly indications cause a mixed reading. Shorter/longer windows remain visible.
- Daily futures price/OI changes are recomputed from sorted, same-basket rows. Daily futures data is a directional check only for matching-date EOD analysis; never label it live confirmation.
- Recent same-basket daily options history can reduce the influence when it disagrees. Never compare across expiry transitions or count overlapping observations as independent proof.
- Show front-month OI change, next-month change, combined change and next-month share. Front OI falling while next rises is possible rollover, not a measured trader rollover rate or a new directional signal.
- Traded-expiry OI levels within 5% of spot affect ranking of credit-spread/condor strikes. Do not substitute combined levels for the traded expiry; only strengthening levels earn a placement bonus.
- OI adjusts the existing score by at most +/-0.08. These weights are provisional. OI never modifies payoff, model scenario shares, IV stress, costs, slippage, liquidity requirements or reward floors.
- Missing/stale OI, mixed readings and directional conflict remain visible as research warnings. Refresh OI on strike edits and standard generator paper-entry checks. EOD ideas still cannot enter as live generator paper trades. The separate custom-leg desk remains a manual paper workflow.
- Save the OI evidence, timestamps, expiry scopes and score contribution with each generated idea. Old saved ideas are marked for re-analysis.

## Validation

- Regression tests cover two-month arithmetic, rollover, malformed data, stale/future timestamps, mismatched scopes, missing recordings, daily-history boundaries, bounded ranking and unchanged payoff/probability values.
- Read-only live checks of SBIN and MPHASIS saved in `oi-live-audit-2026-11-07.json`; both used 23 November and 29 December expiries with the 6 November closing snapshot. No paper or broker orders were placed by the live audit.
- Browser test uses synthetic data with the real server/API and page, in a temporary database.

## Limits

This is a defensively checked research feature, not a proven trading edge or an institutional-grade certification. OI includes both buyers and sellers; it cannot identify option writers, hedges, trader intent or actual rollover transactions. Public-source schema/access may change. Forward validation with execution costs is still required. Intraday recording quality must also be observed during an open market session.

Reference: [Zerodha rollover explanation](https://support.zerodha.com/category/trading-and-markets/trading-faqs/f-otrading/articles/what-does-rollover-mean-and-what-are-the-charges-involved).

Validation results: full Python suite passed (255 tests before the final extra calendar-month check); the final OI suite passed with 18 tests. The 47 options tests, 6 feed tests, both JavaScript test files, JavaScript syntax checks and isolated browser check passed. Browser screenshot: `oi-browser-2026-11-07.png`.
