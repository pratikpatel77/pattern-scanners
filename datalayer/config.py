"""Credentials and settings, from a `.env` file in the project folder.

    copy .env.example .env      (then fill in the broker(s) you use)

Only the broker you actually scan with needs credentials. NSE delivery data,
`--offline` and `--source local` need none.

Backward compatibility: if the older per-broker folders sit next to this one
(`../definedge-api`, `../kotak-neo-api`, `../fyers- api- non-trading`), their
`.env` files are used as a fallback for any value missing here. A fresh
machine simply doesn't have them, and doesn't need them.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values
import threading

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

_LEGACY = [
    PROJECT_ROOT.parent.parent / "Logins" / "definedge-api" / ".env",
    PROJECT_ROOT.parent.parent / "Logins" / "kotak-neo-api" / ".env",
    PROJECT_ROOT.parent.parent / "Logins" / "fyers- api- non-trading" / ".env",
]

_loaded = False
_stamps = None
_managed = {}
_lock = threading.RLock()


def load() -> None:
    """Reload changed login files. Values from the real environment always win."""
    global _loaded, _stamps, _managed
    with _lock:
        paths = [ENV_FILE, *_LEGACY]
        stamps = tuple(p.stat().st_mtime_ns if p.exists() else None for p in paths)
        if _loaded and stamps == _stamps:
            return
        values = {}
        for path in paths:
            if path.exists():
                for key, value in dotenv_values(path).items():
                    if value is not None:
                        values.setdefault(key, value)
        # Only replace values that we loaded ourselves. Real environment wins.
        managed = {}
        for key, value in values.items():
            if key not in os.environ or (key in _managed and os.environ[key] == _managed[key]):
                os.environ[key] = value
                managed[key] = value
        for key, value in _managed.items():
            if key not in values and os.environ.get(key) == value:
                os.environ.pop(key, None)
        _managed, _stamps, _loaded = managed, stamps, True


def get(name: str, default: str = "") -> str:
    load()
    return (os.getenv(name) or default).strip()


def require(names: list[str], broker: str) -> dict[str, str]:
    """Return {name: value}; raise a readable error listing what is missing."""
    from .base import AuthError

    values = {n: get(n) for n in names}
    missing = [n for n, v in values.items() if not v]
    if missing:
        raise AuthError(
            f"{broker}: missing {', '.join(missing)}. Copy .env.example to .env "
            f"(in {PROJECT_ROOT}) and fill in your {broker} details."
        )
    return values
