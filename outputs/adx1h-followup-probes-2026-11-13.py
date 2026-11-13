"""Synthetic review probes. No real trades or network calls."""
import sys
import tempfile
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from test_adx_hour_review import ReviewFixes, REQUEST, LIVE
from test_adx_hour import HourDesk
from test_adx import Desk
from kronos import adx_brain, adx_hour

results = []
fixture = ReviewFixes()
try:
    with tempfile.TemporaryDirectory() as root, LIVE():
        desk, row = fixture.live_desk(root)
        saved = desk._saved()
        for r in saved['rows']:
            if r['id'] == row['id']:
                r['quality'] = 0
                r['features'] = {}
        saved['market'] = {'regime': 'bear', 'tilt': -1}
        desk._save('board', saved)
        # Force a real Grade C decision without replacing decide().
        with patch('kronos.adx_desk.brain.conviction', return_value=dict(score=30, grade='C', factors=[])):
            current = desk._row('TEST', row['id'])[1]
            decision = adx_brain.decide(current, fixture.made, dict(checked=True, next=None))
            desk.paper(REQUEST(row))
        assert decision['call'] == 'Pass' and len(fixture.requests) == 1
        results.append(dict(case='manual_grade_C', decision=decision['call'], fill_calls=len(fixture.requests)))
finally:
    fixture.doCleanups()

fixture = ReviewFixes()
try:
    with tempfile.TemporaryDirectory() as root, LIVE():
        desk, row = fixture.live_desk(root)
        desk.bars.frames.pop(adx_hour.NIFTY)
        current = desk._row('TEST', row['id'])[1]
        up = desk._views('TEST', None)[0]
        problem = desk._entry_problem('TEST', current, up)
        assert problem == ''
        desk.paper(REQUEST(row))
        results.append(dict(case='missing_benchmark', gate_problem=problem, fill_calls=len(fixture.requests)))
finally:
    fixture.doCleanups()

fixture = HourDesk()
try:
    with tempfile.TemporaryDirectory() as root, LIVE():
        trade = Desk().trade(1, -700., status='open')
        trade['payload'].update(source='adx1h')
        trade['payload']['plan']['expiry'] = '2099-12-31'
        trade['payload']['legs'] = [dict(strike=100., type='CE', qty=1, entry_fill=10., symbol='MOCK', lot=1)]
        desk = fixture.desk(root, [trade])
        desk._views = lambda *a: (SimpleNamespace(c=[100.], dom=[1], adx=[30.]), None)
        desk._after = lambda *a: []
        desk._bench = lambda: None
        desk.service.desk.ltp = lambda *a: dict(quotes={})
        desk._live_quotes = lambda *a: {'TEST': dict(ltp=100., stale=False, age=0)}
        out = desk.trades()['open'][0]
        assert out['pnl'] == -700.
        results.append(dict(case='missing_option_quote', old_mark_retained=out['pnl'], advice=out['advice']))
        trade['payload']['mark'] = None
        trade['payload']['marks'] = []
        desk._live_quotes = lambda *a: {'TEST': dict(ltp=94., stale=False, age=0)}
        first = desk.trades()['open'][0]['action']
        desk._live_quotes = lambda *a: {'TEST': dict(ltp=100., stale=False, age=0)}
        second = desk.trades()['open'][0]['action']
        assert first == 'exit' and second == 'hold'
        results.append(dict(case='live_stop_recovery', first_action=first, next_action=second))
finally:
    fixture.doCleanups()

fixture = HourDesk()
try:
    with tempfile.TemporaryDirectory() as root:
        desk = fixture.desk(root, [])
        desk._scan()
        saved = desk._saved()
        desk.bars.frames[adx_hour.NIFTY] = desk.bars.frames[adx_hour.NIFTY].iloc[:-1]
        cover = desk.bars.coverage(['TEST', adx_hour.NIFTY])
        starts = []
        desk.start = lambda: starts.append(True)
        desk.auto = -100000.
        desk.board()
        assert cover['behind_n'] == 1 and not desk._behind(saved) and not starts
        results.append(dict(case='partial_refresh_not_retried', behind_symbols=cover['behind'], scan_started=bool(starts)))
finally:
    fixture.doCleanups()

print(json.dumps(results, indent=2))
