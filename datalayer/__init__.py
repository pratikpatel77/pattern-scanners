"""Common data layer: one interface over every broker / data vendor.

    from datalayer import get_source, BarStore, resample

    src = get_source("kotak"); src.authenticate()
    bars = BarStore(src).load_many({"RELIANCE": src.instruments()["RELIANCE"]})
    weekly = resample(bars["RELIANCE"], "W")

Adding a broker = one file in datalayer/sources implementing DataSource,
plus one line in SOURCES below.
"""
from .base import BENCHMARK, AuthError, DataSource
from .store import BarStore
from .timeframe import DAILY, WEEKLY, resample, to_weekly

SOURCES = {
    "definedge": ("datalayer.sources.definedge", "DefinedgeSource"),
    "kotak": ("datalayer.sources.kotak", "KotakNeoSource"),
    "fyers": ("datalayer.sources.fyers", "FyersSource"),
    "local": ("datalayer.sources.local", "LocalFilesSource"),
}


def get_source(name: str, **kwargs) -> DataSource:
    import importlib

    if name not in SOURCES:
        raise ValueError(f"unknown source {name!r}; choose from {', '.join(SOURCES)}")
    module, cls = SOURCES[name]
    return getattr(importlib.import_module(module), cls)(**kwargs)


__all__ = ["AuthError", "BENCHMARK", "BarStore", "DAILY", "DataSource", "SOURCES", "WEEKLY", "get_source", "resample", "to_weekly"]
