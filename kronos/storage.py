"""SQLite run journal and a single strategy row, with atomic writes."""
from datetime import datetime, timezone
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import uuid
from .settings import Settings


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS strategy (
                    id INTEGER PRIMARY KEY CHECK(id=1), settings TEXT NOT NULL, updated TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                    created TEXT NOT NULL, updated TEXT NOT NULL, request TEXT NOT NULL,
                    progress REAL NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
                    result TEXT, error TEXT);
            ''')
            db.execute("INSERT OR IGNORE INTO strategy VALUES (1,?,?)", (json.dumps(Settings().to_dict()), now()))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def settings(self):
        with self.connect() as db:
            return Settings.from_dict(json.loads(db.execute('SELECT settings FROM strategy WHERE id=1').fetchone()[0]))

    def save_settings(self, settings):
        with self.connect() as db:
            db.execute('UPDATE strategy SET settings=?, updated=? WHERE id=1', (json.dumps(settings.to_dict()), now()))

    def create(self, kind, request):
        key, stamp = uuid.uuid4().hex, now()
        with self.connect() as db:
            db.execute('INSERT INTO runs (id,kind,status,created,updated,request,message) VALUES (?,?,?,?,?,?,?)',
                       (key, kind, 'queued', stamp, stamp, json.dumps(request, allow_nan=False), 'Waiting to start'))
        return key

    def update(self, key, **values):
        if not values or set(values) - {'status', 'progress', 'message', 'result', 'error'}:
            raise ValueError('Invalid run update')
        if 'result' in values:
            values['result'] = json.dumps(values['result'], allow_nan=False)
        values['updated'] = now()
        with self.connect() as db:
            db.execute('UPDATE runs SET ' + ','.join(k+'=?' for k in values) + ' WHERE id=?', [*values.values(), key])

    def get(self, key):
        with self.connect() as db:
            row = db.execute('SELECT * FROM runs WHERE id=?', (key,)).fetchone()
        if row is None:
            raise KeyError('Run not found')
        result = dict(row)
        for name in ('request', 'result'):
            result[name] = json.loads(result[name]) if result[name] else None
        return result

    def list(self, limit=100):
        with self.connect() as db:
            rows = db.execute('SELECT id,kind,status,created,updated,request,progress,message,error FROM runs ORDER BY created DESC LIMIT ?', (limit,)).fetchall()
        return [{**dict(r), 'request': json.loads(r['request'])} for r in rows]

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE runs SET status='interrupted', message='App closed before this run finished', updated=? WHERE status IN ('running','queued','cancelling')", (now(),))
