"""Live options desk: whole F&O universe, no orders, no options backtests."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import threading

import numpy as np
import pandas as pd

from .engine import forecast, check_cancel, Cancelled
from .options_journal import OptionsJournal, paper_stats, summarise
from .options_sources import ContextSources
from .options_market import LiveMarket, clock, market_open, number
from .options_math import analyse_structure, book_fill, candidates, entry, fee, fill, charges, underlying_stop, reward_reasons, REWARD_REASONS
from .settings import Settings

LEGACY=('capital','risk_pct','portfolio_pct')  # paper capital and loss budgets were removed; old saved values are ignored
DEFAULTS=dict(slippage=.01,
              min_net_profit=100.,min_return_on_risk_pct=10.,min_scenario_pct=60.,min_oi=10000,min_volume=1000,max_spread_pct=8.,
              horizon=5,samples=32,avoid_events=True,brokerage=20.)


def preferences(values):
    if not isinstance(values,dict): raise ValueError('Unknown options settings.')
    values={k:v for k,v in values.items() if k not in LEGACY}
    if set(values)-set(DEFAULTS): raise ValueError('Unknown options settings.')
    result={**DEFAULTS,**values}
    bounds=dict(slippage=(.001,.05),
                min_net_profit=(1,1e7),min_return_on_risk_pct=(.1,500),min_scenario_pct=(50,95),min_oi=(1,1e9),min_volume=(1,1e9),max_spread_pct=(.1,20),
                horizon=(2,15),samples=(32,128),brokerage=(0,500))
    for key,(lo,hi) in bounds.items():
        val=result[key]
        if type(val) not in (int,float) or not math.isfinite(val) or not lo<=val<=hi:
            raise ValueError(f'{key}: use a value from {lo} to {hi}.')
    if any(type(result[k]) is not int for k in ('horizon','samples','min_oi','min_volume')):
        raise ValueError('Horizon, samples, OI and volume must be whole numbers.')
    if type(result['avoid_events']) is not bool:
        raise ValueError('Check event settings.')
    return result


# Warnings a research paper trade may acknowledge. Never liquidity or price-age failures.
ADVISORY=('Stressed net profit below minimum','Stressed net return on maximum loss below minimum','Negative average outcome under IV stress','Too few profitable model scenarios',
          'Credit too small for the spreads','IV percentile needs a dated source','Event calendar needs review')
# Entry timing: the idea stays visible for review, but entry waits for fresh live data.
TIMING=('Chain preview: refresh leg depth before entry','EOD analysis: run a live forecast before paper entry',
        'Underlying price is stale or market is closed','Option prices unavailable: market closed or a selected strike has no two-sided book',
        'Forecast is older than 10 minutes. Analyse this stock again','Stock moved more than 0.5%. Analyse this stock again')
STATUS_RANK={'Paper candidate':2,'Research only':1,'Skip':0}


def classify(reasons):
    if any(r not in ADVISORY and r not in TIMING for r in reasons): return 'Skip'
    return 'Research only' if any(r in ADVISORY for r in reasons) else 'Paper candidate'


def liquid(option,prefs):
    bid,ask=number(option.get('bid')),number(option.get('ask_price'))
    qty=option.get('lot',1)*abs(option.get('qty',1))
    return (0<bid<=ask and (ask-bid)/((ask+bid)/2)*100<=prefs['max_spread_pct']
            and number(option.get('oi'))>=prefs['min_oi'] and number(option.get('v'))>=prefs['min_volume']
            and (option.get('bid_qty',qty)>=qty and option.get('ask_qty',qty)>=qty))


class OptionsDesk:
    def __init__(self,service,provider=None):
        self.service=service
        self.journal=OptionsJournal(service.store)
        self.sources=ContextSources(service.store)
        self.market=provider or LiveMarket()
        self.action_lock=threading.RLock()
        self.event_cache=None
        self.event_error_until=0.

    def bootstrap(self):
        stored=self.journal.settings()
        prefs=preferences(stored)
        if stored!=prefs: self.journal.save_settings(prefs)  # first run and old files: keep predefined values on disk
        from datalayer import config
        forecasts=self.journal.forecasts()
        scored=[r['outcome'] for r in forecasts if r['outcome'] and r['outcome'].get('direction_correct') is not None]
        paper=self.journal.paper()
        return dict(settings=prefs,login_ready=bool(config.get('FYERS_APP_ID') and config.get('FYERS_ACCESS_TOKEN')),
            market_open=market_open(),time=clock().isoformat(),active=self.service.active,
            paper=paper,stats=paper_stats(paper),analyses=[self.compact(a) for a in self.journal.analyses() if a.get('status')!='Stock only'],
            stock_ideas=self.stock_ideas(),
            accuracy=dict(recorded=len(forecasts),scored=len(scored),correct=sum(r['direction_correct'] for r in scored),
                          pct=round(sum(r['direction_correct'] for r in scored)/len(scored)*100,1) if scored else None),
            sources=dict(quotes='FYERS option depth; stock LTP uses FYERS with Definedge fallback',
                         ivp='Dated manual source or collected daily ATM IV history',
                         events='NSE scheduled results/actions, with dated manual fallback'),
            note='Paper trading only. Scenario shares are not measured accuracy or market probabilities.')

    def stock_ideas(self):
        """All saved pattern detections, including stocks without an options analysis."""
        rows={}
        setups=list(self.service.setups())
        market=getattr(self.service,'market',None)
        # Once the workspace has a refreshed report, it is the current universe.
        # Do not merge old CSV detections back into the options stock list.
        directory=getattr(self.service,'directory',None)
        if market is not None and not (directory and (directory/'scanner.html').exists()):
            setups.extend(market.saved_setups())
        from scanners import ALL_SCANNERS
        for s in setups:
            symbol=str(s.get('symbol','')).strip().upper()
            if not symbol: continue
            row=rows.setdefault(symbol,dict(symbol=symbol,patterns=[],setups=[],directions=[]))
            direction='short' if s.get('direction')=='short' else 'long'
            if direction=='short':
                # Bearish phases reuse words like "Handle forming"; name the pattern so it is unambiguous.
                module=ALL_SCANNERS.get(s.get('scanner'))
                label='Bearish '+(module.TITLE if module else str(s.get('label') or 'pattern'))
            else:
                label=str(s.get('label') or s.get('scanner') or 'Pattern')
            label+=' · '+str(s.get('timeframe','D'))
            if label not in row['patterns']: row['patterns'].append(label)
            if direction not in row['directions']: row['directions'].append(direction)
            row['setups'].append(s)
        for row in rows.values():
            d=row['directions']
            row['bias']='mixed' if len(d)>1 else ('bearish' if d==['short'] else 'bullish')
        return sorted(rows.values(),key=lambda r:r['symbol'])

    @staticmethod
    def top_ideas(candidates):
        """The best two distinct structures. Ready ideas rank first; others stay visible for review."""
        result=[];names=set()
        for c in sorted(candidates,key=lambda c:(STATUS_RANK.get(c['status'],0),c['score']),reverse=True):
            if c['name'] not in names:
                result.append(c);names.add(c['name'])
            if len(result)==2: break
        return result

    @staticmethod
    def entry_checks(plan):
        """Recommendation warnings can be studied on paper; execution limits cannot."""
        warnings=[]; blockers=[]
        for reason in plan.get('reasons',[]):
            if reason=='Chain preview: refresh leg depth before entry': continue
            (warnings if reason in ADVISORY else blockers).append(reason)
        return dict(warnings=warnings,blockers=blockers)

    def preview_quote(self,symbol):
        method=getattr(type(self.market),'preview_depth',None)
        return self.market.preview_depth(symbol) if method else self.market.depth(symbol)

    def _depths(self,symbols,preview=False):
        """Books for several symbols at once when the provider supports it; one by one otherwise."""
        if getattr(type(self.market),'depth_many',None):
            return self.market.depth_many(symbols,require_fresh=not preview)
        return [self.preview_quote(s) if preview else self.market.depth(s) for s in symbols]

    def contracts(self,key):
        a=self.journal.analysis(key)
        if not a.get('forecast'): raise ValueError('Run a stock analysis first.')
        chain=self.market.chain(a['symbol'],a['forecast_end'])
        if any(c['expiry']!=a['expiry'] for c in chain):
            raise ValueError('Available expiry changed. Run a new analysis.')
        return dict(contracts=chain)

    def analysis(self,key):
        a=self.journal.analysis(key)
        if a.get('forecast_end'):
            a['events']=self.events(a['symbol'],a['forecast_end'])
            if a.get('iv'): a['ivp']=self.iv_percentile(a['symbol'],a['iv'])
        for c in a.get('candidates',[]):
            if c.get('calculation_version') != 2:
                c.setdefault('reasons',[]).append('Saved calculation is outdated. Analyse this stock again')
            if a.get('forecast'):
                c.setdefault('underlying_stop',underlying_stop(c['bias'],c['legs'],a['forecast']))
            # Refresh context warnings without changing saved prices or model outcomes.
            c['reasons']=[r for r in c.get('reasons',[]) if r not in
                ('IV percentile needs a dated source','Event calendar needs review','Scheduled event during trade period','One lot exceeds the loss budget')]
            if a.get('ivp',{}).get('value') is None:c['reasons'].append('IV percentile needs a dated source')
            if not a.get('events',{}).get('checked'):c['reasons'].append('Event calendar needs review')
            if a.get('events',{}).get('events') and a.get('prefs',{}).get('avoid_events',True):
                c['reasons'].append('Scheduled event during trade period')
            c['reasons']=[r for r in c['reasons'] if r not in REWARD_REASONS]
            c['reasons'].extend(reward_reasons(c,preferences(a.get('prefs',{}))))
            c['status']=classify(c['reasons'])
            c.pop('checks',None)
        return a

    def reprice(self,request):
        """Resolve edited strikes on the server; never accept prices or quantities from a browser."""
        with self.action_lock:
            a=self.journal.analysis(str(request.get('analysis','')))
            c=next((c for c in a.get('candidates',[]) if c['id']==request.get('candidate')),None)
            if not c: raise ValueError('Strategy not found.')
            strikes=request.get('strikes')
            if not isinstance(strikes,list) or len(strikes)!=len(c['legs']):
                raise ValueError('Choose one strike for every leg.')
            if any(type(k) not in (int,float) or not math.isfinite(k) or k<=0 for k in strikes):
                raise ValueError('Choose valid strikes.')
            for i,old in enumerate(c['legs']):
                for j,other in enumerate(c['legs'][:i]):
                    before=old['strike']-other['strike'];after=strikes[i]-strikes[j]
                    if (before==0 and after!=0) or (before!=0 and before*after<=0):
                        raise ValueError('Keep the strike order and shared centre to preserve this strategy and its hedges.')
            chain=self.contracts(a['id'])['contracts']
            legs=[];seen=set()
            for old,strike in zip(c['legs'],strikes):
                contract=next((o for o in chain if o['type']==old['type'] and o['strike']==strike),None)
                if not contract: raise ValueError('Strike is not available for this stock, option type and expiry.')
                if contract['symbol'] in seen: raise ValueError('Each leg must use a different contract.')
                seen.add(contract['symbol']);legs.append({**contract,'qty':old['qty']})
            prefs=preferences(a['prefs'])
            books=self._depths([f"NSE:{a['symbol']}-EQ"]+[l['symbol'] for l in legs],preview=True)
            spot=books[0]
            fresh=[];issues=[]
            if spot.get('fresh') is False: issues.append('Underlying price is stale or market is closed')
            for leg,book in zip(legs,books[1:]):
                value=self.quote_leg(leg,book)
                if value['iv']<=0: raise ValueError('IV is unavailable for a selected strike.')
                if not liquid(value,prefs): issues.append('A selected leg fails liquidity or depth checks')
                fresh.append(value)
            if any(l.get('fresh') is False for l in fresh):
                issues.append('Option prices unavailable: market closed or a selected strike has no two-sided book')
            ivp=self.iv_percentile(a['symbol'],a['iv']) if a.get('iv') else a['ivp']
            plan=analyse_structure(c['name'],c['bias'],fresh,a['forecast'],prefs,ivp['value'])
            event=self.events(a['symbol'],a['forecast_end'])
            if not event['checked']: issues.append('Event calendar needs review')
            if event['events'] and prefs['avoid_events']: issues.append('Scheduled event during trade period')
            age=(datetime.now(timezone.utc)-datetime.fromisoformat(a.get('forecast_created',a['created']))).total_seconds()
            if age>600: issues.append('Forecast is older than 10 minutes. Analyse this stock again')
            if abs(number(spot['ltp'])/a['forecast']['last_close']-1)>.005:
                issues.append('Stock moved more than 0.5%. Analyse this stock again')
            plan['reasons']=list(dict.fromkeys(plan['reasons']+issues))
            plan['status']=classify(plan['reasons'])
            plan['id']=hashlib.sha256(json.dumps([[l['symbol'],l['qty']] for l in fresh]).encode()).hexdigest()[:16]
            plan['quote_time']=min(l['quote_time'] for l in fresh)
            plan['checks']=self.entry_checks(plan)
            root=a.get('parent_analysis',a['id'])
            # One replaceable draft per original idea; the forecast stays on the parent row.
            draft_key='draft-'+hashlib.sha256(f"{root}|{request.get('candidate')}".encode()).hexdigest()[:24]
            saved=self.journal.save_analysis({**{k:v for k,v in a.items() if k!='forecast'},'parent_analysis':root,'is_draft':True,
                'forecast_created':a.get('forecast_created',a['created']),'candidates':[plan],
                'spot':number(spot['ltp']),'quote_time':spot['quote_time'],'events':event,'ivp':ivp,
                'status':'Skip' if plan['status']=='Skip' else 'Review'},key=draft_key)
            return dict(analysis=saved['id'],plan=plan,spot=saved['spot'],events=event,ivp=ivp)

    @staticmethod
    def compact(a):
        """Scanner-dashboard row. Statuses are re-derived from each idea's stored reasons."""
        row=a if 'forecast_summary' in a else summarise(a)
        ideas=[]
        for c in row.get('candidates',[]):
            reasons=list(c.get('reasons') or [])
            if c.get('calculation_version')!=2: reasons.append('Saved calculation is outdated. Analyse this stock again')
            ideas.append({**c,'reasons':reasons,'status':classify(reasons)})
        ranked=sorted(ideas,key=lambda c:(STATUS_RANK[c['status']],c.get('score') or 0),reverse=True)
        best=ranked[0] if ranked else None
        f=row.get('forecast_summary') or {}
        expected=(f['median_end']/f['last_close']-1)*100 if f.get('median_end') and f.get('last_close') else None
        events=row.get('events') or {}
        upcoming=[e for e in events.get('upcoming',events.get('events',[])) if e.get('date')]
        counts={s:sum(c['status']==s for c in ideas) for s in STATUS_RANK}
        return {k:v for k,v in row.items() if k not in ('candidates','forecast_summary','events','prefs')} | {
            'has_forecast':bool(f),'bias':f.get('bias'),'expected_pct':round(expected,2) if expected is not None else None,
            'forecast_close':f.get('median_end'),'ivp_value':(row.get('ivp') or {}).get('value'),
            'events_checked':events.get('checked'),'event_in_trade':bool(events.get('events')),
            'next_event':min(upcoming,key=lambda e:e['date']) if upcoming else None,
            'candidate_count':len(ideas),'counts':counts,
            'best':{k:best.get(k) for k in ('name','bias','status','entry_credit','max_loss','max_profit','score',
                    'stressed_mean_net','scenario_profit_pct','short_touch_pct','reasons')} if best else None}

    def save_reference(self,request):
        symbol=str(request.get('symbol','')).upper()
        if symbol not in self.market.universe(): raise ValueError('Choose a stock from the F&O list.')
        source=str(request.get('source','')).strip()[:500]
        if not source.startswith('https://'): raise ValueError('Add the source page link you checked.')
        val=request.get('ivp')
        if val is not None and (type(val) not in (int,float) or not math.isfinite(val) or not 0<=val<=100):
            raise ValueError('IV percentile must be from 0 to 100.')
        event_end=str(pd.Timestamp(request.get('events_through')).date()) if request.get('events_through') else None
        if event_end and event_end<str(clock().date()): raise ValueError('Event review must cover today or later.')
        events=[]
        for e in request.get('events',[]):
            events.append(dict(date=str(pd.Timestamp(e['date']).date()),title=str(e['title'])[:200]))
        value=dict(date=str(clock().date()),source=source,ivp=val,
                   events_through=event_end,events=events,
                   event_source=str(request.get('event_source',''))[:500])
        if event_end and not value['event_source'].startswith('https://'):
            raise ValueError('Add the event calendar source link.')
        self.journal.reference(symbol,value)
        return value

    def events(self,symbol,end):
        ref=self.journal.reference(symbol)
        if ref.get('date')==str(clock().date()) and ref.get('events_through','') and ref['events_through']>=end:
            return dict(checked=True,source=ref['event_source'],events=[e for e in ref['events'] if str(clock().date())<=e['date']<=end],
                        coverage='Manual review of scheduled events')
        return self.sources.calendar(symbol,end)

    def iv_percentile(self,symbol,iv):
        ref=self.journal.reference(symbol)
        if ref.get('date')==str(clock().date()) and ref.get('ivp') is not None:
            return dict(value=ref['ivp'],source=ref['source'],date=ref['date'],sessions=None)
        automatic=self.sources.ivp(symbol)
        if automatic.get('usable'): return automatic
        # One session observation, consistent horizon/expiry rule, stored locally from now onwards.
        method=f"ATM nearest expiry beyond {preferences(self.journal.settings())['horizon']} forecast bars + 3 days"
        with self.service.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS options_iv (symbol TEXT,date TEXT,method TEXT,iv REAL,PRIMARY KEY(symbol,date,method))')
            db.execute('INSERT OR REPLACE INTO options_iv VALUES (?,?,?,?)',(symbol,str(clock().date()),method,iv))
            rows=db.execute('SELECT iv FROM options_iv WHERE symbol=? AND method=? AND date>=? AND date<?',
                (symbol,method,str(clock().date()-timedelta(days=365)),str(clock().date()))).fetchall()
        return dict(value=round(sum(r[0]<iv for r in rows)/len(rows)*100,1) if len(rows)>=60 else None,
                    source='Collected daily ATM IV (intraday observation; not a constant-maturity series)',
                    automatic_status=automatic.get('status'),note=automatic.get('note'),
                    observed_value=automatic.get('value_raw'),observed_sessions=automatic.get('sessions'),
                    observed_source=automatic.get('source'),observed_date=automatic.get('date'),
                    date=str(clock().date()),sessions=len(rows))

    def submit(self,request):
        prefs=preferences(request.get('settings',self.journal.settings()))
        symbols=self.market.universe()
        chosen=request.get('symbol')
        pattern_symbols={r['symbol'] for r in self.stock_ideas()}
        if chosen and chosen not in symbols:
            raise ValueError('The options desk covers F&O stocks only. Choose a stock from the F&O list.')
        scope=request.get('scope','fno')
        if scope not in ('fno','patterns'): raise ValueError('Choose a scan scope.')
        if not chosen and scope=='patterns':
            symbols=[s for s in symbols if s in pattern_symbols]
            if not symbols: raise ValueError('No saved pattern stock currently has F&O contracts.')
        # Refresh the full universe independently of pattern scanner detections.
        from .service import BusyError
        with self.service.lock:
            if self.service.active: raise BusyError('A Kronos job is already running.')
            self.journal.save_settings(prefs)
            label=chosen or ('F&O pattern stocks' if scope=='patterns' else 'All F&O stocks')
            key=self.service.store.create('options',dict(symbol=label,settings=prefs))
            self.service.active=key
            cancel=threading.Event();self.service.events[key]=cancel
            self.service.pool.submit(self._run,key,[chosen] if chosen else symbols,prefs,cancel)
        return self.service.store.get(key)

    def _run(self,key,symbols,prefs,cancel):
        results=[]
        try:
            for i,symbol in enumerate(symbols):
                check_cancel(cancel)
                self.service.store.update(key,status='running',progress=round(i/len(symbols)*100,1),message=f'{symbol} · {i+1}/{len(symbols)}')
                try:
                    def progress(fraction,message):
                        self.service.store.update(key,progress=round((i+fraction*.85)/len(symbols)*100,1),
                                                  message=f'{symbol} · {i+1}/{len(symbols)} · {message}')
                    value=self.analyse(symbol,prefs,cancel,progress)
                except Cancelled: raise
                except Exception as exc:
                    reason=str(exc) if isinstance(exc,ValueError) else 'Analysis failed. Check model and live-data connection.'
                    value=self.journal.save_analysis(dict(symbol=symbol,status='Skip',reason=reason,candidates=[]))
                results.append(self.compact(value))
                if value.get('status')=='Skip' and any(word in value.get('reason','').lower()
                    for word in ('connection failed','access denied','login','rate limit','market closed')):
                    self.service.store.update(key,status='failed',message='Scan stopped: live data unavailable',
                        error=value['reason'],result=dict(stocks=results,unprocessed=len(symbols)-i-1))
                    return
            self.service.store.update(key,status='complete',progress=100,message='Options scan complete',result=dict(stocks=results))
        except Cancelled:
            self.service.store.update(key,status='cancelled',message='Stopped; completed stock records kept',result=dict(stocks=results))
        finally:
            with self.service.lock:
                self.service.active=None;self.service.events.pop(key,None)

    def _closing_bars(self,symbol):
        """15:30-16:00 on a trading day: the provider's daily candle is not settled yet, so build today's
        candle from the closing quote. Otherwise a forecast run right after the close would start from yesterday."""
        now=clock()
        if now.weekday()>=5 or not 930<=now.hour*60+now.minute<960 or not hasattr(self.market,'depth'):
            return None
        try:
            quote=self.market.depth(f'NSE:{symbol}-EQ',require_fresh=False)
            stamp=number(quote.get('quote_time'))
            if not stamp or datetime.fromtimestamp(stamp,now.tzinfo).date()!=now.date():
                return None      # no trade today (holiday): the settled history is already current
            return self.market.history(symbol,quote)
        except (ValueError,KeyError,TypeError,AttributeError):
            return None

    def analyse(self,symbol,prefs,cancel=None,progress=None):
        eod=not market_open()
        bars=self._closing_bars(symbol) if eod else None
        if bars is not None:
            spot=dict(ltp=float(bars.close.iloc[-1]),quote_time=None)
        elif eod:
            bars=getattr(self.service,'prices',self.market).eod_history(symbol)
            spot=dict(ltp=float(bars.close.iloc[-1]),quote_time=None)
        else:
            spot=self.market.depth(f'NSE:{symbol}-EQ')
            bars=self.market.history(symbol,spot)
        base=self.service.store.settings()
        settings=replace(base,timeframe='D',horizon=prefs['horizon'],samples=prefs['samples'],lookback=min(base.lookback,len(bars)))
        signals=[]
        try:
            from scanners import REGISTRY
            for module in REGISTRY.values():
                signal=module.scan(symbol,bars,'D',None)
                if signal: signals.append(signal)
        except (ValueError,TypeError,KeyError): pass
        forecast_created=datetime.now(timezone.utc).isoformat()
        result=forecast(symbol,bars,settings,signals[0] if signals else None,self.service.predictor,
                        progress=progress,cancel=cancel,include_paths=True)
        result['metadata'].update(live_spot_at=spot['quote_time'],partial_day=not eod,
                                  data_mode='EOD' if eod else 'Live',data_asof=str(bars.index[-1].date()))
        forecast_id=self.journal.forecast(result)
        end=result['forecast'][-1]['date']
        # Keep the prediction visible even if option data subsequently fails.
        base_value=dict(symbol=symbol,prefs=prefs,forecast=result,forecast_id=forecast_id,
            spot=number(spot['ltp']),patterns=[s.label for s in signals],
            forecast_created=forecast_created,forecast_end=end,quote_time=spot['quote_time'],candidates=[],
            data_mode='EOD' if eod else 'Live',data_asof=str(bars.index[-1].date()))
        try:
            return self._build_options(base_value,result,symbol,prefs,spot,signals,forecast_id,end,forecast_created)
        except ValueError as exc:
            return self.journal.save_analysis({**base_value,'status':'Data needed','reason':str(exc)})

    def _build_options(self,base_value,result,symbol,prefs,spot,signals,forecast_id,end,forecast_created):
        if symbol not in self.market.universe():
            return self.journal.save_analysis({**base_value,'status':'Stock only',
                'reason':'Pattern and prediction available. This stock has no current F&O contracts.'})
        # Inference may take minutes. Never carry the pre-inference quote into the trade screen.
        fresh_spot=spot if base_value.get('data_mode')=='EOD' else self.market.depth(f'NSE:{symbol}-EQ')
        if abs(number(fresh_spot['ltp'])/result['last_close']-1)>.005:
            raise ValueError('Stock moved more than 0.5% while Kronos was running. Run a fresh forecast.')
        spot=fresh_spot
        chain=self.market.chain(symbol,end)
        atm=min(chain,key=lambda c:abs(c['strike']-result['last_close']))
        if atm['iv']<=0: raise ValueError('FYERS did not provide ATM IV.')
        ivp=self.iv_percentile(symbol,atm['iv'])
        events=self.events(symbol,end)
        floors=[min(r['low'] for r in p) for p in result['paths']]
        ceilings=[max(r['high'] for r in p) for p in result['paths']]
        lower,upper=float(np.quantile(floors,.1)),float(np.quantile(ceilings,.9))
        chain=[c for c in chain if liquid(c,prefs) and c['iv']>0]
        explored=[]
        for name,bias,legs in candidates(chain,result['last_close'],lower,upper):
            try:
                plan=analyse_structure(name,bias,legs,result,prefs,ivp['value'])
                if base_value.get('data_mode')=='EOD': plan['reasons'].append('EOD analysis: run a live forecast before paper entry')
                if not events['checked']: plan['reasons'].append('Event calendar needs review')
                if events['events'] and prefs['avoid_events']: plan['reasons'].append('Scheduled event during trade period')
                plan['status']=classify(plan['reasons'])
                plan['id']=hashlib.sha256(json.dumps([[l['symbol'],l['qty']] for l in legs]).encode()).hexdigest()[:16]
                explored.append(plan)
            except ValueError: pass
        explored.sort(key=lambda c:(STATUS_RANK[c['status']],c['score']),reverse=True)
        # Keep distinct structures and useful alternatives, rather than hundreds of near-identical widths.
        retained=[]; counts={}
        for c in explored:
            if counts.get(c['name'],0)>=3: continue
            retained.append(c);counts[c['name']]=counts.get(c['name'],0)+1
        for c in retained:
            if c['status']!='Skip': c['reasons'].append('Chain preview: refresh leg depth before entry')
        value=dict(symbol=symbol,data_mode=base_value.get('data_mode','Live'),data_asof=base_value.get('data_asof'),status='Review' if any(c['status']!='Skip' for c in retained) else 'Skip',
            reason='Compare the plans below' if retained else 'No liquid, useful hedged structure found',
            prefs=prefs,forecast=result,forecast_id=forecast_id,spot=number(spot['ltp']),iv=atm['iv'],ivp=ivp,events=events,
            patterns=[s.label for s in signals],lower=lower,upper=upper,candidates=retained,
            quote_time=spot['quote_time'],forecast_created=forecast_created,forecast_end=end,expiry=atm['expiry'])
        return self.journal.save_analysis(value)

    @staticmethod
    def quote_leg(leg,quote):
        # Depth represents expiry as an epoch; instrument identity stays from the master.
        return {**leg,**quote,**{k:leg[k] for k in ('symbol','underlying','expiry','strike','type','lot','tick','qty') if k in leg}}

    def _fresh_legs(self,legs,prefs):
        fresh=[]
        for leg,quote in zip(legs,self._depths([l['symbol'] for l in legs])):
            value=self.quote_leg(leg,quote)
            if not liquid(value,prefs): raise ValueError('A leg fails spread, volume, OI or one-lot depth checks.')
            fresh.append(value)
        return fresh

    def open_paper(self,request):
        with self.action_lock:
            a=self.journal.analysis(str(request.get('analysis','')))
            if a.get('data_mode')=='EOD':
                raise ValueError('EOD analysis is for review. Run a live forecast during market hours before paper entry.')
            if (datetime.now(timezone.utc)-datetime.fromisoformat(a.get('forecast_created',a['created']))).total_seconds()>600:
                raise ValueError('Analysis older than 10 minutes. Run a fresh analysis.')
            c=next((c for c in a.get('candidates',[]) if c['id']==request.get('candidate')),None)
            if not c: raise ValueError('This idea did not pass the entry checks.')
            research=request.get('accept_warnings') is True
            status=classify(c.get('reasons',[]))
            if status=='Skip': raise ValueError('This idea did not pass the entry checks: '+', '.join(self.entry_checks(c)['blockers']))
            if status!='Paper candidate' and not research:
                raise ValueError('This idea did not pass the entry checks. Review its warnings before a research paper trade.')
            prefs=preferences(a['prefs'])
            spot=self.market.depth(f"NSE:{a['symbol']}-EQ")
            if abs(number(spot['ltp'])/a['forecast']['last_close']-1)>.005:
                raise ValueError('Stock moved more than 0.5%. Refresh the Kronos forecast.')
            events=self.events(a['symbol'],a['forecast_end'])
            if (not events['checked'] and not research) or (events['events'] and prefs['avoid_events']):
                raise ValueError('Event check failed. Refresh the analysis or review the calendar.')
            legs=self._fresh_legs(c['legs'],prefs)
            plan=analyse_structure(c['name'],c['bias'],legs,a['forecast'],prefs,a['ivp']['value'])
            if not events['checked']: plan['reasons'].append('Event calendar needs review')
            checks=self.entry_checks(plan)
            if checks['blockers'] or (checks['warnings'] and not research):
                raise ValueError('Fresh entry checks: '+', '.join(checks['blockers']+checks['warnings']))
            for l in legs: l['entry_fill']=fill(l,l['qty'])  # buy at ask, sell at bid
            payload=dict(symbol=a['symbol'],plan=plan,legs=legs,prefs=prefs,forecast_id=a.get('forecast_id'),
                entry_spot=spot['ltp'],entry_time=clock().isoformat(),exit_by=plan['exit_by'],
                entry_credit=plan['entry_credit'],entry_charges=plan['entry_charges'],
                research=bool(checks['warnings']),accepted_warnings=checks['warnings'],
                note='One-lot paper fill. All legs assumed filled together; real execution is not guaranteed.')
            payload['priced_analysis']=a['id']
            return self.journal.open(a.get('parent_analysis',a['id']),c['id'],payload)

    def mark_paper(self,request):
        with self.action_lock:
            trade=self.journal.paper(str(request.get('id','')))
            if trade['status']!='open': raise ValueError('Paper trade is already closed.')
            p=trade['payload'];prefs=p['prefs']
            if clock().date()>=pd.Timestamp(p['plan']['expiry']).date():
                raise ValueError('Expiry reached without a fresh exit. Manual settlement reconciliation is required; no fill invented.')
            # A close is risk-reducing: do not block it on new-entry OI/volume filters.
            legs=[];buy=sell=0.
            for l,q in zip(p['legs'],self._depths([l['symbol'] for l in p['legs']])):
                qty=abs(l['qty'])*l['lot']
                if not 0<q['bid']<=q['ask_price']:
                    raise ValueError('No live two-sided quote for every leg; cannot mark or close.')
                try:  # closing as a market order: longs hit the bids, shorts lift the asks, deeper levels if needed
                    price,levels=book_fill({**l,**q},-l['qty'],qty)
                except ValueError as exc:
                    raise ValueError(f"{l['symbol']}: {exc}") from None
                if l['qty']>0: sell+=price*qty
                else: buy+=price*qty
                legs.append(dict(symbol=l['symbol'],price=price,levels=levels,quote_time=q['quote_time']))
            # Desk trades are billed like a broker account: brokerage per executed exit order too.
            orders,per_order=len(p['legs']),p['prefs'].get('brokerage',0.)
            exit_charges=charges(buy,sell,orders,per_order)
            net=p['entry_credit']+sell-buy-sum(p['entry_charges'].values())-sum(exit_charges.values())
            p={**p,'mark':dict(time=clock().isoformat(),net_pnl=round(net,2),exit_charges=exit_charges,legs=legs)}
            p['exit_signal']='Close: holding period ended' if str(clock().date())>=p['exit_by'] else 'Monitor'
            if net>=max(0,p['entry_credit'])*.5 and p['entry_credit']>0: p['exit_signal']='Review exit: 50% of entry credit earned'
            if p['plan'].get('max_loss') and net<=-p['plan']['max_loss']*.5: p['exit_signal']='Review exit: half the planned loss reached'
            stop=p['plan'].get('underlying_stop')
            if stop:
                spot=self.market.depth(f"NSE:{p['symbol']}-EQ")
                value=number(spot['ltp'])
                p['mark']['underlying']=value
                if ((stop.get('lower') is not None and value<=stop['lower']) or
                    (stop.get('upper') is not None and value>=stop['upper'])):
                    p['exit_signal']='EXIT NOW: underlying stop crossed; directional view invalidated'
            return self.journal.update(trade['id'],p,close=request.get('close') is True)

    def mark_all(self):
        """Fresh marks for every open trade; one failing trade does not stop the rest."""
        marked=[];errors=[]
        for trade in self.journal.paper():
            if trade['status']!='open': continue
            try:
                marked.append(self.mark_paper(dict(id=trade['id'])))
            except ValueError as exc:
                errors.append(dict(id=trade['id'],symbol=trade['payload']['symbol'],error=str(exc)))
                if any(w in str(exc).lower() for w in ('login','access denied','rate limit','connection failed')): break
        alerts=[dict(id=t['id'],symbol=t['payload']['symbol'],signal=t['payload']['exit_signal'],net_pnl=t['payload']['mark']['net_pnl'])
                for t in marked if t['payload'].get('exit_signal')!='Monitor']
        return dict(marked=len(marked),errors=errors,alerts=alerts,time=clock().isoformat())

    def score_forecasts(self):
        scored=0
        for row in self.journal.forecasts():
            if row['outcome'] or row['due']>=str(clock().date()): continue
            f=row['payload']
            data=self.market._get('https://api-t1.fyers.in/data/history',dict(symbol=f"NSE:{row['symbol']}-EQ",
                resolution='D',date_format='1',range_from=row['due'],range_to=row['due'],cont_flag='1'),True)
            candles=data.get('candles',[])
            if not candles: continue
            valid=[r for r in candles if str(datetime.fromtimestamp(r[0],clock().tzinfo).date())==row['due']]
            if not valid: continue
            actual=valid[-1][4];pred=f['summary']['median_end'];start=f['last_close']
            correct=(actual-start)*(pred-start)>0 if pred!=start and actual!=start else None
            self.journal.score(row['id'],dict(actual_close=actual,observed_date=row['due'],direction_correct=correct,
                predicted_return_pct=(pred/start-1)*100,actual_return_pct=(actual/start-1)*100))
            scored+=1
        return dict(scored=scored)
