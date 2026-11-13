"""Read-only synthetic audit. No broker calls; all stores are temporary.
Run from the project root: python outputs/adx1h-audit-2026-11-13.py
Assertions document current defects, not desired trading behaviour.
"""
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from test_adx_hour import HourDesk
from test_adx import Desk, chain
from kronos import adx_brain as brain, adx_hour, adx_options

results = []
def record(name, **evidence):
    results.append(dict(check=name, **evidence))

# The real board can be behind and still dispatch an automatic entry.
with tempfile.TemporaryDirectory() as root:
    fixture = HourDesk()
    desk = fixture.desk(root, [])
    fixture.enter(desk)
    desk.start = lambda: None  # prevent background work in this isolated probe
    with patch('kronos.adx_desk.market_open', return_value=True):
        board = desk.board()
        calls = []
        desk.paper = lambda request, auto=False: (calls.append(request) or {'payload': {'plan': {'name': 'mock'}}})
        desk._auto_enter(board)
    assert board['behind'] and calls
    record('stale_board_auto_entry', behind=board['behind'], data_date=board['data_date'], calls=len(calls))

# Call the real paper() path, replacing only feeds, selection and the fill boundary.
# It ignores the decision layer's Grade C and event rejection, as well as Armed.
for case in ('grade_C', 'event_credit', 'armed'):
    with tempfile.TemporaryDirectory() as root:
        paper = []
        desk = HourDesk().desk(root, paper)
        row = dict(id='signal', symbol='TEST', state='armed', verdict='armed' if case == 'armed' else 'enter',
                   direction='bullish', setup='ADXodus', trigger=100., stop=95., target=110., quality=80,
                   grade='C' if case == 'grade_C' else 'A', score=40 if case == 'grade_C' else 80)
        kind = 'credit' if case == 'event_credit' else 'debit'
        idea = dict(name='mock spread', kind=kind, legs=[], cases=[200., -100., -20.])
        plan = dict(ideas=[idea], best=idea['name'], chain={}, stale=False)
        event = dict(checked=True, inside=case == 'event_credit', next=dict(date='2026-11-14', title='Results'))
        desk._row = lambda *a: ([row], row)
        desk._plan = lambda *a: plan
        desk._events = lambda *a: event
        fills = []
        def fill(request):
            fills.append(request)
            trade = Desk().trade(1, None, status='open')
            paper.append(trade)
            return trade
        desk.service.desk.open_paper = fill
        decision = brain.decide(row, plan, event)['call']
        desk.paper(dict(symbol='TEST', signal='signal'), auto=True)
        assert decision in ('Pass', 'Wait') and len(fills) == 1
        record(case + '_entry_bypass', displayed_call=decision, fills=len(fills))

# A provider can return old bars successfully. Refresh records success and scan
# then stamps the board as current, so board() no longer retries on missing bars.
with tempfile.TemporaryDirectory() as root:
    desk = HourDesk().desk(root, [])
    old = desk.bars.frames['TEST'].copy()
    store = adx_hour.HourBars(None, Path(root) / 'cache.pkl')
    store._range = lambda *a: old
    due = store.refresh(['TEST'])
    assert store.frames['TEST'].index[-1] < due
    desk.bars = store
    desk.bars.frames[adx_hour.NIFTY] = old
    desk.universe = lambda: ['TEST']
    desk._scan()
    saved = desk._saved()
    assert not desk.job['error'] and saved['stamp'] == desk._stamp() and saved['data_date'] < desk._expected()
    record('stale_fetch_marked_complete', data_date=saved['data_date'], expected=desk._expected(), stamp_current=True)

# Minute aggregation accepts an incomplete hour, including a single minute.
with tempfile.TemporaryDirectory() as root:
    api = SimpleNamespace(ready=lambda: None, instruments={'TEST': '1'},
                          source=SimpleNamespace(_limiter=SimpleNamespace(wait=lambda: None), session_key='mock'))
    reply = SimpleNamespace(status_code=200, text='121120260915,100,101,99,100,10', raise_for_status=lambda: None)
    with patch('kronos.adx_hour.requests.get', return_value=reply):
        frame = adx_hour.HourBars(api, Path(root) / 'cache.pkl')._definedge('TEST', datetime(2026, 11, 12), datetime(2026, 11, 12))
    accepted = adx_hour.session_bars(frame, datetime(2026, 11, 12, 9, 15))
    assert len(accepted) == 1
    record('incomplete_minute_history', input_minutes=1, accepted_hourly_bars=len(accepted))

# A stop touched and recovered while the watcher was offline is not latched.
with tempfile.TemporaryDirectory() as root:
    trade = Desk().trade(1, None, status='open')
    trade['payload'].update(source='adx1h')
    trade['payload']['plan']['expiry'] = '2099-12-31'
    trade['payload']['adx'].update(trigger=100., stop=95., target=110.)
    desk = HourDesk().desk(root, [trade])
    desk._views = lambda *a: (SimpleNamespace(c=[100.], dom=[1], adx=[30.]), None)
    desk._after = lambda *a: [(101., 94.)]
    desk._bench = lambda: None
    with patch('kronos.adx_desk.market_open', return_value=False):
        out = desk.trades()['open'][0]
    assert out['action'] == 'hold'
    record('missed_stop_touch', stop=95., post_entry_low=94., current_price=100., action=out['action'])

# The preview expiry floor is advisory; no later expiry means even 2 days is picked.
with tempfile.TemporaryDirectory() as root:
    desk = HourDesk().desk(root, [])
    desk.service.desk.chain = lambda *a: chain(days=2.)
    row = dict(symbol='TEST', state='triggered', direction='bullish', setup='ADXodus', trigger=1002., stop=975., target=1056.)
    plan = desk._plan(row, [1000.] * 25)
    assert plan['best'] and plan['days'] <= 3
    record('expiry_inside_exit_window', days=plan['days'], required_days=plan['days_wanted'], best=plan['best'])

print(json.dumps(results, indent=2))
