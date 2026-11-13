# Stock analysis

Everything in this file is computed from **daily bars** in the scanner cache. Code: `kronos/adx_core.py`.

## The indicator

| Item | Value | Why |
|---|---|---|
| +DMI / −DMI period | 13 | Schaap's setting; slightly more responsive than Wilder's 14 |
| ADX period | 8 | Makes ADX react faster to changes in trend strength |
| Smoothing | Wilder (alpha = 1/n) | The standard for ADX/DMI |
| True range | max(high−low, \|high−prev close\|, \|low−prev close\|) | Handles gaps |
| Directional movement | Only the larger of the up or down move counts; inside days count none | Per the book's appendix |
| Trend level | **25** | Under 25 = consolidation (breakout mode). Over 25 = trend (retracement mode) |
| Moving averages | 20 EMA, 5 and 10 SMA | The book trades price, ADX/DMI and the 20 EMA |
| ATR | 14 | Measures distances without eyeballing |
| Warm-up | 80 bars | No rule is judged earlier |
| Weekly | Built from daily bars, **using only the last completed week** | A daily bar never sees its own week's close |
| Market context | Nifty 50 DMI dominance, if its bars are cached | Shown as a feature, not required |

## DMI dominance (the key idea)

A DMI line is **dominant** only after it wins a fight, not merely by being on top:

- **Crossover high**: the line crosses above, rises over 25, and peaks higher than the other line's preceding peak.
- **Cross and hold**: it crosses, pulls back without crossing back, then makes a new high over 25.

Until one of these happens the previous dominant line still rules. This is the filter that removes most whipsaw crossovers (the book's own warning).

## The six trend setups

Each is detected for bullish and bearish; bearish runs the same code on a mirrored series. A setup has an **entry trigger** (high of the pivot bar), a **stop** (0.5 ATR beyond the pivot) and a **target** (2R from the entry). The entry-to-stop distance must be 0.3–3.5 ATR, otherwise the setup is not offered.

| Setup | Conditions (bullish form) | Idea |
|---|---|---|
| **ADXodus** | Low-ADX base of 30+ bars (ADX under 25), +DMI dominant, a close above the base high, then the **first pullback to the 20 EMA** within 20 bars | The longer a stock is quiet, the further it runs; enter the first retracement |
| **ADXplosive** | As ADXodus, but a 20+ bar base counts when the **weekly ADX is also low and the weekly DMI agrees** | Both timeframes store energy |
| **ADXquisite** | Weekly +ADX peak of 25+ in the last 12 weeks with weekly +DMI on top; a daily correction of 5+ bars with −DMI on top that reached the weekly 20 EMA zone; daily +DMI has just regained dominance (within 15 bars); pullback at the daily 20 EMA | Weekly trend intact; the daily correction looked like a reversal but was not |
| **ADXtender** | Established +DMI dominance for 5+ bars, a new 20-bar high with ADX above 25 and rising, a pullback to the 20 EMA, ADX turning down, no DMI cross, no bearish divergence at the last high | Add to a strong trend on its rests |
| **ADXpress** | +DMI crossover high within 15 bars, ADX over 15 and rising, a 20-bar high in the last 5 bars, a **2–5 bar tight pause** near the 5/10 SMA with +DMI staying over 25 | A fast breakout that will not retrace deeply |
| **ADXtra** | A 30+ bar low-ADX base where dominance changed **inside** the base, then a **daily close above** the base high with +DMI at a 10-bar high, price near the 20 EMA | Enter the breakout itself, with dominance already settled |

When two setups could apply to one bar, the first in this priority order wins: ADXquisite, ADXplosive, ADXodus, ADXtender, ADXpress, ADXtra.

### Quality checklist
Each setup lists its own rules as a checklist (visible in the tab). **Rules %** is the share that passed. Examples of optional-but-useful items: both DMI lines were above ADX before the breakout, the dominant DMI held near 25 on the pullback, weekly DMI agrees.

## The range view

If a stock has been under ADX 25 for **30+ bars** and has no dominant DMI, it gets one neutral **Range** row with the base's high and low. A quiet base looks the same from both directions, so it appears once, not twice. This is the evidence the desk needs before offering straddles, strangles, iron flies or iron condors.

## Verdicts

| Verdict | Meaning |
|---|---|
| **Enter** | The stock is through its trigger and within 0.5R of it |
| **Armed** | The setup is complete but price has not reached the trigger; enter only if it trades through it |
| **Range** | A quiet base, with price still inside it |
| **Watch** | Early stage (broke out but no pullback yet; power trend at new highs; dominance changed but no close beyond the base; or a range stock that has since left its base) |
| **Skipped** | Price is already beyond the stop, or more than 0.5R past the entry ("chasing") |

A live price decides when market is open; otherwise the last close is used and labelled "close".

## Ranking

Within a verdict, stocks are ordered by: share of rules passed, +10 if the weekly trend agrees, and −30 if your own closed trades in that setup have been losing (after 5 closed trades). Range rows are ordered by how long the base has lasted. This is an ordering, not a probability.

## What the tab shows per stock

Chart with price, 20 EMA, the entry / stop / target (or range high / low) and the ADX/DMI panel; trend description ("Power uptrend, ADX rising", "Quiet base: ADX under 25 for 68 bars"); ADX, +DMI, −DMI, who is dominant and how; weekly state; ATR; and the rule checklist.
