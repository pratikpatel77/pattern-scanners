"""Application jobs: forecasts, rolling historical tests and cached setup scans."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import threading
import numpy as np
import pandas as pd
from .data import MarketData, ROOT, signal_dict
from .engine import Cancelled, check_cancel, forecast
from .evaluation import compare, simulate_trade, summarize
from .core import classify_path
from .settings import Settings
from .storage import Store


class BusyError(ValueError):
    pass


def future_observations(bars, origin, timeframe):
    """Only completed later periods can score a saved forecast."""
    if timeframe == 'W':
        if len(bars) and bars.index[-1].weekday() != 4:
            bars = bars.iloc[:-1]
        return bars.loc[bars.index.to_period('W-FRI') > pd.Timestamp(origin).to_period('W-FRI')]
    return bars.loc[bars.index > pd.Timestamp(origin)]


class ResearchService:
    def __init__(self, root=ROOT, predictor=None):
        self.root = Path(root)
        self.directory = self.root/'.cache/kronos_research'
        self.store = Store(self.directory/'research.sqlite3')
        self.market = MarketData(self.root)
        self.predictor = predictor
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='kronos')
        self.lock = threading.Lock()
        self.active = None
        self.events = {}
        from .monitor import DailyMonitor
        self.monitor = DailyMonitor(self)

    def setups(self):
        path = self.directory/'setups.json'
        if path.exists():
            try:
                return json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                pass
        return self.market.saved_setups()

    def bootstrap(self):
        return {'settings':self.store.settings().to_dict(),'setups':self.setups(),
                'symbols':self.market.symbols(),'runs':self.store.list(),
                'active':self.active,'models':['small','mini','base'],
                'runtime_ready':all(importlib.util.find_spec(x) is not None for x in ('torch','einops','huggingface_hub')),
                'strategy_name':'Pattern breakout research',
                'notice':'AI forecasts are research scenarios. Review the sample size, data date and test results.'}

    def submit(self, request):
        if not isinstance(request,dict) or request.get('kind') not in ('forecast','backtest','scan'):
            raise ValueError('Choose forecast, backtest or scan.')
        settings = Settings.from_dict(request.get('settings',self.store.settings().to_dict()))
        kind = request['kind']
        symbol = str(request.get('symbol','')).strip().upper()
        if kind!='scan' and symbol not in self.market.symbols():
            raise ValueError('Choose a symbol from the scanner cache.')
        scanner = request.get('scanner','all')
        if scanner not in ('all','stage','darvas','cup'):
            raise ValueError('Unknown pattern type.')
        cleaned = {'kind':kind,'symbol':symbol,'scanner':scanner,'settings':settings.to_dict()}
        if request.get('asof'):
            cleaned['asof']=str(pd.Timestamp(request['asof']).date())
        if kind=='backtest':
            start,end = pd.Timestamp(request.get('start')),pd.Timestamp(request.get('end'))
            if pd.isna(start) or pd.isna(end) or start>=end:
                raise ValueError('Choose a test start date before its end date.')
            windows = request.get('max_windows',3)
            if type(windows) is not int or not 1<=windows<=20:
                raise ValueError('Use between 1 and 20 test windows.')
            cleaned.update(start=str(start.date()),end=str(end.date()),max_windows=windows)
        with self.lock:
            if self.active is not None:
                raise BusyError('A job is already running. Wait or cancel it first.')
            if not request.get('monitor'):
                self.store.save_settings(settings)
            else:
                cleaned['monitor'] = True
            key = self.store.create(kind,cleaned)
            self.active=key
            self.events[key]=threading.Event()
            self.pool.submit(self._run,key,cleaned,settings)
        return self.store.get(key)

    def cancel(self,key):
        with self.lock:
            if key!=self.active:
                raise ValueError('This job is not running.')
            self.events[key].set()
            self.store.update(key,status='cancelling',message='Stopping after the current forecast batch')
        return self.store.get(key)

    def _run(self,key,request,settings):
        cancel=self.events[key]
        def progress(fraction,message):
            self.store.update(key,progress=round(max(0,min(1,fraction))*100,1),message=message)
        try:
            self.store.update(key,status='running',message='Preparing market data')
            check_cancel(cancel)
            if request['kind']=='scan':
                result=self._scan(settings,progress,cancel)
            elif request['kind']=='backtest':
                result=self._backtest(request,settings,progress,cancel)
            else:
                result=self._forecast(request,settings,progress,cancel)
            check_cancel(cancel)
            self.store.update(key,status='complete',result=result,progress=100,message='Complete')
        except Cancelled:
            self.store.update(key,status='cancelled',message='Cancelled; incomplete results are not counted')
        except Exception as exc:
            # Do not expose broker credentials or raw network response bodies.
            safe = str(exc) if isinstance(exc,(ValueError,FileNotFoundError,ImportError)) else f'{type(exc).__name__}: the job could not finish. Check model dependencies and available memory.'
            self.store.update(key,status='failed',error=safe,message=safe)
        finally:
            with self.lock:
                self.active=None
                self.events.pop(key,None)

    def _scan(self,settings,progress,cancel):
        from scanners.common import liquid_enough
        symbols=self.market.symbols()
        setups,errors=[],[]
        for i,symbol in enumerate(symbols):
            check_cancel(cancel)
            try:
                if liquid_enough(self.market.daily(symbol),2.):
                    setups.extend(signal_dict(s) for s in self.market.detect(symbol,settings.timeframe))
            except ValueError as exc:
                errors.append({'symbol':symbol,'error':str(exc)})
            if i%25==0 or i+1==len(symbols):
                progress((i+1)/max(1,len(symbols)),f'Scanned {i+1}/{len(symbols)} symbols')
        setups.extend(s for s in self.setups() if s['timeframe']!=settings.timeframe)
        setups.sort(key=lambda s:-s['score'])
        path=self.directory/'setups.json'
        temp=path.with_suffix('.tmp')
        temp.write_text(json.dumps(setups,allow_nan=False),encoding='utf-8')
        temp.replace(path)
        return {'setups':setups,'scanned':len(symbols),'errors':errors,'timeframe':settings.timeframe}

    def _forecast(self,request,settings,progress,cancel):
        symbol,asof=request['symbol'],request.get('asof')
        bars=self.market.bars(symbol,settings.timeframe,asof)
        candidates=self.market.detect(symbol,settings.timeframe,asof,request['scanner'])
        signal=candidates[0] if candidates else None
        all_bars=self.market.bars(symbol,settings.timeframe)
        actual=future_observations(all_bars,bars.index[-1],settings.timeframe).iloc[:settings.horizon]
        dates=actual.index if len(actual)==settings.horizon else None
        result=forecast(symbol,bars,settings,signal,self.predictor,dates,progress,cancel)
        result['comparison']=compare(result,actual)
        return result

    def _backtest(self,request,settings,progress,cancel):
        symbol=request['symbol']
        bars=self.market.bars(symbol,settings.timeframe,request['end'])
        if settings.timeframe=='W' and bars.index[-1].weekday()!=4:
            bars=bars.iloc[:-1]  # Exclude a potentially incomplete outcome week.
        start,end=pd.Timestamp(request['start']),pd.Timestamp(request['end'])
        candidates=[i for i in range(settings.lookback-1,len(bars)-settings.horizon)
                    if bars.index[i]>=start and bars.index[i+settings.horizon]<=end]
        # Greedy non-overlapping windows; no concurrent or duplicated paper trades.
        origins=[]
        for i in candidates:
            if not origins or i-origins[-1]>=settings.horizon:
                origins.append(i)
        if not origins:
            raise ValueError('No complete test windows. Widen the date range or reduce lookback/horizon.')
        if len(origins)>request['max_windows']:
            origins=[origins[i] for i in np.linspace(0,len(origins)-1,request['max_windows']).astype(int)]
        windows=[]
        cash=settings.capital
        for number,i in enumerate(origins):
            check_cancel(cancel)
            date=bars.index[i]
            # Pattern detection is replayed at the origin, never selected using today's pattern.
            signals=self.market.detect(symbol,settings.timeframe,date,request['scanner'])
            signal=signals[0] if signals else None
            actual=bars.iloc[i+1:i+1+settings.horizon]
            def window_progress(fraction,message):
                progress((number+fraction)/len(origins),f'Window {number+1}/{len(origins)} · {date.date()} · {message}')
            result=forecast(symbol,bars.iloc[:i+1],settings,signal,self.predictor,
                            actual.index,window_progress,cancel)
            result['comparison']=compare(result,actual)
            rate=result['breakout']['success_rate'] if result['breakout'] else None
            passed=rate is not None and rate>=settings.min_success_rate and settings.samples>=20
            trade=simulate_trade(signal if passed else None,actual,settings,cash)
            if signal and not passed:
                trade['reason']='AI sample rate below the entry filter, or too few samples'
            cash+=trade['pnl']
            result['trade']=trade
            result['ai_filter_passed']=passed
            result['brier']=None
            if signal and result['breakout']:
                outcome,_,_=classify_path(signal,actual,result['breakout']['evaluation_target'],settings.rules())
                result['actual_outcome']=outcome
                result['brier']=(rate/100-int(outcome=='success'))**2
            windows.append(result)
        return {'symbol':symbol,'timeframe':settings.timeframe,'settings':settings.to_dict(),
            'start':request['start'],'end':request['end'],'windows':windows,
            'summary':summarize(windows,settings.capital),
            'method':'Rolling origins; no future prices in inputs; non-overlapping windows; pattern detection replayed at each origin.',
            'limitations':['Historical tests may overlap model pretraining data.',
                'Long-only paper trades enter at the next open after confirmation. Stops win target/stop ties; costs apply on both sides.',
                'Trade returns use the chosen cash and risk limit. The sample is not a guarantee of live results.',
                'A zero-trade test cannot establish strategy profitability.']}

    def run(self,key):
        run=self.store.get(key)
        if run['kind']=='forecast' and run['status']=='complete' and run['result']:
            result=run['result']
            try:
                bars=self.market.bars(result['symbol'],result['timeframe'])
                actual=future_observations(bars,result['asof'],result['timeframe'])
                result['comparison']=compare(result,actual)
            except ValueError:
                pass
        return run

    def close(self):
        self.monitor.close()
        with self.lock:
            for event in self.events.values():
                event.set()
        self.pool.shutdown(wait=False,cancel_futures=True)
