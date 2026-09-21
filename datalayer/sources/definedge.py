"""Definedge Integrate adapter.

Login is built in: TOTP, fully automatic, no prompts. Needs in `.env`:
    DEFINEDGE_API_TOKEN, DEFINEDGE_API_SECRET, DEFINEDGE_TOTP_SECRET
"""
from __future__ import annotations

import io
import zipfile
from datetime import date, datetime

import pyotp
import requests

from .. import config
from ..base import AuthError, DataSource, NoDataError
from ..util import cached_download

LOGIN_URL = "https://signin.definedgesecurities.com/auth/realms/debroking/dsbpkc"
MASTER_URL = "https://app.definedgesecurities.com/public/allmaster.zip"
HISTORY_URL = "https://data.definedgesecurities.com/sds/history/NSE/{token}/day/{frm}/{to}"
DATE_FMT = "%d%m%Y%H%M"


class DefinedgeSource(DataSource):
    name = "definedge"
    max_days_per_request = 3650
    workers = 12
    requests_per_second = 15

    def __init__(self):
        super().__init__()
        self.session_key = None

    def authenticate(self):
        cred = config.require(["DEFINEDGE_API_TOKEN", "DEFINEDGE_API_SECRET", "DEFINEDGE_TOTP_SECRET"], "Definedge")
        try:
            r1 = requests.get(f"{LOGIN_URL}/login/{cred['DEFINEDGE_API_TOKEN']}",
                              headers={"api_secret": cred["DEFINEDGE_API_SECRET"]}, timeout=20)
            r1.raise_for_status()
            otp = pyotp.TOTP(cred["DEFINEDGE_TOTP_SECRET"]).now()
            r2 = requests.post(f"{LOGIN_URL}/token", json={"otp_token": r1.json()["otp_token"], "otp": otp}, timeout=20)
            r2.raise_for_status()
            self.session_key = r2.json()["api_session_key"]
        except (requests.RequestException, KeyError, ValueError) as e:
            raise AuthError(f"Definedge login failed ({e}). Check the DEFINEDGE_* values in .env "
                            "and that this computer's clock is correct (TOTP needs it).") from e

    def instruments(self) -> dict[str, str]:
        path = cached_download(MASTER_URL, "definedge_allmaster.zip")
        out: dict[str, str] = {}
        with zipfile.ZipFile(path) as z, z.open("allmaster.csv") as f:
            for raw in io.TextIOWrapper(f, encoding="utf-8", errors="ignore"):
                parts = raw.strip().split(",")
                if len(parts) >= 5 and parts[0] == "NSE" and parts[4] == "EQ":
                    out[parts[2]] = parts[1]
        return out

    def benchmark_id(self) -> str | None:
        return "26000"

    def _fetch_range(self, ident: str, start: date, end: date) -> list[tuple]:
        url = HISTORY_URL.format(
            token=ident,
            frm=datetime.combine(start, datetime.min.time()).strftime(DATE_FMT),
            to=datetime.combine(end, datetime.max.time()).strftime(DATE_FMT),
        )
        r = requests.get(url, headers={"Authorization": self.session_key}, timeout=30)
        r.raise_for_status()
        rows = []
        for line in r.text.strip().splitlines():
            f = line.split(",")
            if len(f) < 5:
                continue
            vol = float(f[5]) if len(f) > 5 and f[5] else 0.0
            rows.append((datetime.strptime(f[0][:8], "%d%m%Y").date(), float(f[1]), float(f[2]), float(f[3]), float(f[4]), vol))
        if not rows:
            raise NoDataError
        return rows
