"""Independent synthetic checks; no broker calls or production writes."""
import sys
import tempfile
import json
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from test_adx import chain, Desk
from test_adx_hour import HourDesk
from test_adx_hour_review import ReviewFixes, LIVE
from kronos.adx_options import plan
from kronos.adx_brain import decide

out = []
sig = dict(direction='bullish', setup='ADXodus', trigger=1002., stop=975., target=1056.)
for costs in (False, True):
    made = plan(sig, chain(), costs=costs)
    idea = next(i for i in made['ideas'] if i['name'] == 'Buy ATM call')
    premium = abs(idea['net']) * idea['lot']
    assert idea['cost'] != premium
    out.append(dict(case='premium_display', costs_enabled=costs, displayed_cost=idea['cost'], premium=premium))

fixture = ReviewFixes()
try:
    with tempfile.TemporaryDirectory() as root, LIVE():
        desk, row = fixture.live_desk(root)
        credit = dict(name='Put credit spread', kind='credit', legs=[], cases=[900., -400., 0.])
        fixture.made['ideas'].append(credit)
        event = dict(checked=True, inside=True, next=dict(date='2026-11-14', title='Results'))
        desk._events = lambda *a: dict(event)
        with patch('kronos.adx_desk.brain.conviction', return_value=dict(score=80, grade='A', factors=[])):
            current = desk._row('TEST', row['id'])[1]
            selected_call = decide(current, dict(fixture.made, best=credit['name']), event)['call']
            desk.paper(dict(symbol='TEST', signal=row['id'], idea=credit['name']))
        assert selected_call == 'Pass' and len(fixture.requests) == 1
        assert not fixture.requests[0]['extra']['adx'].get('override')
        out.append(dict(case='selected_structure_gate', selected_call=selected_call, fills=1, override_recorded=False))
finally:
    fixture.doCleanups()

fixture = HourDesk()
try:
    with tempfile.TemporaryDirectory() as root, LIVE():
        t = Desk().trade(1, None, status='open')
        t['payload'].update(source='adx1h', entry_spot=100.)
        t['payload']['plan']['expiry'] = '2099-12-31'
        desk = fixture.desk(root, [t])
        desk._views = lambda *a: (SimpleNamespace(c=[94.], dom=[1], adx=[30.]), None)
        desk._after = lambda *a: []  # last completed close is before entry
        desk._bench = lambda: None
        desk._live_quotes = lambda *a: {}
        first = desk.trades()['open'][0]
        desk._live_quotes = lambda *a: {'TEST': dict(ltp=100., stale=False, age=0)}
        recovered = desk.trades()['open'][0]
        assert first['spot_stale'] and recovered['action'] == 'exit'
        out.append(dict(case='pre_entry_close_latched', stale=first['spot_stale'], recorded_source=t['payload']['adx']['breach']['source'], next_action=recovered['action']))
finally:
    fixture.doCleanups()
print(json.dumps(out, indent=2))
