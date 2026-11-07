"""Recommendation eligibility is separate from research ranking. Never force two ideas."""
import math
import statistics
from .options_math import reward_reasons

VERSION = 1
DIRECTION_REASONS = ('Strategy direction conflicts with the price forecast',
                     'Price forecast and OI disagree; wait for alignment',
                     'Price forecast has no clear directional or range view')
REVIEW_ONLY = ('Chain preview: refresh leg depth before entry',
               'EOD analysis: run a live forecast before paper entry')


def forecast_view(forecast):
    spot = forecast.get('last_close')
    paths = forecast.get('paths') or []
    if not isinstance(spot, (int, float)) or spot <= 0 or not paths:
        return 'unknown'
    closes = [p[-1].get('close') for p in paths if p]
    if len(closes) != len(paths) or any(not isinstance(x,(int,float)) or not math.isfinite(x) for x in closes):
        return 'unknown'
    move = (statistics.median(closes)/spot-1)*100
    upside = sum(x > spot for x in closes)/len(closes)
    if move >= .15 and upside >= .6: return 'bullish'
    if move <= -.15 and upside <= .4: return 'bearish'
    if abs(move) < .15 and .4 <= upside <= .6: return 'range'
    return 'unknown'


def select_plan(plan, forecast, context, prefs):
    view = forecast_view(forecast)
    reasons = [r for r in plan.get('reasons',[]) if r not in DIRECTION_REASONS]
    if view == 'unknown': reasons.append(DIRECTION_REASONS[2])
    elif plan.get('bias') != view: reasons.append(DIRECTION_REASONS[0])
    oi = context.get('bias') if context.get('available') else None
    if oi in ('bullish','bearish') and (view == 'range' or view in ('bullish','bearish') and oi != view):
        reasons.append(DIRECTION_REASONS[1])
    plan['reasons'] = list(dict.fromkeys(reasons))
    failures = [r for r in reasons if r not in REVIEW_ONLY]
    if plan.get('calculation_version') != 2:
        failures.append('Saved calculation is outdated. Analyse this stock again')
    failures.extend(reward_reasons(plan,prefs))
    pct = plan.get('stressed_profit_pct')
    if not isinstance(pct,(int,float)) or not math.isfinite(pct) or pct < prefs['min_scenario_pct']:
        failures.append('Too few profitable model scenarios')
    if not context.get('available'):
        failures.append('OI context unavailable or stale')
    elif context.get('mixed'):
        failures.append('OI evidence is mixed across expiries or time windows')
    failures = list(dict.fromkeys(failures))
    plan.update(selection_version=VERSION, recommended=not failures, forecast_bias=view,
                selection_reasons=failures)
    return plan


def no_trade_reason(plans):
    reasons = list(dict.fromkeys(r for p in plans for r in p.get('selection_reasons',[])))
    priority = [r for r in DIRECTION_REASONS if r in reasons]
    rest = [r for r in reasons if r not in DIRECTION_REASONS]
    return 'No worthwhile trade. ' + ('; '.join((priority+rest)[:4]) if reasons else 'No plan passed all checks. Run a fresh analysis.')
