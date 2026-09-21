"""Rule-based read of VOLUME + DELIVERY + PRICE ACTION across a detected pattern.

The scanners decide *what* the pattern is. This module decides whether the
pattern is being **accumulated** (bought by people taking shares home) or
**distributed** (sold into), by comparing three things over the exact bars of
the pattern, the stretch before it formed, and the latest bars:

  volume    -- how much stock changed hands, and on which days
  delivery  -- how much of that volume was actually taken into demat
               (NSE publishes it daily; the rest is intraday churn)
  price     -- where the stock closed relative to the pattern and the pivot

Everything is deterministic arithmetic -- no model, no network, instant.

Output per signal:
  verdict   short label, e.g. "Accumulation"
  pressure  -100..+100 score behind the label
  headline  one short line for the table cell
  chips     2 key numbers for the table cell
  sections  [{title, bullets:[{text, tone}]}] for the popup
"""
from __future__ import annotations

import numpy as np
import pandas as pd

UP, DOWN = 1, -1
GOOD, BAD, FLAT = "good", "bad", "flat"

VERDICTS = [
    (45, "Strong accumulation"),
    (20, "Accumulation"),
    (-20, "Mixed"),
    (-45, "Caution"),
    (-101, "Distribution"),
]


# ---------------------------------------------------------------- formatting
def qty(x) -> str:
    """Indian share counts: 1.2 Cr / 3.4 L / 8,200."""
    if x is None or not np.isfinite(x):
        return "n/a"
    if x >= 1e7:
        return f"{x / 1e7:.1f} Cr"
    if x >= 1e5:
        return f"{x / 1e5:.1f} L"
    return f"{x:,.0f}"


def pct(x, nd=0) -> str:
    return "n/a" if x is None or not np.isfinite(x) else f"{x:.{nd}f}%"


def sign(x, nd=0) -> str:
    return "n/a" if x is None or not np.isfinite(x) else f"{x:+.{nd}f}"


def points(x, nd=0) -> str:
    """+8 points / +1 point / -3 points."""
    if x is None or not np.isfinite(x):
        return "n/a"
    word = "point" if abs(round(x, nd)) == 1 else "points"
    return f"{x:+.{nd}f} {word}"


def mult(x, nd=1) -> str:
    return "n/a" if x is None or not np.isfinite(x) else f"{x:.{nd}f}x"


def _mean(a):
    a = np.asarray(a, float)
    a = a[np.isfinite(a)]
    return float(a.mean()) if a.size else np.nan


def _sum(a):
    a = np.asarray(a, float)
    a = a[np.isfinite(a)]
    return float(a.sum()) if a.size else np.nan


def _ratio(a, b):
    return float(a / b) if np.isfinite(a) and np.isfinite(b) and b > 0 else np.nan


