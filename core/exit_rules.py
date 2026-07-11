"""
Exit rules. Mechanical TP/SL/trail + optional FVB revert + opposite BXT.

Priority on each bar (first hit wins):
  1. Hard SL / trailed SL
  2. Partial TP at R-multiple (scale-out; does not full-close)
  3. FVB revert (vwap or inner band — selectable)
  4. Opposite BXT same-TF (faster lengths)
  5. Opposite BXT lower-TF (aligned series)
  6. Fixed full TP (if enabled)
  7. Max bars

Every exit price must be within the bar's high-low range.
"""
from dataclasses import dataclass
from typing import List, Optional

from core.models import Position, Bar, Direction, ExitReason, Indicators


@dataclass
class ExitDecision:
    should_exit: bool
    reason: Optional[ExitReason] = None
    exit_price: Optional[float] = None
    new_sl: Optional[float] = None
    partial_exit_qty: float = 0.0
    is_partial: bool = False


def _clamp(price: float, bar: Bar) -> float:
    return max(bar.low, min(price, bar.high))


def compute_tp_sl(
    direction: Direction,
    entry_price: float,
    atr: Optional[float],
    sym_cfg,
) -> tuple:
    """Initial TP/SL at entry time. ATR-driven with min percentage floors."""
    if atr is not None and atr > 0:
        tp_dist = max(entry_price * sym_cfg.min_tp_pct / 100, atr * sym_cfg.tp_atr_mult)
        sl_dist = max(entry_price * sym_cfg.min_sl_pct / 100, atr * sym_cfg.sl_atr_mult)
    else:
        tp_dist = entry_price * sym_cfg.min_tp_pct / 100
        sl_dist = entry_price * sym_cfg.min_sl_pct / 100

    if direction == Direction.LONG:
        tp = entry_price + tp_dist
        sl = entry_price - sl_dist
    else:
        tp = entry_price - tp_dist
        sl = entry_price + sl_dist
    return tp, sl


def update_sl_on_water(
    position: Position,
    sym_cfg,
) -> float:
    """GIVEBACK-FROM-PEAK trail. Disabled when use_trail is False."""
    if not getattr(sym_cfg, "use_trail", True):
        return position.current_sl
    if sym_cfg.breakeven_bars <= 0 or sym_cfg.trail_after_be <= 0:
        return position.current_sl
    if position.bars_held <= sym_cfg.breakeven_bars:
        return position.current_sl

    trail_pct = sym_cfg.trail_after_be / 100

    if position.direction == Direction.LONG:
        new_sl = position.high_water * (1 - trail_pct)
        new_sl = max(new_sl, position.entry_price)
        return max(position.current_sl, new_sl)
    else:
        new_sl = position.low_water * (1 + trail_pct)
        new_sl = min(new_sl, position.entry_price)
        return min(position.current_sl, new_sl)


def _crossover_above(series: List[Optional[float]], lookback: int) -> bool:
    if series is None or len(series) < 2:
        return False
    for j in range(1, min(lookback, len(series))):
        if series[-j] is None or series[-j - 1] is None:
            continue
        if series[-j] > 0 and series[-j - 1] <= 0:
            return True
    return False


def _crossover_below(series: List[Optional[float]], lookback: int) -> bool:
    if series is None or len(series) < 2:
        return False
    for j in range(1, min(lookback, len(series))):
        if series[-j] is None or series[-j - 1] is None:
            continue
        if series[-j] < 0 and series[-j - 1] >= 0:
            return True
    return False


def _fvb_revert_hit(
    position: Position,
    bar: Bar,
    indicators: Indicators,
    bar_idx: int,
    target: str,
) -> bool:
    """True when price has returned to the chosen fair-value target."""
    close = bar.close
    if target == "inner":
        if position.direction == Direction.LONG:
            band = indicators.fvb_lower1[bar_idx] if bar_idx < len(indicators.fvb_lower1) else None
            return band is not None and close >= band
        band = indicators.fvb_upper1[bar_idx] if bar_idx < len(indicators.fvb_upper1) else None
        return band is not None and close <= band
    mid = indicators.fvb[bar_idx] if bar_idx < len(indicators.fvb) else None
    if mid is None:
        return False
    if position.direction == Direction.LONG:
        return close >= mid
    return close <= mid


def _bxt_against(position: Position, series: List[Optional[float]], lookback: int) -> bool:
    """Long exits on bearish cross down; short exits on bullish cross up."""
    if not series or lookback < 1:
        return False
    if position.direction == Direction.LONG:
        return _crossover_below(series, lookback)
    return _crossover_above(series, lookback)


