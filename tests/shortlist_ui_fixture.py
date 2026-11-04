"""Synthetic shortlist server: temporary journal and no broker or model calls."""
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kronos.server import create_server
from kronos.storage import Store


def main():
    with tempfile.TemporaryDirectory(prefix='shortlist-ui-') as directory:
        root = Path(directory)
        (root/'results.html').write_text('<!doctype html><title>Fixture scanner</title><p>Scanner fixture</p>')
        rows = [
            dict(symbol='ALPHA',scanner='darvas',timeframe='D',direction='long',close=99,pivot=100,support=95,score=82,date='2026-11-03'),
            dict(symbol='ALPHA',scanner='cup_bear',timeframe='W',direction='short',close=99,pivot=96,support=105,score=65,date='2026-11-03'),
            dict(symbol='BETA',scanner='stage_bear',timeframe='D',direction='short',close=201,pivot=200,support=210,score=75,date='2026-11-03'),
            dict(symbol='GAMMA',scanner='cup',timeframe='W',direction='long',close=300,pivot=310,support=290,score=70,date='2026-11-03'),
            dict(symbol='OLD',scanner='darvas',timeframe='D',direction='long',close=50,pivot=51,support=45,score=60,date='2026-10-15')]
        def quotes(symbols):
            stamp = datetime.now(timezone.utc)
            prices = {'ALPHA':99.5,'BETA':199,'GAMMA':285,'OLD':50.9}
            return dict(market_open=True,quotes={s:dict(ltp=prices[s],source='SYNTHETIC TEST',fresh=s!='OLD',
                quote_time=stamp.timestamp() if s!='OLD' else None,fetched_at=stamp.isoformat()) for s in symbols})
        state = dict(status='complete',message='SYNTHETIC TEST — no broker connection',revision=None)
        service = SimpleNamespace(store=Store(root/'research.sqlite3'),setups=lambda:rows,
            root=root,directory=root,prices=SimpleNamespace(quotes=quotes),
            refresh=SimpleNamespace(status=lambda:state,start=lambda:state),
            options=SimpleNamespace(market=SimpleNamespace(universe=lambda:[])),
            bootstrap=lambda:dict(settings={},setups=rows,symbols=['ALPHA','BETA','GAMMA'],runs=[]))
        server = create_server(service,0)
        print(f'http://127.0.0.1:{server.server_port}',flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()


if __name__=='__main__':
    main()
