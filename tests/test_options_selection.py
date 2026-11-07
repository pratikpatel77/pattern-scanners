import unittest
from copy import deepcopy
from kronos.options_selection import select_plan, forecast_view
from kronos.options import OptionsDesk, preferences, classify
from kronos.options_math import candidates
from test_kronos_options import option


def forecast(price=980):
    return dict(last_close=1000,paths=[[dict(close=price)] for _ in range(32)])


def plan(bias='bearish'):
    return dict(calculation_version=2,name='Put debit spread',bias=bias,score=1,status='Paper candidate',reasons=[],
                stressed_mean_net=200,max_loss=1000,stressed_profit_pct=75,scenario_profit_pct=80)


class SelectionTests(unittest.TestCase):
    def test_screenshot_spread_cannot_be_recommended_even_with_high_oi_score(self):
        p=plan('bullish');p.update(name='Put credit spread',score=999,stressed_mean_net=-1215,
                                 max_loss=9694,max_profit=306,stressed_profit_pct=3)
        select_plan(p,forecast(),dict(available=True,bias='bullish'),preferences({}))
        self.assertFalse(p['recommended']);self.assertEqual(OptionsDesk.top_ideas([p]),[])
        self.assertIn('Price forecast and OI disagree; wait for alignment',p['selection_reasons'])
        self.assertIn('Stressed net profit below minimum',p['selection_reasons'])

    def test_economics_and_scenario_floors_both_required(self):
        for changes in [dict(stressed_mean_net=99),dict(max_loss=3000),dict(stressed_profit_pct=59),dict(stressed_profit_pct=float('nan'))]:
            p=plan();p.update(changes)
            select_plan(p,forecast(),dict(available=True,bias='bearish'),preferences({}))
            self.assertFalse(p['recommended'])

    def test_aligned_net_profitable_plan_qualifies_without_inflating_probability(self):
        p=plan();select_plan(p,forecast(),dict(available=True,bias='bearish'),preferences({}))
        self.assertTrue(p['recommended']);self.assertEqual(OptionsDesk.top_ideas([p]),[p])
        self.assertEqual(p['scenario_profit_pct'],80)

    def test_both_directions_rejected_when_forecast_and_oi_conflict(self):
        for bias in ('bullish','bearish','range'):
            p=plan(bias);select_plan(p,forecast(),dict(available=True,bias='bullish'),preferences({}))
            self.assertFalse(p['recommended']);self.assertEqual(classify(p['reasons']),'Skip')

    def test_saved_flags_cannot_hide_new_failures(self):
        p=plan();c=dict(available=True,bias='bearish')
        select_plan(p,forecast(),c,preferences({}));self.assertTrue(p['recommended'])
        p['stressed_mean_net']=-10
        select_plan(p,forecast(),c,preferences({}));self.assertFalse(p['recommended'])

    def test_missing_paths_and_mixed_oi_abstain(self):
        for f,c in [(dict(last_close=1000),dict(available=True,bias='neutral')),
                    (forecast(),dict(available=True,bias='mixed',mixed=True)),(forecast(),{})]:
            p=plan();select_plan(p,f,c,preferences({}));self.assertFalse(p['recommended'])

    def test_eod_is_reviewable_but_stale_live_prices_do_not_qualify(self):
        p=plan();p['reasons']=['EOD analysis: run a live forecast before paper entry']
        select_plan(p,forecast(),dict(available=True,bias='bearish'),preferences({}));self.assertTrue(p['recommended'])
        p['reasons']=['Underlying price is stale or market is closed']
        select_plan(p,forecast(),dict(available=True,bias='bearish'),preferences({}));self.assertFalse(p['recommended'])

    def test_wider_strike_search_includes_price_zone_and_oi_level(self):
        chain=[option(k,kind) for k in range(850,1151,10) for kind in ('CE','PE')]
        rows=candidates(chain,1000,900,1100,context={'by_expiry':{'2026-11-23':{'support':{'strike':960}}}},target=960)
        shorts={legs[0]['strike'] for name,_,legs in rows if name=='Put credit spread'}
        self.assertIn(960,shorts);self.assertIn(990,shorts);self.assertIn(900,shorts)
        debits=[legs for name,_,legs in rows if name=='Put debit spread']
        self.assertTrue(any(l[1]['strike']==960 for l in debits))
        self.assertTrue(all(len({leg['expiry'] for leg in legs})==1 for _,_,legs in rows))

if __name__=='__main__':unittest.main()
