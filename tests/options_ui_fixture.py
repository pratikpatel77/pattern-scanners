"""Isolated browser smoke-test server. Synthetic TEST contracts; temporary journal only.

Run: python tests/options_ui_fixture.py
Never connects to FYERS or writes into the user's research database.
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from types import SimpleNamespace
from unittest.mock import Mock
from datetime import datetime
import tempfile
from kronos.service import ResearchService
from kronos.server import create_server
from kronos.options import OptionsDesk, preferences
from kronos.options_math import analyse_structure
from test_kronos_options import option, forecast_value


def main():
    with tempfile.TemporaryDirectory(prefix='kronos-options-ui-') as directory:
        service=ResearchService(root=directory)
        setups=[dict(symbol='TEST',scanner='darvas',label='Synthetic test pattern',timeframe='D',
                     date='2026-10-30',pivot=1005,support=980,score=65)]
        service.market=SimpleNamespace(symbols=lambda:['TEST'],saved_setups=lambda:setups)
        service.setups=lambda:setups
        f=forecast_value()
        f['asof']='2026-10-30'
        f['history']=[dict(date=f'2026-10-{day}',open=990,high=1005,low=985,close=990+day/3,volume=10000) for day in range(10,31)]
        for p in f['paths']:p[-1]['close']=1100.
        prefs=preferences({})
        legs=[option(970,qty=-1,bid=8,ask=8.1),option(950,qty=1,bid=3,ask=3.1)]
        chain=legs+[option(960,bid=4,ask=4.1)]
        market=Mock();market.universe.return_value=['TEST'];market.chain.return_value=chain
        def quote(symbol):
            if symbol.endswith('-EQ'):return dict(ltp=1000.,quote_time=datetime.now().timestamp())
            return {**next(l for l in chain if l['symbol']==symbol),'ltp':7.25,'quote_time':datetime.now().timestamp()}
        market.depth.side_effect=quote
        desk=OptionsDesk(service,market);service.options=desk
        # TESTB keeps a missing IVP so the research-only flow is visible.
        desk.iv_percentile=lambda symbol,iv:dict(value=None if symbol=='TESTB' else 70,source='Synthetic UI test',date='2026-10-30')
        service.prices=SimpleNamespace(quotes=lambda symbols:dict(quotes={symbol:dict(ltp=1000.,source='SYNTHETIC TEST',quote_time=datetime.now().timestamp(),fetched_at=datetime.now().isoformat(),timestamp_kind='quote') for symbol in symbols}))
        desk.events=Mock(return_value=dict(checked=True,events=[],coverage='Isolated UI test calendar'))
        plan=analyse_structure('Put credit spread','bullish',legs,f,prefs,70);plan['id']='original'
        desk.journal.save_settings(prefs)
        desk.journal.save_analysis(dict(symbol='TEST',status='Review',reason='SYNTHETIC UI TEST — isolated temporary journal',
            prefs=prefs,spot=1000,forecast=f,forecast_id=desk.journal.forecast(f),forecast_end='2026-11-06',
            expiry='2026-11-23',iv=25,ivp=dict(value=70,source='UI test'),events=desk.events(),lower=970,upper=1100,candidates=[plan]))
        # Extra synthetic rows for the scanner, journal analytics and alert views.
        research={**plan,'id':'research','name':'Iron condor','reasons':['IV percentile needs a dated source'],'status':'Research only'}
        desk.journal.save_analysis(dict(symbol='TESTB',status='Review',prefs=prefs,spot=1000,forecast=f,forecast_end='2026-11-06',
            expiry='2026-11-23',iv=22,ivp=dict(value=None),events=dict(checked=True,events=[],upcoming=[dict(date='2026-11-20',title='Financial results')]),
            candidates=[research]))
        desk.journal.save_analysis(dict(symbol='TESTC',status='Stock only',reason='Pattern and prediction available. This stock has no current F&O contracts.',forecast=f,candidates=[]))
        desk.journal.save_analysis(dict(symbol='TESTD',status='Data needed',reason='Option chain unavailable',candidates=[]))
        for i,(pnl,name) in enumerate([(850,'Put credit spread'),(-1200,'Iron condor'),(420,'Put credit spread'),(310,'Call debit spread')]):
            t=desk.journal.open(f'closed-{i}','c',dict(symbol=f'PAST{i}',plan=dict(name=name,max_loss=2500,expiry='2026-10-28'),legs=[],
                prefs=prefs,entry_credit=500,entry_charges={},research=name=='Iron condor'))
            desk.journal.update(t['id'],{**t['payload'],'mark':dict(time=datetime.now().isoformat(),net_pnl=pnl)},close=True)
        alert=desk.journal.open('alert','c',dict(symbol='ALERT',plan=dict(name='Put credit spread',max_loss=2000,expiry='2026-11-27',
            underlying_stop=dict(lower=990,upper=None)),legs=legs,prefs=prefs,entry_credit=500,entry_charges={},exit_by='2026-11-06',entry_spot=1010,
            entry_time=datetime.now().isoformat()))
        for n,u in ((120,1004),(-80,996),(-410,987)):
            alert=desk.journal.update(alert['id'],{**alert['payload'],'mark':dict(time=datetime.now().isoformat(),net_pnl=n,underlying=u),
                'exit_signal':'EXIT NOW: underlying stop crossed; directional view invalidated' if u<990 else 'Monitor'})
        original=desk.bootstrap
        desk.bootstrap=lambda:{**original(),'market_open':True}
        server=create_server(service,8767)
        print('SYNTHETIC UI TEST http://127.0.0.1:8767/options',flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:pass
        finally:server.server_close();service.close()


if __name__=='__main__':main()
