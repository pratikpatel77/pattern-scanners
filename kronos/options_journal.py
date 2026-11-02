"""Durable options records, independent of historical research and scanner files."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import uuid


def stamp(): return datetime.now(timezone.utc).isoformat()
def encode(value): return json.dumps(value,allow_nan=False)


CANDIDATE_FIELDS=('id','name','bias','status','reasons','score','entry_credit','max_loss','max_profit',
                  'stressed_mean_net','scenario_profit_pct','short_touch_pct','expiry')


def summarise(value):
    """Small list row: no sampled paths, payoff curves or quotes."""
    f=value.get('forecast')
    row={k:v for k,v in value.items() if k not in ('forecast','candidates')}
    row['forecast_summary']=dict(last_close=f.get('last_close'),asof=f.get('asof'),
        **{k:(f.get('summary') or {}).get(k) for k in ('bias','median_end','sample_count')}) if f else None
    row['candidates']=[{k:c.get(k) for k in CANDIDATE_FIELDS} for c in value.get('candidates',[])]
    return row


def paper_stats(trades):
    """Realised results use closing marks only; open marks are snapshots, never realised P&L."""
    opened=[t for t in trades if t['status']=='open']
    closed=sorted((t for t in trades if t['status']=='closed' and t['payload'].get('mark')),key=lambda t:t['updated'])
    pnl=[t['payload']['mark']['net_pnl'] for t in closed]
    wins=[x for x in pnl if x>0];losses=[x for x in pnl if x<=0]
    curve=[];total=peak=drawdown=0.
    for t,x in zip(closed,pnl):
        total+=x;peak=max(peak,total);drawdown=max(drawdown,peak-total)
        curve.append(dict(time=t['updated'],symbol=t['payload']['symbol'],strategy=t['payload']['plan']['name'],
                          pnl=round(x,2),cumulative=round(total,2)))
    groups={}
    for t,x in zip(closed,pnl):
        for key in (t['payload']['plan']['name'],'Research trades' if t['payload'].get('research') else 'Standard trades'):
            g=groups.setdefault(key,dict(name=key,trades=0,wins=0,pnl=0.,risk=0.))
            g['trades']+=1;g['wins']+=x>0;g['pnl']=round(g['pnl']+x,2);g['risk']+=t['payload']['plan']['max_loss']
    held=[(datetime.fromisoformat(t['updated'])-datetime.fromisoformat(t['created'])).total_seconds()/86400 for t in closed]
    risk=sum(t['payload']['plan']['max_loss'] for t in closed)
    marked=[t for t in opened if t['payload'].get('mark')]
    return dict(
        open=dict(count=len(opened),risk=round(sum(t['payload']['plan']['max_loss'] for t in opened),2),
                  unrealised=round(sum(t['payload']['mark']['net_pnl'] for t in marked),2),unmarked=len(opened)-len(marked),
                  alerts=sum(1 for t in opened if t['payload'].get('exit_signal') not in (None,'Monitor'))),
        closed=dict(count=len(closed),wins=len(wins),losses=len(losses),
                    win_rate=round(len(wins)/len(closed)*100,1) if closed else None,
                    realised=round(sum(pnl),2),avg_win=round(sum(wins)/len(wins),2) if wins else None,
                    avg_loss=round(sum(losses)/len(losses),2) if losses else None,
                    profit_factor=round(sum(wins)/-sum(losses),2) if wins and sum(losses)<0 else None,
                    expectancy=round(sum(pnl)/len(pnl),2) if pnl else None,
                    best=max(pnl,default=None),worst=min(pnl,default=None),max_drawdown=round(drawdown,2),
                    return_on_risk_pct=round(sum(pnl)/risk*100,1) if risk else None,
                    avg_days_held=round(sum(held)/len(held),1) if held else None),
        curve=curve,
        groups=sorted(({**g,'win_rate':round(g['wins']/g['trades']*100,1),'risk':round(g['risk'],2)} for g in groups.values()),
                      key=lambda g:g['pnl'],reverse=True),
        note='Paper results from fresh bid/ask marks (buys at ask, sells at bid) and estimated charges. Small samples are not evidence of an edge.')


class OptionsJournal:
    def __init__(self,store):
        self.store=store
        with store.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS options_settings (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS options_references (symbol TEXT PRIMARY KEY, payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS options_analyses (id TEXT PRIMARY KEY, created TEXT NOT NULL, symbol TEXT NOT NULL, payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS options_forecasts (id TEXT PRIMARY KEY, created TEXT NOT NULL, symbol TEXT NOT NULL, due TEXT NOT NULL, payload TEXT NOT NULL, outcome TEXT);
              CREATE TABLE IF NOT EXISTS options_paper (id TEXT PRIMARY KEY, created TEXT NOT NULL, updated TEXT NOT NULL, status TEXT NOT NULL, analysis TEXT NOT NULL, candidate TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(analysis,candidate));
            ''')
            if 'summary' not in {r[1] for r in db.execute('PRAGMA table_info(options_analyses)')}:
                db.execute('ALTER TABLE options_analyses ADD COLUMN summary TEXT')
            # One-time backfill so list views never parse full forecast payloads again.
            for key,payload in db.execute("SELECT id,payload FROM options_analyses WHERE summary IS NULL "
                                          "AND COALESCE(json_extract(payload,'$.is_draft'),0)=0").fetchall():
                db.execute('UPDATE options_analyses SET summary=? WHERE id=?',(encode(summarise(json.loads(payload))),key))
        with store.connect() as db:
            # Paper trades are permanent records: the database itself refuses to delete them.
            db.execute('''CREATE TRIGGER IF NOT EXISTS options_paper_no_delete BEFORE DELETE ON options_paper
                          BEGIN SELECT RAISE(ABORT, 'Paper trades are permanent records and cannot be deleted.'); END''')
        self.backup_dir = Path(store.path).parent/'paper_backup'
        self._restore_if_empty()
        self._backup()
        self.prune_drafts()

    def _backup(self):
        """Readable copy of every paper trade, rewritten after each change (latest, plus one file per day)."""
        try:
            rows=[dict(id=t['id'],created=t['created'],updated=t['updated'],status=t['status'],
                       analysis=t['analysis'],candidate=t['candidate'],payload=t['payload']) for t in self.paper()]
            if not rows: return  # never overwrite a good backup with an empty list
            self.backup_dir.mkdir(parents=True,exist_ok=True)
            text=json.dumps(rows,indent=1,allow_nan=False)
            for name in ('paper_trades.json',f"paper_trades_{datetime.now().date()}.json"):
                temp=self.backup_dir/(name+'.tmp')
                with open(temp,'w',encoding='utf-8') as f:
                    f.write(text);f.flush();os.fsync(f.fileno())
                temp.replace(self.backup_dir/name)
        except (OSError,ValueError):
            pass  # the database copy is authoritative; a failed backup must never block a trade

    def _restore_if_empty(self):
        """If the database was lost or replaced, bring every trade back from the last backup."""
        source=self.backup_dir/'paper_trades.json'
        with self.store.connect() as db:
            if db.execute('SELECT 1 FROM options_paper LIMIT 1').fetchone() or not source.exists(): return
            try: rows=json.loads(source.read_text(encoding='utf-8'))
            except (OSError,ValueError): return
            for r in rows:
                db.execute('INSERT OR IGNORE INTO options_paper VALUES (?,?,?,?,?,?,?)',
                           (r['id'],r['created'],r['updated'],r['status'],r['analysis'],r['candidate'],encode(r['payload'])))

    def settings(self):
        with self.store.connect() as db:
            row=db.execute('SELECT payload FROM options_settings WHERE id=1').fetchone()
        return json.loads(row[0]) if row else {}

    def save_settings(self,values):
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO options_settings VALUES (1,?)',(encode(values),))

    def reference(self,symbol,values=None):
        with self.store.connect() as db:
            if values is not None:
                db.execute('INSERT OR REPLACE INTO options_references VALUES (?,?)',(symbol,encode(values)))
            row=db.execute('SELECT payload FROM options_references WHERE symbol=?',(symbol,)).fetchone()
        return json.loads(row[0]) if row else {}

    def forecast(self,value):
        key=uuid.uuid4().hex
        with self.store.connect() as db:
            db.execute('INSERT INTO options_forecasts VALUES (?,?,?,?,?,NULL)',
                       (key,stamp(),value['symbol'],value['forecast'][-1]['date'],encode(value)))
        return key

    def forecasts(self):
        with self.store.connect() as db:
            rows=db.execute('SELECT * FROM options_forecasts ORDER BY created DESC').fetchall()
        return [{**dict(r),'payload':json.loads(r['payload']),'outcome':json.loads(r['outcome']) if r['outcome'] else None} for r in rows]

    def score(self,key,outcome):
        with self.store.connect() as db:
            db.execute('UPDATE options_forecasts SET outcome=? WHERE id=? AND outcome IS NULL',(encode(outcome),key))

    def save_analysis(self,value,key=None):
        """A fixed key replaces that row: repriced drafts keep one row per idea."""
        key=key or uuid.uuid4().hex
        value={**value,'id':key,'created':stamp()}
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO options_analyses (id,created,symbol,payload,summary) VALUES (?,?,?,?,?)',
                       (key,value['created'],value['symbol'],encode(value),None if value.get('is_draft') else encode(summarise(value))))
        return value

    def analysis(self,key):
        with self.store.connect() as db:
            row=db.execute('SELECT payload FROM options_analyses WHERE id=?',(key,)).fetchone()
            value=json.loads(row[0]) if row else None
            # Drafts reference the parent's forecast instead of copying its sampled paths.
            if value and 'forecast' not in value and value.get('parent_analysis'):
                parent=db.execute("SELECT json_extract(payload,'$.forecast') FROM options_analyses WHERE id=?",
                                  (value['parent_analysis'],)).fetchone()
                if parent and parent[0]: value['forecast']=json.loads(parent[0])
        if value is None: raise ValueError('Analysis not found.')
        return value

    def prune_drafts(self,days=2):
        """Old repriced drafts are disposable unless a paper trade was priced from one."""
        cutoff=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
        with self.store.connect() as db:
            removed=db.execute("""DELETE FROM options_analyses WHERE json_extract(payload,'$.is_draft')=1 AND created<?
                AND id NOT IN (SELECT json_extract(payload,'$.priced_analysis') FROM options_paper
                               WHERE json_extract(payload,'$.priced_analysis') IS NOT NULL)""",(cutoff,)).rowcount
        return removed

    def analyses(self):
        """Newest first, as compact summaries. Load one analysis by id for its forecast and payoffs."""
        with self.store.connect() as db:
            rows=db.execute("SELECT summary FROM options_analyses WHERE summary IS NOT NULL ORDER BY created DESC LIMIT 1000").fetchall()
        return [json.loads(r[0]) for r in rows]

    def latest_forecast_id(self,symbol):
        """Newest saved analysis with a forecast for one stock; kept until a newer run replaces it."""
        with self.store.connect() as db:
            row=db.execute("""SELECT id FROM options_analyses WHERE symbol=? AND summary IS NOT NULL
                              AND json_extract(summary,'$.forecast_summary') IS NOT NULL
                              ORDER BY created DESC LIMIT 1""",(symbol,)).fetchone()
        return row[0] if row else None

    def paper(self,key=None):
        with self.store.connect() as db:
            rows=db.execute('SELECT * FROM options_paper WHERE id=?',(key,)).fetchall() if key else db.execute('SELECT * FROM options_paper ORDER BY created DESC').fetchall()
        result=[{**dict(r),'payload':json.loads(r['payload'])} for r in rows]
        if key:
            if not result: raise ValueError('Paper trade not found.')
            return result[0]
        return result

    def open(self,analysis,candidate,payload):
        key=uuid.uuid4().hex
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows=db.execute("SELECT payload FROM options_paper WHERE status='open'").fetchall()
            opened=[json.loads(r[0]) for r in rows]
            if any(p['symbol']==payload['symbol'] for p in opened):
                raise ValueError('A paper trade is already open for this stock.')
            if db.execute('SELECT id FROM options_paper WHERE analysis=? AND candidate=?',(analysis,candidate)).fetchone():
                raise ValueError('This idea has already been paper traded.')
            db.execute('INSERT INTO options_paper VALUES (?,?,?,?,?,?,?)',
                       (key,stamp(),stamp(),'open',analysis,candidate,encode(payload)))
        self._backup()
        return self.paper(key)

    def update(self,key,payload,close=False):
        mark=payload.get('mark')
        if mark:
            history=[*payload.get('marks',[]),dict(time=mark.get('time'),net_pnl=mark['net_pnl'],underlying=mark.get('underlying'))]
            payload={**payload,'marks':history[-500:]}
        with self.store.connect() as db:
            changed=db.execute("UPDATE options_paper SET updated=?,status=?,payload=? WHERE id=? AND status='open'",
                (stamp(),'closed' if close else 'open',encode(payload),key)).rowcount
            if changed!=1: raise ValueError('This paper trade is already closed.')
        self._backup()
        return self.paper(key)
