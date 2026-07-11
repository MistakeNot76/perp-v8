"""
Exit rules. GIVEBACK-FROM-PEAK trail only.

Every exit price must be within the bar's high-low range.
This is the bug-killer: no phantom prices, ever.
"""
from dataclasses import dataclass
from typing import Optional

from core.models import Position, Bar, Direction, ExitReason


@dataclass
class ExitDecision:
    should_exit: bool
    reason: Optional[ExitReason] = None
    exit_price: Optional[float] = None
    new_sl: Optional[float] = None
    partial_exit_qty: float = 0.0


def compute_tp_sl(
    direction: Direction,
    entry_price: float,
    atr: Optional[float],
    sym_cfg,
) -> tuple:
    """Initial TP/SL at entry time. ATR-driven with min percentage floors.

    tp_distance = max(entry * min_tp_pct/100, atr * tp_atr_mult)
    sl_distance = max(entry * min_sl_pct/100, atr * sl_atr_mult)

    If ATR is None or zero (warmup), falls back to percentage-only.
    """
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
    """
    GIVEBACK-FROM-PEAK trail. trail_sl = high_water * (1 - pct/100).
    Anchored to real exchange prices via the bar's high/low.

    No step-based offsets. No max(1, n_steps). No fabricated prices.
    """
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


def check_bar_exit(
    position: Position,
    bar: Bar,
    sym_cfg,
) -> ExitDecision:
    """
    Check if position should exit on this bar.
    Exit price is ALWAYS clamped to the bar's [low, high] range.
    """
    position.update_water(bar)
    position.bars_held += 1

    new_sl = update_sl_on_water(position, sym_cfg)
    position.current_sl = new_sl

    if position.direction == Direction.LONG:
        if bar.low <= position.current_sl:
            exit_price = min(position.current_sl, bar.high)
            exit_price = max(exit_price, bar.low)
            if position.current_sl >= position.entry_price:
                return ExitDecision(True, ExitReason.BE, exit_price)
            return ExitDecision(True, ExitReason.SL, exit_price)
        if bar.high >= position.tp:
            exit_price = min(position.tp, bar.high)
            exit_price = max(exit_price, bar.low)
            return ExitDecision(True, ExitReason.TP, exit_price)
    else:
        if bar.high >= position.current_sl:
            exit_price = max(position.current_sl, bar.low)
            exit_price = min(exit_price, bar.high)
            if position.current_sl <= position.entry_price:
                return ExitDecision(True, ExitReason.BE, exit_price)
            return ExitDecision(True, ExitReason.SL, exit_price)
        if bar.low <= position.tp:
            exit_price = max(position.tp, bar.low)
            exit_price = min(exit_price, bar.high)
            return ExitDecision(True, ExitReason.TP, exit_price)

    if position.bars_held >= sym_cfg.max_bars:
        exit_price = bar.close
        exit_price = max(exit_price, bar.low)
        exit_price = min(exit_price, bar.high)
        return ExitDecision(True, ExitReason.MAX_BARS, exit_price)

    return ExitDecision(False)
