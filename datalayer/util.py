"""Shared paths and small helpers for the data layer."""
from __future__ import annotations

import contextlib
import io
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
API_ROOT = PROJECT_ROOT.parent  # folder holding the per-broker login projects
CACHE_DIR = PROJECT_ROOT / ".cache"
DAY = 86400


def cached_download(url: str, filename: str, max_age: float = DAY, headers: dict | None = None) -> Path:
    """Download `url` into .cache/<filename>, reusing it while younger than max_age.
    Falls back to a stale copy if the download fails."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / filename
    if path.exists() and time.time() - path.stat().st_mtime < max_age:
        return path
    try:
        r = requests.get(url, headers=headers, timeout=60)
        r.raise_for_status()
        path.write_bytes(r.content)
    except requests.RequestException:
        if not path.exists():
            raise
    return path


@contextlib.contextmanager
def quiet():
    """Swallow stdout from broker login helpers (some print session keys)."""
    with contextlib.redirect_stdout(io.StringIO()):
        yield
