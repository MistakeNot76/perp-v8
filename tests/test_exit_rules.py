"""CRITICAL: tests for exit rules. These are the bug-killers."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from core.models import Bar, Position, Direction, SymbolConfig
from core.exit_rules import check_bar_exit, compute_tp_sl, update_sl_on_water
from core.validator import validate_exit_price, ValidationError


def make_sym_cfg(**overrides) -> SymbolConfig:
    defaults = dict(
        tf="5m",
        leverage=15,
        notional=100,
        min_tp_pct=15.0,
        min_sl_pct=6.0,
        tp_atr_mult=2.0,
        sl_atr_mult=1.5,
        confirmation_bars=6,
        breakeven_bars=8,
        trail_after_be=1.0,
        max_bars=200,
        adx_max=30,
        adx_trend_max=35,
        rsi2_oversold=10,
        rsi2_overbought=90,
    )
    defaults.update(overrides)
    return SymbolConfig(**defaults)


def make_bar(close=100.0, high=101.0, low=99.0, ts=0) -> Bar:
    return Bar(ts=ts, open=close, high=high, low=low, close=close, volume=100)


def make_position(direction, entry=100.0, sl=94.0, tp=115.0, high_water=None, low_water=None, bars_held=0):
    if direction == Direction.LONG:
        hw = high_water if high_water is not None else entry
        lw = low_water if low_water is not None else entry
    else:
        hw = high_water if high_water is not None else entry
        lw = low_water if low_water is not None else entry
    return Position(
        symbol="TESTUSDT",
        direction=direction,
        entry_price=entry,
        entry_bar_idx=0,
        entry_ts=0,
        notional=100,
        leverage=15,
        size=1.0,
        initial_sl=sl,
        current_sl=sl,
        tp=tp,
        high_water=hw,
        low_water=lw,
        bars_held=bars_held,
    )


def test_tp_hit_long():
    pos = make_position(Direction.LONG, entry=100, tp=110, sl=94)
    bar = make_bar(close=109, high=111, low=108)
    decision = check_bar_exit(pos, bar, make_sym_cfg())
    assert decision.should_exit
    assert decision.reason.value == "tp"
    assert 108 <= decision.exit_price <= 111


def test_sl_hit_long():
    pos = make_position(Direction.LONG, entry=100, tp=110, sl=94)
    bar = make_bar(close=93, high=94, low=92)
    decision = check_bar_exit(pos, bar, make_sym_cfg())
    assert decision.should_exit
    assert decision.reason.value == "sl"
    assert 92 <= decision.exit_price <= 94


def test_breakeven_long():
    pos = make_position(Direction.LONG, entry=100, tp=110, sl=94, bars_held=10, high_water=100.5)
    bar = make_bar(close=99.5, high=100, low=99)
    decision = check_bar_exit(pos, bar, make_sym_cfg(breakeven_bars=8, trail_after_be=1.0))
    assert decision.should_exit
    assert decision.reason.value == "breakeven"
    assert decision.exit_price == 100.0


def test_trail_giveback_long():
    pos = make_position(Direction.LONG, entry=100, tp=120, sl=94, bars_held=10, high_water=110)
    bar = make_bar(close=108, high=109, low=107)
    pos.current_sl = update_sl_on_water(pos, make_sym_cfg(breakeven_bars=8, trail_after_be=1.0))
    assert pos.current_sl == 110 * 0.99


def test_no_fabricated_exit_prices():
    pos = make_position(Direction.LONG, entry=100, tp=110, sl=94, bars_held=10, high_water=100.5)
    bar = make_bar(close=99.5, high=100.0, low=99.0)
    decision = check_bar_exit(pos, bar, make_sym_cfg(breakeven_bars=8, trail_after_be=1.0))
    if decision.should_exit:
        try:
            validate_exit_price(decision.exit_price, bar, "TESTUSDT")
        except ValidationError:
            pytest.fail("Exit price outside bar range")


def test_short_tp_hit():
    pos = make_position(Direction.SHORT, entry=100, tp=90, sl=106)
    bar = make_bar(close=91, high=92, low=89)
    decision = check_bar_exit(pos, bar, make_sym_cfg())
    assert decision.should_exit
    assert decision.reason.value == "tp"


def test_short_sl_hit():
    pos = make_position(Direction.SHORT, entry=100, tp=90, sl=106)
    bar = make_bar(close=107, high=108, low=106)
    decision = check_bar_exit(pos, bar, make_sym_cfg())
    assert decision.should_exit
    assert decision.reason.value == "sl"


def test_max_bars_exit():
    pos = make_position(Direction.LONG, entry=100, tp=110, sl=94, bars_held=199, high_water=100.0)
    bar = make_bar(close=101, high=102, low=100)
    decision = check_bar_exit(pos, bar, make_sym_cfg(max_bars=200, breakeven_bars=999))
    assert decision.should_exit
    assert decision.reason.value == "max_bars"
    assert 100 <= decision.exit_price <= 102


def test_trail_does_not_lower_sl_long():
    pos = make_position(Direction.LONG, entry=100, tp=120, sl=94, bars_held=10, high_water=110)
    sym = make_sym_cfg(breakeven_bars=8, trail_after_be=1.0)
    pos.current_sl = update_sl_on_water(pos, sym)
    sl_after_up = pos.current_sl
    pos.high_water = 105
    pos.current_sl = update_sl_on_water(pos, sym)
    assert pos.current_sl <= sl_after_up


def test_trail_anchored_to_high_water():
    """The original bug: SL jumped ABOVE high_water. This must NEVER happen."""
    pos = make_position(Direction.LONG, entry=100, tp=120, sl=94, bars_held=10, high_water=100.5)
    sym = make_sym_cfg(breakeven_bars=8, trail_after_be=1.0)
    pos.current_sl = update_sl_on_water(pos, sym)
    assert pos.current_sl <= pos.high_water


def test_compute_tp_sl_with_atr_long():
    """ATR-driven TP/SL for a long position."""
    sym = make_sym_cfg(min_tp_pct=2.0, min_sl_pct=1.0, tp_atr_mult=2.0, sl_atr_mult=1.5)
    atr = 1.5  # $1.50 ATR on a $100 asset
    tp, sl = compute_tp_sl(Direction.LONG, 100.0, atr, sym)
    # tp_dist = max(100*0.02, 1.5*2.0) = max(2.0, 3.0) = 3.0 → tp=103.0
    # sl_dist = max(100*0.01, 1.5*1.5) = max(1.0, 2.25) = 2.25 → sl=97.75
    assert tp == pytest.approx(103.0)
    assert sl == pytest.approx(97.75)


def test_compute_tp_sl_with_atr_short():
    """ATR-driven TP/SL for a short position."""
    sym = make_sym_cfg(min_tp_pct=2.0, min_sl_pct=1.0, tp_atr_mult=2.0, sl_atr_mult=1.5)
    atr = 1.5
    tp, sl = compute_tp_sl(Direction.SHORT, 100.0, atr, sym)
    # tp = 100 - 3.0 = 97.0, sl = 100 + 2.25 = 102.25
    assert tp == pytest.approx(97.0)
    assert sl == pytest.approx(102.25)


def test_compute_tp_sl_floor_wins_over_atr():
    """When ATR is tiny, the percentage floor should win."""
    sym = make_sym_cfg(min_tp_pct=5.0, min_sl_pct=3.0, tp_atr_mult=2.0, sl_atr_mult=1.5)
    atr = 0.1  # tiny ATR
    tp, sl = compute_tp_sl(Direction.LONG, 100.0, atr, sym)
    # tp_dist = max(100*0.05, 0.1*2.0) = max(5.0, 0.2) = 5.0 → tp=105.0
    # sl_dist = max(100*0.03, 0.1*1.5) = max(3.0, 0.15) = 3.0 → sl=97.0
    assert tp == pytest.approx(105.0)
    assert sl == pytest.approx(97.0)


def test_compute_tp_sl_atr_none_fallback():
    """When ATR is None (warmup), fall back to percentage-only."""
    sym = make_sym_cfg(min_tp_pct=2.0, min_sl_pct=1.0)
    tp, sl = compute_tp_sl(Direction.LONG, 100.0, None, sym)
    assert tp == pytest.approx(102.0)
    assert sl == pytest.approx(99.0)


def test_compute_tp_sl_atr_zero_fallback():
    """When ATR is zero, fall back to percentage-only."""
    sym = make_sym_cfg(min_tp_pct=2.0, min_sl_pct=1.0)
    tp, sl = compute_tp_sl(Direction.LONG, 100.0, 0.0, sym)
    assert tp == pytest.approx(102.0)
    assert sl == pytest.approx(99.0)


def test_validator_rejects_phantom_price():
    bar = make_bar(high=100, low=90)
    with pytest.raises(ValidationError):
        validate_exit_price(101, bar, "TEST")
    with pytest.raises(ValidationError):
        validate_exit_price(89, bar, "TEST")


def test_validator_accepts_real_price():
    bar = make_bar(high=100, low=90)
    validate_exit_price(95, bar, "TEST")
    validate_exit_price(90, bar, "TEST")
    validate_exit_price(100, bar, "TEST")
