"""Tests for Hermes P1 residuals: tf parsing, slippage round-trip, fvb API."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from core.timeframes import tf_to_minutes, bars_per_day
from core.models import FeeConfig, Bar, Direction, SymbolConfig, ExitReason
from core.indicators import fvb, vwap
from core.engine import EngineState, open_position, close_position
from core.entry_rules import EntrySignal
from core.indicators import Indicators


def test_tf_to_minutes_common():
    assert tf_to_minutes("5m") == 5
    assert tf_to_minutes("15m") == 15
    assert tf_to_minutes("30m") == 30
    assert tf_to_minutes("1h") == 60
    assert tf_to_minutes("4h") == 240
    assert tf_to_minutes("1d") == 1440


def test_tf_to_minutes_case_and_whitespace():
    assert tf_to_minutes(" 15M ") == 15
    assert tf_to_minutes("1H") == 60


def test_tf_to_minutes_invalid():
    with pytest.raises(ValueError, match="Unknown timeframe"):
        tf_to_minutes("xyz")
    with pytest.raises(ValueError, match="Unknown timeframe"):
        tf_to_minutes("")


def test_bars_per_day():
    assert bars_per_day("1m") == 1440
    assert bars_per_day("5m") == 288
    assert bars_per_day("15m") == 96
    assert bars_per_day("30m") == 48
    assert bars_per_day("1h") == 24
    assert bars_per_day("4h") == 6
    assert bars_per_day("1d") == 1


def test_round_trip_slippage_is_two_sides():
    fees = FeeConfig(taker_pct=0.06, slippage_pct=0.05)
    notional = 1000.0
    assert fees.round_trip_slippage(notional) == pytest.approx(1.0)
    assert fees.entry_cost(notional) == pytest.approx(0.6)
    assert fees.exit_cost(notional) == pytest.approx(0.6)
    # Fees must NOT include slippage
    assert fees.entry_cost(notional) == notional * fees.taker_pct / 100


def test_close_position_uses_round_trip_slippage():
    fees = FeeConfig(taker_pct=0.06, slippage_pct=0.05, funding_pct_per_8h=0.0)
    bar = Bar(ts=1, open=100, high=101, low=99, close=100, volume=10)
    exit_bar = Bar(ts=2, open=100, high=110, low=99, close=105, volume=10)
    sym = SymbolConfig(
        tf="15m", leverage=15, notional=1000,
        min_tp_pct=2.0, min_sl_pct=1.0, tp_atr_mult=2.0, sl_atr_mult=1.5,
        confirmation_bars=1, breakeven_bars=8, trail_after_be=1.0, max_bars=200,
        adx_max=50, adx_trend_max=60, rsi2_oversold=10, rsi2_overbought=90,
    )
    # Minimal indicators stub for open_position ATR lookup
    n = 2
    ind = Indicators(
        n=n, closes=[100, 105], highs=[101, 110], lows=[99, 99], volumes=[10, 10],
        fvb=[100, 100], fvb_lower1=[95, 95], fvb_lower2=[90, 90],
        fvb_upper1=[105, 105], fvb_upper2=[110, 110],
        atr=[1.0, 1.0], adx=[20, 20], rsi2=[5, 50], rsi14=[50, 50],
        bxt_long=[0, 0], bxt_short=[0, 0], hurst=[0.5, 0.5], mfi=[50, 50],
        bb_upper=[110, 110], bb_middle=[100, 100], bb_lower=[90, 90],
    )
    state = EngineState(
        symbol="TESTUSDT",
        bars=[bar, exit_bar],
        indicators=ind,
        sym_cfg=sym,
        fees=fees,
        bar_minutes=15,
    )
    signal = EntrySignal(direction=Direction.LONG, bar_idx=0, reason="test")
    open_position(state, signal)
    trade = close_position(state, 105.0, exit_bar, ExitReason.TP)
    assert trade.slippage == pytest.approx(1.0)  # 2 * 0.05% * 1000
    expected_fees = fees.entry_cost(1000) + fees.exit_cost(1000)
    assert trade.fees == pytest.approx(expected_fees)
    gross = (105.0 - 100.0) * trade.qty
    assert trade.pnl_net == pytest.approx(gross - trade.fees - trade.slippage - trade.funding)


def test_fvb_equals_vwap_regardless_of_length():
    bars = [
        Bar(ts=i * 60_000, open=100 + i, high=102 + i, low=99 + i, close=101 + i, volume=10 + i)
        for i in range(8)
    ]
    assert fvb(bars, length=2) == fvb(bars, length=20)
    assert fvb(bars, length=8) == vwap(bars)
