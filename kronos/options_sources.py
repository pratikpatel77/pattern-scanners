"""Public, read-only context feeds. Failures never mean zero IVP or no events."""
from datetime import timedelta, datetime
from html.parser import HTMLParser
import json
import math
import threading
import pandas as pd
import requests
from .options_market import clock

IV_URL='https://equitytimer.com/derivatives/iv-grid/'
NSE_URL='https://www.nseindia.com/companies-listing/corporate-filings-event-calendar'
SB_EVENTS='https://api.sensibull.com/v1/current_events'


class PayloadParser(HTMLParser):
    def __init__(self): super().__init__();self.payload=None
    def handle_starttag(self,tag,attrs):
        value=dict(attrs).get('data-payload')
        if value:
            p=json.loads(value)
            if isinstance(p,dict) and 'date' in p and isinstance(p.get('rows'),list):self.payload=p


def parse_iv(html):
    parser=PayloadParser();parser.feed(html)
    if parser.payload is None:raise ValueError('IVP table format changed')
    p=parser.payload
    date=str(pd.Timestamp(p['date']).date())
    rows={}
    for row in p['rows']:
        val=row.get('ivp');n=row.get('n')
        if isinstance(val,(int,float)) and math.isfinite(val) and 0<=val<=100 and isinstance(n,int):
            rows[str(row['s']).upper()]=dict(value=round(val,2),sessions=n)
    if not rows:raise ValueError('IVP table contains no valid values')
    return dict(date=date,rows=rows)


def parse_events(rows,kind,source):
    if not isinstance(rows,list):raise ValueError('Unexpected calendar format')
    result=[]
    for row in rows:
        symbol=row.get('symbol') or row.get('bm_symbol') or row.get('single_stock')
        raw=row.get('event_date') or row.get('date') or row.get('bm_date') or row.get('exDate')
        if not symbol or not raw:continue
        try:date=str(datetime.fromisoformat(raw[:10]).date()) if len(raw)>=10 and raw[4]=='-' and raw[7]=='-' else str(pd.to_datetime(raw,dayfirst=True).date())
        except (ValueError,TypeError):continue
        title=row.get('purpose') or row.get('bm_purpose') or row.get('subject') or row.get('title') or kind
        if title=='stock_results':title='Financial results'
        result.append(dict(symbol=str(symbol).upper(),date=date,title=str(title),
                           detail=str(row.get('bm_desc') or ''),source=source))
    return result


class ContextSources:
    def __init__(self,store):
        self.store=store;self.lock=threading.RLock();self.failures={}
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS options_source_cache (name TEXT PRIMARY KEY, fetched TEXT NOT NULL, payload TEXT NOT NULL)')

    def get(self,name,loader):
        with self.lock:
            with self.store.connect() as db:
                row=db.execute('SELECT fetched,payload FROM options_source_cache WHERE name=?',(name,)).fetchone()
            if row and row['fetched'][:10]==str(clock().date()):
                return json.loads(row['payload'])
            if self.failures.get(name,0)>clock().timestamp():raise ValueError('Source temporarily unavailable')
            try:
                value=loader();value['fetched_at']=clock().isoformat()
                with self.store.connect() as db:
                    db.execute('INSERT OR REPLACE INTO options_source_cache VALUES (?,?,?)',
                               (name,value['fetched_at'],json.dumps(value,allow_nan=False)))
                return value
            except (requests.RequestException,ValueError,KeyError,TypeError):
                self.failures[name]=clock().timestamp()+300
                raise ValueError('Source unavailable; retry in five minutes') from None

    @staticmethod
    def fetch(url):
        response=requests.get(url,headers={'User-Agent':'Mozilla/5.0','Referer':'https://www.nseindia.com/'},timeout=(5,15))
        response.raise_for_status();return response

    def ivp(self,symbol):
        try:
            p=self.get('equitytimer_ivp',lambda:parse_iv(self.fetch(IV_URL).text))
            row=p['rows'].get(symbol)
            if not row:raise ValueError('Stock absent')
            date=pd.Timestamp(p['date']).date();today=clock().date()
            lag=len(pd.bdate_range(date,today))-1
            valid=0<=(today-date).days<=5 and lag<=1 and row['sessions']>=60
            return dict(**row,value_raw=row['value'],source=IV_URL,date=p['date'],fetched_at=p['fetched_at'],
                        usable=valid,status='EOD IVP' if valid else 'Insufficient history or outdated source',
                        note='EquityTimer EOD; may differ from Sensibull. '+('Previous session. ' if date<today else '')+
                             f"{row['sessions']} history observations. Sensibull automatic data access was unavailable.")
        except ValueError:
            return dict(value=None,source=IV_URL,date=None,sessions=0,usable=False,
                        status='IVP source unavailable',note='Automatic fallback unavailable. Add a dated manual reading.')

    def calendar(self,symbol,end):
        specs=[('nse_events','https://www.nseindia.com/api/event-calendar?index=equities','Board meeting / results'),
               ('nse_actions','https://www.nseindia.com/api/corporates-corporateActions?index=equities','Corporate action'),
               ('sensibull_events',SB_EVENTS,'Financial results')]
        rows=[];statuses=[]
        for name,url,kind in specs:
            try:
                def load():
                    data=self.fetch(url).json()
                    if name=='sensibull_events':
                        if data.get('status') is not True:raise ValueError('Calendar response failed')
                        data=data['data']['single_stock']
                    return dict(rows=parse_events(data,kind,url))
                value=self.get(name,load);rows.extend(value['rows'])
                statuses.append(dict(name=name,ok=True,source=url,fetched_at=value['fetched_at']))
            except ValueError:statuses.append(dict(name=name,ok=False,source=url,error='Calendar unavailable'))
        today=str(clock().date());until=str(clock().date()+timedelta(days=90))
        dedup={}
        for e in rows:
            if e['symbol']!=symbol or not today<=e['date']<=until:continue
            title=e['title'];key=(e['date'],'results' if 'result' in title.lower() else title.lower())
            if key in dedup:dedup[key]['sources'].append(e['source']);continue
            dedup[key]={**e,'sources':[e['source']],
                        'days_away':(pd.Timestamp(e['date']).date()-clock().date()).days,'during_trade':e['date']<=end}
        upcoming=sorted(dedup.values(),key=lambda r:(r['date'],r['title']))
        complete=all(s['ok'] for s in statuses[:2])
        return dict(checked=complete,source=NSE_URL,events=[e for e in upcoming if e['during_trade']],
            upcoming=upcoming,sources=statuses,fetched_at=clock().isoformat(),
            coverage='NSE meetings/results and actions; Sensibull results fallback. '+
                     ('Calendars checked; dates can change.' if complete else 'Partial coverage: some calendars unavailable.'))
