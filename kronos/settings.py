"""One editable strategy profile; completed runs retain a settings snapshot."""
from dataclasses import asdict, dataclass, fields
import math


@dataclass(frozen=True)
class Settings:
    model: str = "small"
    timeframe: str = "D"
    horizon: int = 15
    lookback: int = 128
    samples: int = 32
    batch_size: int = 4
    seed: int = 42
    temperature: float = 1.0
    top_p: float = 0.9
    buffer_pct: float = 0.1
    confirmation_bars: int = 2
    target_r: float = 1.0
    min_target_pct: float = 5.0
    fee_bps: float = 5.0
    slippage_bps: float = 5.0
    min_success_rate: float = 50.0
    capital: float = 100000.0
    risk_pct: float = 1.0

    def __post_init__(self):
        if self.model not in ("mini", "small", "base") or self.timeframe not in ("D", "W"):
            raise ValueError("Choose a supported model and Daily or Weekly bars.")
        for name, lo, hi in (("horizon", 2, 60), ("lookback", 60, 512),
                             ("samples", 4, 128), ("batch_size", 1, 16),
                             ("seed", 0, 2**31-1), ("confirmation_bars", 1, 5)):
            value = getattr(self, name)
            if type(value) is not int or not lo <= value <= hi:
                raise ValueError(f"{name.replace('_', ' ').title()} must be an integer from {lo} to {hi}.")
        for name, lo, hi in (("temperature", .1, 2), ("top_p", .05, 1),
                             ("buffer_pct", 0, 5), ("target_r", .1, 5),
                             ("min_target_pct", 0, 30), ("fee_bps", 0, 100),
                             ("slippage_bps", 0, 100), ("min_success_rate", 0, 100),
                             ("capital", 100, 1e9), ("risk_pct", .1, 5)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
                raise ValueError(f"{name.replace('_', ' ').title()} must be from {lo} to {hi}.")
        if self.confirmation_bars > self.horizon:
            raise ValueError("Confirmation bars must fit within the forecast horizon.")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, values):
        if not isinstance(values, dict):
            raise ValueError("Settings must be an object.")
        unknown = set(values) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError("Unknown settings: " + ", ".join(sorted(unknown)))
        return cls(**values)

    def rules(self):
        from .core import BreakoutRules
        return BreakoutRules(self.buffer_pct, self.confirmation_bars, self.target_r, self.min_target_pct)
