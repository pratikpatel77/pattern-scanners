# Option selection

Given a setup, the desk answers: **which option position, which strikes, which expiry, how much risk, and where is the stop.** Code: `kronos/adx_options.py` (pricing and comparison) and `kronos/adx_desk.py` (expiry choice).

## Nine structures, all with a known worst case

**Naked short options are never offered.** A short call's loss is open-ended and a short put's is close to it, so every sold leg is paired with a bought wing.

### Trend setups (a DMI is dominant) — five choices

| Choice | Legs | When it tends to win |
|---|---|---|
| Buy ITM option | Buy a strike 2 in the money | Lower time-decay, higher cost |
| Buy ATM option | Buy the nearest strike | The balanced default |
| Buy OTM option | Buy a strike 2 out of the money | Cheap, needs a bigger move |
| Debit spread | Buy ATM, sell the strike nearest the stock **target** | Caps cost and gives up gains beyond the target |
| Credit spread | Sell the first strike beyond the stock **stop**, buy a wing one (or two) strikes further | The stock only has to stay on the right side of the stop; best when options are expensive |

Bullish uses calls (and a put credit spread); bearish mirrors it with puts (and a call credit spread).

### Range setups (no direction) — four choices

| Choice | Legs | Wins when |
|---|---|---|
| Long straddle | Buy ATM call and put | The stock moves a lot, either way |
| Long strangle | Buy a call and put one expected move away | The stock moves a lot, cheaper |
| Iron fly | Sell ATM call and put, buy wings one expected move away | The stock stays put |
| Iron condor | Sell calls and puts further out (at the open-interest walls when they are usable), buy wings 2 strikes beyond | The stock stays inside a wide band |

## How the choices are compared

Every candidate is priced from the live chain (**buys at the ask, sells at the bid**), then valued with Black-Scholes at today's implied volatility for each leg, a few days ahead:

- **Trend**: value at the stock's **target**, at its **stop**, and **flat** (unchanged), about **7 days** from now. Score = gain at target ÷ loss at stop. This asks the one question a trade plan has: does the position pay more if I am right than it costs if I am stopped out?
- **Range**: value at five stock prices from unchanged to twice an expected move up or down, weighted roughly as a normal distribution spreads them (38%, 23.5%, 23.5%, 7.5%, 7.5%), about **21 days** from now. Score = weighted result ÷ amount at risk. Five points, not three, because weighting only "exactly unchanged" flatters sold structures and buries bought ones.

The **expected move uses the stock's own realised (20-day) volatility**, not the implied volatility. That is the point: if the options imply more movement than the stock delivers, sold structures win the comparison; if they imply less, bought ones do. Using implied volatility for both would make buying and selling look identical.

The best score is recommended and every other choice gets a one-line reason it was not picked ("1.18 to 1 against 1.23 to 1; loses ₹5,759 if the stock goes nowhere; risks more").

### Adjustments to the score

| Situation | Effect | Reason |
|---|---|---|
| Implied ÷ realised volatility ≥ 1.3 (trend) | Bought options ×0.7, credit spread ×1.25 | Options are expensive; selling premium is favoured |
| Implied ÷ realised ≤ 1.0 (trend) | Bought ×1.15, debit ×1.1, credit ×0.8 | Options are cheap |
| A scheduled event (results, corporate action) falls inside the trade | Sold structures ×0.5–0.7, long volatility ×1.3 | A gap can go straight through a short strike |
| A leg has a wide bid-ask spread | Score ×0.6 plus a warning | Real fills will be worse than the estimate |
| Your own closed ADX trades of this type (≥5) | ×1.2 if they made money, ×0.7 if they lost | The only learning signal that is real option P&L |

Range setups get no volatility tilt: their figures are already measured against what the stock actually moves, so an over-priced option shows up as a loss in the numbers.

## Choosing the expiry

1. Estimate the **days the move needs** from the distance trigger → target in ATRs (assuming the stock nets well under one ATR a day), minimum 10 calendar days, maximum 60.
2. Start with the nearest expiry. **Skip it** if it has fewer days than needed or fewer than **6 tradable strikes** within 6 strikes of the money.
3. Move to the next expiry (up to two moves).
4. If the chosen expiry has **no position with a positive score**, try the next expiry once more before giving up.
5. If time is short and no later expiry exists, say so.

The reason for any move is shown ("Moved to the 2026-12-29 expiry because the 2026-11-23 expiry has 12 days and the move needs about 13").

## Liquidity rules

| Condition | Action |
|---|---|
| No two-sided quote (bid ≤ 0, ask < bid) | The leg is **never used** |
| Open interest under **100** | The leg is **never used** |
| Bid-ask spread over **12%** of mid | Allowed, with a warning and score ×0.6 |
| Fewer than 6 tradable strikes near the money | The expiry is skipped, or a "thin chain" warning is shown |
| No live order book (last price only) | Priced from the last price, labelled |

## What the option chain tells you

Shown per stock so the recommendation can be checked:

- Implied vs realised volatility and whether options look cheap, fair or expensive.
- **Open-interest walls**: the biggest call open interest above the stock (resistance) and put open interest below (support). A wall **before the target** is flagged ("the move may stall there"). Condor short strikes use the walls when they sit one to 2.5 expected moves out.
- Put-call ratio, max pain, skew (are downside options dearer?), the expected move, and how many strikes are tradable.

## Risk numbers you see

For the recommended position (one lot): what you pay or receive; the result at target, at stop and if the stock goes nowhere; the **worst case held to expiry**; the best case (or "open-ended"); and exact **breakevens** at expiry (found from the payoff's piecewise-linear shape). The result at the stop is the **planned loss**; the worst case is the absolute cap.

## The stop-loss and exit plan shown with every recommendation

- **Trend**: exit the whole position if the stock trades beyond the stop; book at target, or most of it at 80% of the way; exit if the opposite DMI takes dominance; exit after 21 days or 3 days before expiry.
- **Range**: a close outside the base ends the range (book a bought structure, exit a sold one); exit if half the amount at risk is lost; take profit at 50% of maximum or when ADX rises above 25; exit 3 days before expiry.

## What it deliberately does not do

- No naked shorts, no calendars or diagonals (their valuation across two expiries needs separate treatment).
- No claim that a recommended position will be profitable. The figures are model estimates for the stock's own plan, before brokerage and charges.
- No sizing across several trades or margin modelling: it shows one lot.
