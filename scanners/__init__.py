"""Scanner registry. Add a scanner = add a module with NAME/TITLE/DESCRIPTION/PARAMS/scan and list it here.

REGISTRY holds the bullish (pre-breakout) scanners. The Kronos AI breakout tools
assume an upward breakout, so they only use these. BEAR_REGISTRY holds the
mirrored pre-breakdown scanners; ALL_SCANNERS is both, for the scanner report.
"""
from . import cup_handle, darvas, stage
from .bearish import BEARISH

REGISTRY = {m.NAME: m for m in (stage, darvas, cup_handle)}
BEAR_REGISTRY = {m.NAME: m for m in BEARISH}
ALL_SCANNERS = {**REGISTRY, **BEAR_REGISTRY}


def direction_of(name: str) -> str:
    """'long' for bullish scanners, 'short' for bearish ones."""
    return getattr(ALL_SCANNERS.get(name), "DIRECTION", "long")
