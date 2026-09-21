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

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

_LEGACY = [
    PROJECT_ROOT.parent / "definedge-api" / ".env",
    PROJECT_ROOT.parent / "kotak-neo-api" / ".env",
    PROJECT_ROOT.parent / "fyers- api- non-trading" / ".env",
]

_loaded = False


def load() -> None:
    """Load .env (once). Values already in the real environment always win."""
    global _loaded
    if _loaded:
        return
    load_dotenv(ENV_FILE, override=False)
    for legacy in _LEGACY:
        if legacy.exists():
            load_dotenv(legacy, override=False)
    _loaded = True


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
