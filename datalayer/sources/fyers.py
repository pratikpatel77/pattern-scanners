"""Fyers v3 adapter.

Needs in `.env`:  FYERS_APP_ID, FYERS_ACCESS_TOKEN
Fyers access tokens expire every day -- refresh with:  python tools/fyers_login.py
"""
from __future__ import annotations

import csv
from datetime import date, datetime, time as dtime

from .. import config
from ..base import AuthError, DataSource, NoDataError
from ..util import CACHE_DIR, cached_download

SYMBOL_MASTER_URL = "https://public.fyers.in/sym_details/NSE_CM.csv"


class FyersSource(DataSource):
    name = "fyers"
    max_days_per_request = 365
    workers = 8
    requests_per_second = 8  # Fyers throttles hard above ~10/s app-wide

    def __init__(self):
        super().__init__()
        self.client = None

    def authenticate(self):
        cred = config.require(["FYERS_APP_ID", "FYERS_ACCESS_TOKEN"], "Fyers")
        from fyers_apiv3 import fyersModel

        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.client = fyersModel.FyersModel(
            client_id=cred["FYERS_APP_ID"], token=cred["FYERS_ACCESS_TOKEN"], is_async=False, log_path=str(CACHE_DIR)
        )
        profile = self.client.get_profile()
        if profile.get("s") != "ok":
            raise AuthError(
                f"Fyers token rejected ({profile.get('message')}). Tokens expire daily -- "
                "get a fresh one with:  python tools/fyers_login.py"
            )

    def instruments(self) -> dict[str, str]:
        path = cached_download(SYMBOL_MASTER_URL, "fyers_nse_cm.csv")
        out: dict[str, str] = {}
        with path.open(newline="", encoding="utf-8", errors="ignore") as f:
            for row in csv.reader(f):
                if len(row) >= 14 and row[9].endswith("-EQ"):
                    out[row[13]] = row[9]
        return out

    def benchmark_id(self) -> str | None:
        return "NSE:NIFTY50-INDEX"

    def _fetch_range(self, ident: str, start: date, end: date) -> list[tuple]:
        data = {
            "symbol": ident,
            "resolution": "D",
            "date_format": "0",
            "range_from": str(int(datetime.combine(start, dtime.min).timestamp())),
            "range_to": str(int(datetime.combine(end, dtime.max).timestamp())),
            "cont_flag": "1",
        }
        r = self.client.history(data=data)
        if r.get("s") == "no_data":
            raise NoDataError
        if r.get("s") != "ok":
            raise RuntimeError(r.get("message") or r)
        candles = r.get("candles") or []
        if not candles:
            raise NoDataError
        return [(datetime.fromtimestamp(ts).date(), float(o), float(h), float(l), float(c), float(v)) for ts, o, h, l, c, v in candles]
