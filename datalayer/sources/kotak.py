"""Kotak Neo adapter.

Historical candles and the scrip master only need the consumer key (no
TOTP/MPIN session), so this source runs without any interactive login.
Kotak caps daily history at 180 days per request and 5 years back.

Needs in `.env`:  KOTAK_NEO_CONSUMER_KEY   (optional: KOTAK_NEO_ENV=prod|uat)
"""
from __future__ import annotations

import csv
import os
from datetime import date, datetime

from .. import config
from ..base import DataSource, NoDataError
from ..util import cached_download, quiet


class KotakNeoSource(DataSource):
    name = "kotak"
    max_days_per_request = 180
    workers = 4
    requests_per_second = 4  # bursts above this get HTTP 429

    def __init__(self):
        super().__init__()
        self.client = None

    def authenticate(self):
        key = config.require(["KOTAK_NEO_CONSUMER_KEY"], "Kotak Neo")["KOTAK_NEO_CONSUMER_KEY"]
        # keep the SDK from spraying JSON error logs on screen / into a logs/ folder
        os.environ.setdefault("NEO_LOG_LEVEL", "CRITICAL")
        os.environ.setdefault("NEO_LOG_FILE_ENABLED", "false")
        from neo_api_client import NeoAPI

        with quiet():
            self.client = NeoAPI(consumer_key=key, environment=config.get("KOTAK_NEO_ENV", "prod"))

    def instruments(self) -> dict[str, str]:
        url = self.client.scrip_master(exchange_segment="nse_cm")
        if not isinstance(url, str):
            raise AuthError(f"Kotak scrip master unavailable: {url}")
        path = cached_download(url, "kotak_nse_cm.csv")
        out: dict[str, str] = {}
        with path.open(newline="", encoding="utf-8", errors="ignore") as f:
            for row in csv.DictReader(f):
                if row.get("pGroup") == "EQ" and (row.get("pTrdSymbol") or "").endswith("-EQ"):
                    out[row["pSymbolName"]] = row["pSymbol"]
        return out

    def benchmark_id(self) -> str | None:
        return "Nifty 50"

    def _fetch_range(self, ident: str, start: date, end: date) -> list[tuple]:
        with quiet():
            r = self.client.historical_data(
                neosymbol=f"nse_cm|{ident}", interval="D", from_date=start.isoformat(), to_date=end.isoformat()
            )
        if r.get("status") != "SUCCESS":
            msg = str((r.get("fault") or {}).get("message", r))
            if "No data" in msg or "older than" in msg:
                raise NoDataError
            raise RuntimeError(msg)
        candles = (r.get("data") or {}).get("candles") or []
        if not candles:
            raise NoDataError
        return [
            (datetime.fromisoformat(c[0][:10]).date(), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5] or 0))
            for c in candles
        ]
