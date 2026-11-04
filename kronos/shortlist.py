"""Saved review notes, isolated from scanner rules, forecasts and paper trades."""
import math
import re

from .storage import now


class Shortlist:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS review_shortlist (
                symbol TEXT PRIMARY KEY, pinned INTEGER NOT NULL,
                note TEXT NOT NULL, updated TEXT NOT NULL)''')

    def entries(self):
        with self.store.connect() as db:
            rows = db.execute('SELECT * FROM review_shortlist ORDER BY symbol').fetchall()
        return {row['symbol']: {**dict(row), 'pinned': bool(row['pinned'])} for row in rows}

    def save(self, request):
        if not isinstance(request, dict) or set(request) - {'symbol', 'pinned', 'note'}:
            raise ValueError('Use a symbol, pin or note.')
        symbol = request.get('symbol')
        if not isinstance(symbol, str) or not re.fullmatch(r'[A-Z0-9&_-]{1,40}', symbol):
            raise ValueError('Choose a valid stock symbol.')
        if 'pinned' in request and type(request['pinned']) is not bool:
            raise ValueError('Pin must be true or false.')
        if 'note' in request and (not isinstance(request['note'], str) or len(request['note']) > 2000):
            raise ValueError('Keep the note within 2,000 characters.')
        # Partial updates prevent a pin click from overwriting notes from another tab.
        with self.store.connect() as db:
            db.execute('INSERT OR IGNORE INTO review_shortlist VALUES (?,0,?,?)', (symbol, '', now()))
            for key in ('pinned', 'note'):
                if key in request:
                    db.execute(f'UPDATE review_shortlist SET {key}=?, updated=? WHERE symbol=?',
                               (request[key], now(), symbol))
            row = dict(db.execute('SELECT * FROM review_shortlist WHERE symbol=?', (symbol,)).fetchone())
        row['pinned'] = bool(row['pinned'])
        return row


def review_setups(rows):
    """Small JSON-safe view. Preserve both directions and both timeframes."""
    result = []
    for source in rows:
        symbol = source.get('symbol', '')
        if not isinstance(symbol, str) or not re.fullmatch(r'[A-Z0-9&_-]{1,40}', symbol):
            continue
        row = {key: str(source.get(key, '')) for key in ('symbol', 'scanner', 'timeframe', 'date', 'label')}
        row['direction'] = 'short' if source.get('direction') == 'short' or row['scanner'].endswith('_bear') else 'long'
        for key in ('close', 'pivot', 'support', 'score'):
            value = source.get(key)
            row[key] = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None
        result.append(row)
    return result
