"""
Entry signal evaluation. Pure functions, no I/O.

A signal triggers when ALL conditions are met for the given direction.
"""
from typing import List, Optional
from dataclasses import dataclass

from core.models import Indicators, Direction


@dataclass
class EntrySignal:
    direction: Direction
    bar_idx: int
    reason: str


def _crossover_above(bxt: List[Optional[float]], lookback: int) -> bool:
    """True if bxt crossed above zero within last `lookback` bars."""
    if bxt is None or len(bxt) < 2:
        return False
    for j in range(1, min(lookback, len(bxt))):
        if bxt[-j] is None or bxt[-j - 1] is None:
            continue
        if bxt[-j] > 0 and bxt[-j - 1] <= 0:
            return True
    return False


def _crossover_below(bxt: List[Optional[float]], lookback: int) -> bool:
    if bxt is None or len(bxt) < 2:
        return False
    for j in range(1, min(lookback, len(bxt))):
        if bxt[-j] is None or bxt[-j - 1] is None:
            continue
        if bxt[-j] < 0 and bxt[-j - 1] >= 0:
            return True
    return False


def check_entry(
    indicators: Indicators,
    bar_idx: int,
    sym_cfg,
) -> Optional[EntrySignal]:
    """
    Check if there's an entry signal at bar_idx.
    Returns EntrySignal or None.

    Long: price below fvb_lower1, ADX<adx_max, Hurst<=0.6, RSI(2)<oversold, BX_short crossed above 0
    Short: price above fvb_upper1, ADX<adx_max, Hurst<=0.6, RSI(2)>overbought, BX_long crossed below 0
    """
    if bar_idx < sym_cfg.confirmation_bars + 1:
        return None
    if bar_idx >= indicators.n:
        return None

    close = indicators.closes[bar_idx]
    fvb_l1 = indicators.fvb_lower1[bar_idx]
    fvb_u1 = indicators.fvb_upper1[bar_idx]
    adx_v = indicators.adx[bar_idx]
    hurst_v = indicators.hurst[bar_idx]
    rsi2_v = indicators.rsi2[bar_idx]
    bx_short = indicators.bxt_short[bar_idx]
    bx_long = indicators.bxt_long[bar_idx]

    if fvb_l1 is None or adx_v is None:
        return None
    if adx_v > sym_cfg.adx_max:
        return None
    if hurst_v is not None and hurst_v > sym_cfg.hurst_max:
        return None

    if close < fvb_l1:
        if rsi2_v is not None and rsi2_v < sym_cfg.rsi2_oversold:
            bx_hist = indicators.bxt_short[max(0, bar_idx - sym_cfg.confirmation_bars): bar_idx + 1]
            if _crossover_above(bx_hist, sym_cfg.confirmation_bars):
                return EntrySignal(
                    direction=Direction.LONG,
                    bar_idx=bar_idx,
                    reason="FVB_below_lower1+RSI2_oversold+BX_short_cross_up",
                )

    if fvb_u1 is not None and close > fvb_u1:
        if rsi2_v is not None and rsi2_v > sym_cfg.rsi2_overbought:
            bx_hist = indicators.bxt_long[max(0, bar_idx - sym_cfg.confirmation_bars): bar_idx + 1]
            if _crossover_below(bx_hist, sym_cfg.confirmation_bars):
                return EntrySignal(
                    direction=Direction.SHORT,
                    bar_idx=bar_idx,
                    reason="FVB_above_upper1+RSI2_overbought+BX_long_cross_dn",
                )

    return None