# ---------------------------------------------------------------- the metrics
def measure(sig, bars: pd.DataFrame) -> dict:
    """All the raw numbers the rules below reason about."""
    n = len(bars)
    z = sig.zones or {}
    # clamp every index into this frame: zones come from the scanner that ran on
    # it, but a mismatch must degrade the read, never raise
    start = min(max(0, int(z.get("start", max(0, n - 40)))), max(0, n - 2))
    lead_start = min(max(0, int(z.get("lead_start", max(0, start - (n - start))))), start)
    recent_len = max(1, min(int(z.get("recent", 5)), n - start))
    handle_start = z.get("handle_start")
    body_end = min(int(handle_start), n) if handle_start else max(start + 1, n - recent_len)

    o = bars["open"].to_numpy(float)
    h = bars["high"].to_numpy(float)
    lo = bars["low"].to_numpy(float)
    c = bars["close"].to_numpy(float)
    v = bars["volume"].to_numpy(float)
    dq = bars["deliv_qty"].to_numpy(float) if "deliv_qty" in bars else np.full(n, np.nan)
    dp = bars["deliv_pct"].to_numpy(float) if "deliv_pct" in bars else np.full(n, np.nan)

    pat = slice(start, n)          # whole pattern, including the handle / latest bars
    body = slice(start, body_end)  # the base / cup / box itself
    lead = slice(lead_start, start)
    rec = slice(max(start, n - recent_len), n)

    m = {}
    m["bars_pattern"] = n - start
    m["bars_recent"] = n - max(start, n - recent_len)
    m["pattern_name"] = z.get("pattern_name", "pattern")
    m["has_delivery"] = bool(np.isfinite(dp[pat]).sum() >= max(3, (n - start) // 4))

    # ---- direction of each bar (close vs previous close)
    dirn = np.zeros(n)
    dirn[1:] = np.sign(c[1:] - c[:-1])

    # ---- volume
    m["vol_pattern"] = _mean(v[body])
    m["vol_lead"] = _mean(v[lead])
    m["vol_recent"] = _mean(v[rec])
    m["vol_vs_lead"] = _ratio(m["vol_pattern"], m["vol_lead"])
    m["vol_recent_vs_pattern"] = _ratio(m["vol_recent"], m["vol_pattern"])

    up_mask = (dirn > 0)[pat]
    dn_mask = (dirn < 0)[pat]
    vp = v[pat]
    m["up_bars"], m["down_bars"] = int(up_mask.sum()), int(dn_mask.sum())
    m["ud_vol"] = _ratio(_sum(vp[up_mask]), _sum(vp[dn_mask]))

    # volume at the lows vs at the highs of the pattern
    cp = c[pat]
    p_low, p_high = float(np.nanmin(lo[pat])), float(np.nanmax(h[pat]))
    span = p_high - p_low
    m["pattern_low"], m["pattern_high"] = p_low, p_high
    if span > 0:
        third = span / 3
        m["vol_at_lows"] = _mean(vp[cp <= p_low + third])
        m["vol_at_highs"] = _mean(vp[cp >= p_high - third])
        m["lows_vs_highs_vol"] = _ratio(m["vol_at_lows"], m["vol_at_highs"])
        m["pos_in_range"] = float((c[-1] - p_low) / span)
    else:
        m["vol_at_lows"] = m["vol_at_highs"] = m["lows_vs_highs_vol"] = np.nan
        m["pos_in_range"] = np.nan

    # heavy-volume bars inside the pattern
    heavy = vp >= 2 * m["vol_pattern"] if np.isfinite(m["vol_pattern"]) else np.zeros(len(vp), bool)
    m["heavy_up"] = int((heavy & up_mask).sum())
    m["heavy_down"] = int((heavy & dn_mask).sum())

    # ---- delivery
    if m["has_delivery"]:
        m["deliv_base"] = float(np.nanmedian(dp[np.isfinite(dp)]))
        m["deliv_pattern"] = _mean(dp[body])
        m["deliv_recent"] = _mean(dp[rec])
        m["deliv_up"] = _mean(dp[pat][up_mask])
        m["deliv_down"] = _mean(dp[pat][dn_mask])
        m["deliv_vs_base"] = m["deliv_pattern"] - m["deliv_base"]
        m["deliv_recent_gap"] = m["deliv_recent"] - m["deliv_pattern"]
        m["deliv_ud_gap"] = m["deliv_up"] - m["deliv_down"]
        m["deliv_qty_pattern"] = _mean(dq[body])
        m["deliv_qty_recent"] = _mean(dq[rec])
        m["deliv_qty_recent_ratio"] = _ratio(m["deliv_qty_recent"], m["deliv_qty_pattern"])
        # conviction days: heavy volume AND delivery well above the stock's norm
        dpp = dp[pat]
        big = np.isfinite(dpp) & (vp >= 1.5 * m["vol_pattern"]) & (dpp >= m["deliv_base"] + 5)
        m["conviction_up"] = int((big & up_mask).sum())
        m["conviction_down"] = int((big & dn_mask).sum())
        # churn days: heavy volume but hardly anything delivered
        m["churn_days"] = int((np.isfinite(dpp) & (vp >= 2 * m["vol_pattern"]) & (dpp <= m["deliv_base"] - 5)).sum())
        m["deliv_latest"] = float(dp[-1]) if np.isfinite(dp[-1]) else np.nan
        m["deliv_latest_gap"] = m["deliv_latest"] - m["deliv_base"] if np.isfinite(dp[-1]) else np.nan
    else:
        for k in ("deliv_base", "deliv_pattern", "deliv_recent", "deliv_up", "deliv_down", "deliv_vs_base",
                  "deliv_recent_gap", "deliv_ud_gap", "deliv_qty_pattern", "deliv_qty_recent",
                  "deliv_qty_recent_ratio", "deliv_latest", "deliv_latest_gap"):
            m[k] = np.nan
        m["conviction_up"] = m["conviction_down"] = m["churn_days"] = 0

    # ---- price action
    m["vol_latest"] = float(v[-1])
    ref = _mean(v[max(0, n - 21):-1])
    m["vol_latest_vs_avg"] = _ratio(m["vol_latest"], ref)
    rng = h[-1] - lo[-1]
    m["close_strength"] = float((c[-1] - lo[-1]) / rng) if rng > 0 else np.nan
    m["bar_change_pct"] = float((c[-1] / c[-2] - 1) * 100) if n > 1 and c[-2] else np.nan
    m["recent_tightness"] = float((np.nanmax(h[rec]) - np.nanmin(lo[rec])) / c[-1] * 100) if c[-1] else np.nan

    third = max(1, (n - start) // 3)
    m["higher_lows"] = bool(np.nanmin(lo[n - third:]) > np.nanmin(lo[start:start + third]))

    # tests of the pivot and whether volume grew on them
    near = np.isfinite(h[pat]) & (h[pat] >= sig.pivot * 0.985)
    m["pivot_tests"] = int(near.sum())
    m["vol_on_tests_vs_pattern"] = _ratio(_mean(vp[near]), m["vol_pattern"]) if near.any() else np.nan

    # how much of the pattern volume sits ABOVE the current price (overhead supply)
    above = cp > c[-1]
    m["overhead_supply"] = _ratio(_sum(vp[above]), _sum(vp))

    # what a convincing breakout needs
    m["breakout_vol"] = m["vol_pattern"] * 1.5 if np.isfinite(m["vol_pattern"]) else np.nan
    m["breakout_deliv"] = max(m["deliv_base"], m["deliv_pattern"]) if m["has_delivery"] else np.nan
    return m


# ---------------------------------------------------------------- the rules
def _band(x, cuts, scores):
    """cuts descending; returns the score for the first cut x clears."""
    if not np.isfinite(x):
        return 0
    for cut, s in zip(cuts, scores):
        if x >= cut:
            return s
    return scores[-1]


def score(m: dict) -> tuple[float, list]:
    """Weighted pressure score plus the reasons that moved it."""
    pts, why = 0.0, []

    def add(p, reason, tone):
        nonlocal pts
        if p:
            pts += p
            why.append((p, reason, tone))

    # buying vs selling effort
    add(_band(m["ud_vol"], [1.5, 1.2, 0.8, 0.6], [18, 12, 0, -12, -18]),
        "volume favours buyers" if (m["ud_vol"] or 0) > 1.2 else "volume favours sellers",
        GOOD if (m["ud_vol"] or 0) > 1.2 else BAD)

    if m["has_delivery"]:
        add(_band(m["deliv_ud_gap"], [8, 3, -3, -8], [18, 12, 0, -12, -18]),
            "delivery higher on up days" if (m["deliv_ud_gap"] or 0) > 3 else "delivery higher on down days",
            GOOD if (m["deliv_ud_gap"] or 0) > 3 else BAD)
        add(_band(m["deliv_vs_base"], [8, 3, -3, -8], [14, 9, 0, -9, -14]),
            "delivery above its normal level" if (m["deliv_vs_base"] or 0) > 3 else "delivery below normal",
            GOOD if (m["deliv_vs_base"] or 0) > 3 else BAD)
        add(_band(m["deliv_recent_gap"], [5, 2, -2, -5], [12, 7, 0, -7, -12]),
            "delivery rising lately" if (m["deliv_recent_gap"] or 0) > 2 else "delivery fading lately",
            GOOD if (m["deliv_recent_gap"] or 0) > 2 else BAD)
        conv = m["conviction_up"] - m["conviction_down"]
        add(max(-10, min(10, conv * 4)),
            "big-money days were buying" if conv > 0 else "big-money days were selling",
            GOOD if conv > 0 else BAD)
        if m["churn_days"] >= 2:
            add(-8, "heavy days with little delivery (churn)", BAD)

    # volume contraction through the pattern is healthy for a base
    v_lead = m["vol_vs_lead"]
    add(_band(v_lead, [1.5, 0.9, 0.75, 0], [-8, 0, 5, 10]) if np.isfinite(v_lead) else 0,
        "quieter than before the pattern" if (v_lead or 9) < 0.9 else "busier than before the pattern",
        GOOD if (v_lead or 9) < 0.9 else BAD)

    vr = m["vol_recent_vs_pattern"]
    add(_band(vr, [1.5, 0.9, 0.7, 0], [-6, 0, 5, 10]) if np.isfinite(vr) else 0,
        "volume drying up near the pivot" if (vr or 9) < 0.9 else "volume picking up near the pivot",
        GOOD if (vr or 9) < 0.9 else FLAT)

    lh = m["lows_vs_highs_vol"]
    add(_band(lh, [1.3, 0.8, 0], [-8, 0, 8]) if np.isfinite(lh) else 0,
        "quiet at the lows, active at the highs" if (lh or 9) < 0.8 else "heavy trade at the lows",
        GOOD if (lh or 9) < 0.8 else BAD)

    if m["higher_lows"]:
        add(5, "each dip is holding higher", GOOD)
    if np.isfinite(m["overhead_supply"]) and m["overhead_supply"] > 0.5:
        add(-6, "plenty of stock bought above current price", BAD)

    # the latest bar
    surge = np.isfinite(m["vol_latest_vs_avg"]) and m["vol_latest_vs_avg"] >= 1.5
    strong = np.isfinite(m["close_strength"]) and m["close_strength"] >= 0.65
    weak = np.isfinite(m["close_strength"]) and m["close_strength"] <= 0.35
    good_del = np.isfinite(m["deliv_latest_gap"]) and m["deliv_latest_gap"] > 0
    if surge and strong and (good_del or not m["has_delivery"]):
        add(8, "latest bar: heavy volume, closed strong", GOOD)
    elif surge and weak:
        add(-8, "latest bar: heavy volume, closed weak", BAD)

    pts = max(-100.0, min(100.0, pts))
    why.sort(key=lambda t: -abs(t[0]))
    return pts, why


def verdict_for(p: float) -> str:
    for cut, label in VERDICTS:
        if p >= cut:
            return label
    return VERDICTS[-1][1]


# ---------------------------------------------------------------- the words
def _bullets(sig, m: dict) -> list:
    tf_word = "week" if sig.tf == "W" else "day"
    tf_plural = "weeks" if sig.tf == "W" else "days"
    name = m["pattern_name"]
    sections = []

    # --- volume
    b = []
    if np.isfinite(m["vol_vs_lead"]):
        r = m["vol_vs_lead"]
        if r < 0.9:
            b.append((f"Trading inside the {name} is {(1 - r) * 100:.0f}% lighter than the move before it "
                      f"({qty(m['vol_pattern'])} vs {qty(m['vol_lead'])} a {tf_word}) - sellers are not pressing.", GOOD))
        elif r > 1.3:
            b.append((f"Trading inside the {name} is {(r - 1) * 100:.0f}% heavier than before it "
                      f"({qty(m['vol_pattern'])} a {tf_word}) - the stock is changing hands fast here.",
                      FLAT if (m["ud_vol"] or 0) > 1.2 else BAD))
        else:
            b.append((f"Volume in the {name} is about the same as before it ({qty(m['vol_pattern'])} a {tf_word}).", FLAT))
    if np.isfinite(m["ud_vol"]):
        r = m["ud_vol"]
        if r >= 1.2:
            b.append((f"Up {tf_plural} carry {mult(r)} the volume of down {tf_plural} "
                      f"({m['up_bars']} up vs {m['down_bars']} down) - buyers are the stronger side.", GOOD))
        elif r <= 0.85:
            b.append((f"Down {tf_plural} carry more volume than up {tf_plural} ({mult(r)}) - sellers are the stronger side.", BAD))
        else:
            b.append((f"Up and down {tf_plural} carry similar volume ({mult(r)}) - no clear winner yet.", FLAT))
    if np.isfinite(m["vol_recent_vs_pattern"]):
        r = m["vol_recent_vs_pattern"]
        last = f"last {m['bars_recent']} {tf_plural if m['bars_recent'] > 1 else tf_word}"
        if r <= 0.8:
            b.append((f"In the {last} volume dropped to {mult(r)} the {name} average - supply has dried up right under the pivot.", GOOD))
        elif r >= 1.4:
            b.append((f"In the {last} volume rose to {mult(r)} the {name} average - the stock is waking up.",
                      GOOD if (m["close_strength"] or 0) > 0.6 else FLAT))
    if np.isfinite(m["lows_vs_highs_vol"]) and m["lows_vs_highs_vol"] <= 0.8:
        b.append((f"Volume at the lows of the {name} is only {mult(m['lows_vs_highs_vol'])} of what trades near the highs - "
                  "selling exhausts at the bottom.", GOOD))
    elif np.isfinite(m["lows_vs_highs_vol"]) and m["lows_vs_highs_vol"] >= 1.3:
        b.append((f"Volume is {mult(m['lows_vs_highs_vol'])} heavier at the lows than the highs - dips are being sold hard.", BAD))
    if m["heavy_up"] or m["heavy_down"]:
        b.append((f"Bursts of very heavy trade: {m['heavy_up']} on up {tf_plural}, {m['heavy_down']} on down {tf_plural}.",
                  GOOD if m["heavy_up"] > m["heavy_down"] else (BAD if m["heavy_down"] > m["heavy_up"] else FLAT)))
    sections.append({"title": "Volume", "bullets": b})

    # --- delivery
    b = []
    if not m["has_delivery"]:
        b.append(("No NSE delivery data for this symbol over the pattern - the read below is volume and price only.", FLAT))
    else:
        gap = m["deliv_vs_base"]
        how = "right at its usual level" if abs(gap) < 1 else f"against its usual {pct(m['deliv_base'])} ({points(gap)})"
        b.append((f"Delivery averages {pct(m['deliv_pattern'])} of volume in the {name}, {how}.",
                  GOOD if m["deliv_vs_base"] > 3 else (BAD if m["deliv_vs_base"] < -3 else FLAT)))
        if np.isfinite(m["deliv_ud_gap"]):
            g = m["deliv_ud_gap"]
            if g >= 3:
                b.append((f"On up {tf_plural} {pct(m['deliv_up'])} of volume is taken home, on down {tf_plural} only "
                          f"{pct(m['deliv_down'])} - buyers are holding, sellers are just trading.", GOOD))
            elif g <= -3:
                b.append((f"Down {tf_plural} show higher delivery ({pct(m['deliv_down'])}) than up {tf_plural} "
                          f"({pct(m['deliv_up'])}) - real holders are letting stock go.", BAD))
            else:
                b.append((f"Delivery is similar on up and down {tf_plural} ({pct(m['deliv_up'])} vs {pct(m['deliv_down'])}).", FLAT))
        if np.isfinite(m["deliv_recent_gap"]):
            g = m["deliv_recent_gap"]
            if g >= 2:
                b.append((f"Lately delivery has climbed to {pct(m['deliv_recent'])} ({points(g)}) - buying is getting more serious "
                          "as price nears the pivot.", GOOD))
            elif g <= -2:
                b.append((f"Lately delivery has slipped to {pct(m['deliv_recent'])} ({points(g)}) - the recent move is lighter in quality.", BAD))
        if np.isfinite(m["deliv_qty_recent_ratio"]) and m["deliv_qty_recent_ratio"] >= 1.2:
            b.append((f"Shares actually delivered are up to {qty(m['deliv_qty_recent'])} a {tf_word} "
                      f"({mult(m['deliv_qty_recent_ratio'])} the {name} pace) - more stock is being absorbed.", GOOD))
        if m["conviction_up"] or m["conviction_down"]:
            b.append((f"High-conviction {tf_plural} (big volume AND delivery above normal): "
                      f"{m['conviction_up']} on the way up, {m['conviction_down']} on the way down.",
                      GOOD if m["conviction_up"] > m["conviction_down"] else (BAD if m["conviction_down"] > m["conviction_up"] else FLAT)))
        if m["churn_days"] >= 2:
            b.append((f"{m['churn_days']} heavy {tf_plural} had unusually low delivery - that is churn or operator activity, not investment.", BAD))
    sections.append({"title": "Delivery", "bullets": b})

    # --- price action
    b = []
    if np.isfinite(m["pos_in_range"]):
        where = ("near the top of" if m["pos_in_range"] >= 0.8 else
                 "in the upper half of" if m["pos_in_range"] >= 0.55 else
                 "around the middle of" if m["pos_in_range"] >= 0.4 else "in the lower half of")
        b.append((f"Price is {where} the {name} range ({m['pattern_low']:,.0f} to {m['pattern_high']:,.0f}), "
                  f"{sig.distance_pct:.1f}% under the pivot.",
                  GOOD if m["pos_in_range"] >= 0.55 else FLAT))
    if m["higher_lows"]:
        b.append(("Each pullback is bottoming higher than the last - buyers step in earlier every time.", GOOD))
    if m["pivot_tests"] >= 2:
        tone = GOOD if (m["vol_on_tests_vs_pattern"] or 9) < 1.2 else FLAT
        b.append((f"The pivot has been tested {m['pivot_tests']} times - repeated tests usually thin out the sellers sitting there.", tone))
    if np.isfinite(m["recent_tightness"]):
        if m["recent_tightness"] <= 6:
            b.append((f"The last {m['bars_recent']} {tf_plural if m['bars_recent'] > 1 else tf_word} have held a tight "
                      f"{m['recent_tightness']:.1f}% range - coiled, ready to move.", GOOD))
        elif m["recent_tightness"] >= 15:
            b.append((f"The last {m['bars_recent']} {tf_plural} swung {m['recent_tightness']:.1f}% - still choppy, not settled.", FLAT))
    if np.isfinite(m["overhead_supply"]) and m["overhead_supply"] > 0.5:
        b.append((f"{m['overhead_supply'] * 100:.0f}% of the {name} volume was bought above today's price - "
                  "some of those holders sell into a rally.", BAD))
    sections.append({"title": "Price action", "bullets": b})

    # --- right now
    b = []
    chg = m["bar_change_pct"]
    vs = m["vol_latest_vs_avg"]
    parts = []
    if np.isfinite(chg):
        parts.append(f"{'up' if chg >= 0 else 'down'} {abs(chg):.1f}%")
    if np.isfinite(vs):
        parts.append(f"volume {mult(vs)} the recent average")
    if np.isfinite(m["close_strength"]):
        where = "near the high" if m["close_strength"] >= 0.65 else ("near the low" if m["close_strength"] <= 0.35 else "mid-range")
        parts.append(f"closed {where} of the {tf_word}")
    if parts:
        tone = GOOD if (np.isfinite(m["close_strength"]) and m["close_strength"] >= 0.65 and (vs or 0) >= 1) else (
            BAD if (np.isfinite(m["close_strength"]) and m["close_strength"] <= 0.35 and (vs or 0) >= 1.5) else FLAT)
        b.append((f"Latest {tf_word}: " + ", ".join(parts) + ".", tone))
    if m["has_delivery"] and np.isfinite(m["deliv_latest"]):
        g = m["deliv_latest_gap"]
        how = "in line with normal" if abs(g) < 1 else f"{points(g)} vs normal"
        b.append((f"Delivery on the latest {tf_word} was {pct(m['deliv_latest'])}, {how}.",
                  GOOD if g > 3 else (BAD if g < -5 else FLAT)))
    sections.append({"title": f"Latest {tf_word}", "bullets": b})

    # --- what would confirm it
    b = []
    need_v = m["breakout_vol"]
    line = f"A breakout above {sig.pivot:,.2f} is believable on volume above {qty(need_v)} for the {tf_word} (1.5x the {name} average)"
    if m["has_delivery"] and np.isfinite(m["breakout_deliv"]):
        line += f", with delivery holding above {pct(m['breakout_deliv'])}"
    b.append((line + ".", FLAT))
    b.append((f"It fails if price closes back under {sig.support:,.2f} "
              f"({sig.risk_pct:.1f}% below here) - that breaks the {name}.", FLAT))
    if m["has_delivery"] and m["deliv_ud_gap"] < -3:
        b.append(("Weak point: the selling side is the one taking delivery. Wait for that to flip before trusting a breakout.", BAD))
    if np.isfinite(m["vol_recent_vs_pattern"]) and m["vol_recent_vs_pattern"] >= 1.4 and (m["close_strength"] or 1) < 0.4:
        b.append(("Weak point: volume is rising while closes stay soft - that combination often precedes a breakdown, not a breakout.", BAD))
    sections.append({"title": "What to watch", "bullets": b})

    return [s for s in sections if s["bullets"]]


def _headline(sig, m: dict, pressure: float, why: list) -> str:
    top = why[0][1] if why else "no clear pressure either way"
    name = m["pattern_name"]
    if pressure >= 45:
        lead = f"Money is going into this {name}"
    elif pressure >= 20:
        lead = f"Mild but real buying in this {name}"
    elif pressure > -20:
        lead = f"Buyers and sellers are evenly matched in this {name}"
    elif pressure > -45:
        lead = f"Supply is the problem in this {name}"
    else:
        lead = f"This {name} is being sold into"
    return f"{lead} - {top}."


def analyse(sig, bars: pd.DataFrame) -> dict:
    """Attach the full read to one signal. `bars` are the signal's own timeframe."""
    m = measure(sig, bars)
    pressure, why = score(m)
    verdict = verdict_for(pressure)

    chips = []
    if m["has_delivery"]:
        chips.append(f"Deliv {pct(m['deliv_pattern'])} ({sign(m['deliv_vs_base'])})")
    if np.isfinite(m["ud_vol"]):
        chips.append(f"Up/Dn vol {mult(m['ud_vol'])}")
    if not chips:
        chips.append("volume only")

    return {
        "verdict": verdict,
        "pressure": round(pressure),
        "headline": _headline(sig, m, pressure, why),
        "chips": chips,
        "sections": [{"title": s["title"], "bullets": [{"text": t, "tone": tone} for t, tone in s["bullets"]]}
                     for s in _bullets(sig, m)],
        "has_delivery": m["has_delivery"],
        "metrics": {k: (None if isinstance(v, float) and not np.isfinite(v) else
                        (round(v, 2) if isinstance(v, float) else v))
                    for k, v in m.items() if not isinstance(v, str)},
    }
