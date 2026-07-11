from dataclasses import dataclass, field
from typing import List, Optional
from enum import Enum


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"


class ExitReason(str, Enum):
    TP = "tp"
    SL = "sl"
    BE = "breakeven"
    TRAIL = "trail"
    OPPOSITE_BX = "opposite_bx"
    MAX_BARS = "max_bars"
    PARTIAL_TP = "partial_tp"


class Mode(str, Enum):
    PAPER = "paper"
    DEMO = "demo"
    LIVE = "live"


@dataclass(frozen=True)
class Bar:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self):
        if self.high < self.low:
            raise ValueError(f"Bar high {self.high} < low {self.low}")
        if self.high < self.open or self.high < self.close:
            raise ValueError(f"Bar high {self.high} below open/close")
        if self.low > self.open or self.low > self.close:
            raise ValueError(f"Bar low {self.low} above open/close")


@dataclass
class SymbolConfig:
    tf: str
    leverage: int
    notional: float
    min_tp_pct: float
    min_sl_pct: float
    tp_atr_mult: float
    sl_atr_mult: float
    confirmation_bars: int
    breakeven_bars: int
    trail_after_be: float
    max_bars: int
    adx_max: float
    adx_trend_max: float
    rsi2_oversold: float
    rsi2_overbought: float
    hurst_max: float = 0.85
    partial_tp_enabled: bool = False
    partial_tp_pct: float = 0.5
    partial_tp_r: float = 1.0


@dataclass
class Position:
    symbol: str
    direction: Direction
    entry_price: float
    entry_bar_idx: int
    entry_ts: int
    notional: float
    leverage: int
    size: float
    initial_sl: float
    current_sl: float
    tp: float
    high_water: float
    low_water: float
    bars_held: int = 0
    partial_tp_hit: bool = False
    partial_tp_qty: float = 0.0
    entry_reason: str = ""

    @property
    def qty(self) -> float:
        return self.notional / self.entry_price

    def r_value(self, price: float) -> float:
        if self.direction == Direction.LONG:
            sl_dist = self.entry_price - self.initial_sl
        else:
            sl_dist = self.initial_sl - self.entry_price
        if sl_dist <= 0:
            return 0.0
        move = (price - self.entry_price) if self.direction == Direction.LONG else (self.entry_price - price)
        return move / sl_dist

    def update_water(self, bar: Bar):
        if self.direction == Direction.LONG:
            self.high_water = max(self.high_water, bar.high)
            self.low_water = min(self.low_water, bar.low)
        else:
            self.high_water = min(self.high_water, bar.high)
            self.low_water = max(self.low_water, bar.low)


@dataclass
class Trade:
    symbol: str
    direction: Direction
    entry_price: float
    entry_ts: int
    exit_price: float
    exit_ts: int
    qty: float
    notional: float
    leverage: int
    pnl_raw: float
    fees: float
    slippage: float
    funding: float
    pnl_net: float
    reason: ExitReason
    bars_held: int
    initial_sl: float
    tp: float
    partial_tp_hit: bool = False
    entry_reason: str = ""


@dataclass
class StrategyParams:
    fvb_length: int = 8
    fvb_band_mult: float = 1.0
    bxt_l1: int = 5
    bxt_l2: int = 30
    bxt_l3: int = 5
    bxt_ll1: int = 30
    bxt_ll2: int = 8
    hurst_window: int = 100
    adx_period: int = 14
    rsi_period: int = 14
    rsi2_period: int = 2
    atr_period: int = 14


@dataclass
class FeeConfig:
    maker_pct: float = 0.02
    taker_pct: float = 0.06
    slippage_pct: float = 0.05
    funding_pct_per_8h: float = 0.01

    def entry_cost(self, notional: float) -> float:
        """Taker fee on entry only (slippage is separate)."""
        return notional * self.taker_pct / 100

    def exit_cost(self, notional: float) -> float:
        """Taker fee on exit only (slippage is separate)."""
        return notional * self.taker_pct / 100

    def round_trip_slippage(self, notional: float) -> float:
        """Slippage on both entry and exit fills."""
        return 2 * notional * self.slippage_pct / 100

    def funding_cost(self, notional: float, bars: int, bar_minutes: int) -> float:
        periods_8h = (bars * bar_minutes) / 480
        return notional * self.funding_pct_per_8h * periods_8h / 100


@dataclass
class Indicators:
    n: int
    closes: List[float]
    highs: List[float]
    lows: List[float]
    volumes: List[float]
    fvb: List[Optional[float]]
    fvb_lower1: List[Optional[float]]
    fvb_lower2: List[Optional[float]]
    fvb_upper1: List[Optional[float]]
    fvb_upper2: List[Optional[float]]
    atr: List[Optional[float]]
    adx: List[Optional[float]]
    rsi2: List[Optional[float]]
    rsi14: List[Optional[float]]
    bxt_long: List[Optional[float]]
    bxt_short: List[Optional[float]]
    hurst: List[Optional[float]]
    mfi: List[Optional[float]]
    bb_upper: List[Optional[float]]
    bb_middle: List[Optional[float]]
    bb_lower: List[Optional[float]]