def check_bar_exit(
    position: Position,
    bar: Bar,
    sym_cfg,
    indicators: Optional[Indicators] = None,
    bar_idx: Optional[int] = None,
) -> ExitDecision:
    """
    Check if position should exit (or partially exit) on this bar.
    Exit price is ALWAYS clamped to the bar's [low, high] range.
    """
    position.update_water(bar)
    position.bars_held += 1

    new_sl = update_sl_on_water(position, sym_cfg)
    position.current_sl = new_sl

    # 1) Hard / trailed stop
    if position.direction == Direction.LONG:
        if bar.low <= position.current_sl:
            exit_price = _clamp(position.current_sl, bar)
            if position.current_sl > position.entry_price + 1e-12:
                return ExitDecision(True, ExitReason.TRAIL, exit_price)
            if position.current_sl >= position.entry_price:
                return ExitDecision(True, ExitReason.BE, exit_price)
            return ExitDecision(True, ExitReason.SL, exit_price)
    else:
        if bar.high >= position.current_sl:
            exit_price = _clamp(position.current_sl, bar)
            if position.current_sl < position.entry_price - 1e-12:
                return ExitDecision(True, ExitReason.TRAIL, exit_price)
            if position.current_sl <= position.entry_price:
                return ExitDecision(True, ExitReason.BE, exit_price)
            return ExitDecision(True, ExitReason.SL, exit_price)

    # 2) Partial TP at R-multiple (scale-out only once)
    if (
        getattr(sym_cfg, "partial_tp_enabled", False)
        and not position.partial_tp_hit
        and getattr(sym_cfg, "partial_tp_pct", 0) > 0
    ):
        r_mult = float(getattr(sym_cfg, "partial_tp_r", 1.0))
        if position.direction == Direction.LONG:
            sl_dist = position.entry_price - position.initial_sl
            target = position.entry_price + sl_dist * r_mult if sl_dist > 0 else None
            hit = target is not None and bar.high >= target
        else:
            sl_dist = position.initial_sl - position.entry_price
            target = position.entry_price - sl_dist * r_mult if sl_dist > 0 else None
            hit = target is not None and bar.low <= target
        if hit and target is not None:
            qty = position.size * float(sym_cfg.partial_tp_pct)
            if qty > 0 and qty < position.size:
                return ExitDecision(
                    should_exit=True,
                    reason=ExitReason.PARTIAL_TP,
                    exit_price=_clamp(target, bar),
                    partial_exit_qty=qty,
                    is_partial=True,
                )

    # Indicator-based exits
    if indicators is not None and bar_idx is not None and 0 <= bar_idx < indicators.n:
        # 3) FVB revert — target selectable: vwap | inner
        if getattr(sym_cfg, "fvb_exit_enabled", False):
            target = str(getattr(sym_cfg, "fvb_exit_target", "vwap")).lower()
            if target not in ("vwap", "inner"):
                target = "vwap"
            if _fvb_revert_hit(position, bar, indicators, bar_idx, target):
                return ExitDecision(True, ExitReason.FVB_REVERT, _clamp(bar.close, bar))

        # 4) Opposite BXT same-TF (faster)
        if getattr(sym_cfg, "bxt_exit_same_tf_enabled", False):
            lookback = int(getattr(sym_cfg, "bxt_exit_confirmation_bars", 2))
            series = indicators.bxt_exit_long or []
            window = series[max(0, bar_idx - lookback): bar_idx + 1]
            if window and _bxt_against(position, window, lookback):
                return ExitDecision(True, ExitReason.OPPOSITE_BX, _clamp(bar.close, bar))

        # 5) Opposite BXT lower-TF
        if getattr(sym_cfg, "bxt_exit_ltf_enabled", False):
            lookback = int(getattr(sym_cfg, "bxt_ltf_confirmation_bars", 2))
            series = indicators.bxt_ltf_long or []
            window = series[max(0, bar_idx - lookback): bar_idx + 1]
            if window and _bxt_against(position, window, lookback):
                return ExitDecision(True, ExitReason.OPPOSITE_BX_LTF, _clamp(bar.close, bar))

    # 6) Fixed full TP
    if getattr(sym_cfg, "use_fixed_tp", True):
        if position.direction == Direction.LONG:
            if bar.high >= position.tp:
                return ExitDecision(True, ExitReason.TP, _clamp(position.tp, bar))
        else:
            if bar.low <= position.tp:
                return ExitDecision(True, ExitReason.TP, _clamp(position.tp, bar))

    # 7) Time stop
    if position.bars_held >= sym_cfg.max_bars:
        return ExitDecision(True, ExitReason.MAX_BARS, _clamp(bar.close, bar))

    return ExitDecision(False)
