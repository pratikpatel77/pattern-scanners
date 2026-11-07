# Strategy selection review

The saved LICHSGFIN analysis reproduced the screenshot: bearish price forecast, bullish OI; a put credit spread capped at Rs305.79 net estimated expiry profit against Rs9694.21 maximum loss, with a stressed average loss of Rs1214.67. Its base-case profitable path share was about 3%, and its worst-IV profitable share was 0%. A put debit spread also failed the existing reward and stressed-scenario floors.

Root cause: both interfaces deliberately filled the best-two slots with research or rejected plans when no eligible plans existed. Forecast direction was not an explicit selection gate. A small positive OI score could make the less suitable research structure rank above the bearish alternative. These were not valid trade recommendations.

Changes:
- A separate recommendation gate requires agreement with the forecast direction, no opposing or mixed OI, the existing net-profit and return-on-maximum-loss floors, the configured stressed profitable-scenario floor, and no unresolved quality warnings.
- Direction is computed from saved path closes, not the UI label: median move of at least 0.15% with at least 60% of closes agreeing for bullish/bearish; a near-flat median and balanced closes for range. These are heuristic rules, not calibrated likelihoods.
- Eligible EOD plans remain reviewable but cannot be opened through the generator as live trades. Missing OI/IVP/events and stale live prices prevent recommendation. Research records remain saved for audit.
- Both main and classic idea panels show at most two eligible distinct structures. No forced fallback. The exact saved example now returns zero recommendations and explains the disagreement and failed economics.
- The strike search now explores OTM shorts near spot, forecast bounds, halfway zones and the traded-expiry OI level; several hedge widths; and debit-spread targets near the model median and path bounds. Every candidate still passes the same payoff, stop, liquidity, IV stress and cost calculations. More strikes do not guarantee a valid setup.
- Selection is rechecked on saved-analysis reads, edited strikes and generator paper entry. Model scenario shares, payoff values and cost/slippage assumptions are not inflated or changed to make trades pass.

Validation: regression tests include the screenshot economics, both directions during OI disagreement, low reward despite high scenario share, missing/stale context, old selection flags, eligible aligned cases and wider strike coverage. Read-only replay: `strategy-selection-audit-2026-11-07.json`. The actual user journal and existing positions were not changed by the audit.

A low premium is not itself a calculation bug. For a put credit spread, maximum expiry gain is the premium retained, less costs; maximum loss is spread width times quantity minus that net gain. Moving the short strike nearer spot usually changes both premium and risk, so the engine must recompute the full plan. Reference: https://prd-web.optionseducation.org/strategies/all-strategies/bull-put-spread-credit-put-spread

The displayed scenario percentages are shares of model paths, not verified market win probabilities. Profitable recommendations cannot be guaranteed; walk-forward and paper validation remain necessary.

Browser validation passed for both recommendation panels: failed plans are not shown as loadable ideas, and the no-trade explanation remains visible. JavaScript maths/workflow and syntax checks passed.
