"""Tests for FVB revert, opposite BXT, and partial TP exits."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.models import Bar, Position, Direction, SymbolConfig, Indicators, ExitReason
from core.exit_rules import check_bar_exit
from core.engine import EngineState, open_position, step, partial_close_position
from core.entry_rules import EntrySignal
from core.models import FeeConfig
from core.indicators import align_series_to_bars, bxt


def _sym(**kw) -> SymbolConfig:
    d = dict(
        tf="15m", leverage=15, notional=100,
        min_tp_pct=50.0, min_sl_pct=6.0, tp_atr_mult=10.0, sl_atr_mult=1.5,
        confirmation_bars=3, breakeven_bars=999, trail_after_be=1.0, max_bars=200,
        adx_max=50, adx_trend_max=60, rsi2_oversold=10, rsi2_overbought=90,
        use_fixed_tp=False, use_trail=False,
        fvb_exit_enabled=False, fvb_exit_target="vwap",
        bxt_exit_same_tf_enabled=False, bxt_exit_ltf_enabled=False,
        partial_tp_enabled=False, partial_tp_pct=0.5, partial_tp_r=1.0,
    )
    d.update(kw)
    return SymbolConfig(**d)


def _pos(direction=Direction.LONG, entry=100.0, sl=94.0, tp=200.0):
    return Position(
        symbol="T", direction=direction, entry_price=entry, entry_bar_idx=0, entry_ts=0,
        notional=100, leverage=15, size=1.0, initial_sl=sl, current_sl=sl, tp=tp,
        high_water=entry, low_water=entry, bars_held=0,
    )


def _ind(n, **kw) -> Indicators:
    closes = kw.pop("closes", [100.0] * n)
    base = dict(
        n=n, closes=closes, highs=[c + 1 for c in closes], lows=[c - 1 for c in closes],
        volumes=[10.0] * n,
        fvb=[100.0] * n, fvb_lower1=[95.0] * n, fvb_lower2=[90.0] * n,
        fvb_upper1=[105.0] * n, fvb_upper2=[110.0] * n,
        atr=[1.0] * n, adx=[20.0] * n, rsi2=[50.0] * n, rsi14=[50.0] * n,
        bxt_long=[0.0] * n, bxt_short=[0.0] * n, hurst=[0.5] * n, mfi=[50.0] * n,
        bb_upper=[110.0] * n, bb_middle=[100.0] * n, bb_lower=[90.0] * n,
        bxt_exit_long=[0.0] * n, bxt_ltf_long=[0.0] * n,
    )
    base.update(kw)
    return Indicators(**base)


def test_fvb_revert_vwap_long():
    n = 5
    ind = _ind(n, fvb=[100.0] * n, closes=[90, 92, 95, 99, 101])
    pos = _pos()
    bar = Bar(ts=4, open=100, high=102, low=100, close=101, volume=1)
    d = check_bar_exit(pos, bar, _sym(fvb_exit_enabled=True, fvb_exit_target="vwap"), ind, 4)
    assert d.should_exit and d.reason == ExitReason.FVB_REVERT


def test_fvb_revert_inner_long_earlier_than_vwap():
    n = 5
    # close back above inner lower1 (95) but still below vwap (100)
    ind = _ind(n, fvb=[100.0] * n, fvb_lower1=[95.0] * n, closes=[90, 92, 94, 96, 96])
    pos = _pos()
    bar = Bar(ts=4, open=95, high=97, low=95, close=96, volume=1)
    d_inner = check_bar_exit(
        pos, bar, _sym(fvb_exit_enabled=True, fvb_exit_target="inner"), ind, 4
    )
    assert d_inner.should_exit and d_inner.reason == ExitReason.FVB_REVERT

    pos2 = _pos()
    d_vwap = check_bar_exit(
        pos2, bar, _sym(fvb_exit_enabled=True, fvb_exit_target="vwap"), ind, 4
    )
    assert not d_vwap.should_exit


def test_opposite_bxt_same_tf_long():
    n = 6
    # bearish cross on last bar
    bxt_exit = [1.0, 1.0, 1.0, 0.5, 0.2, -0.3]
    ind = _ind(n, bxt_exit_long=bxt_exit)
    pos = _pos()
    bar = Bar(ts=5, open=100, high=101, low=99, close=100, volume=1)
    d = check_bar_exit(
        pos, bar,
        _sym(bxt_exit_same_tf_enabled=True, bxt_exit_confirmation_bars=3),
        ind, 5,
    )
    assert d.should_exit and d.reason == ExitReason.OPPOSITE_BX


def test_opposite_bxt_ltf_short():
    n = 6
    # bullish cross against short
    bxt_ltf = [-1.0, -0.5, -0.2, -0.1, 0.05, 0.4]
    ind = _ind(n, bxt_ltf_long=bxt_ltf)
    pos = _pos(direction=Direction.SHORT, entry=100, sl=106, tp=50)
    bar = Bar(ts=5, open=100, high=101, low=99, close=100, volume=1)
    d = check_bar_exit(
        pos, bar,
        _sym(bxt_exit_ltf_enabled=True, bxt_ltf_confirmation_bars=3),
        ind, 5,
    )
    assert d.should_exit and d.reason == ExitReason.OPPOSITE_BX_LTF


def test_partial_tp_scales_out():
    fees = FeeConfig(taker_pct=0.0, slippage_pct=0.0, funding_pct_per_8h=0.0)
    n = 3
    bars = [
        Bar(ts=0, open=100, high=100, low=100, close=100, volume=1),
        Bar(ts=1, open=100, high=107, low=100, close=106, volume=1),
        Bar(ts=2, open=106, high=106, low=105, close=105, volume=1),
    ]
    ind = _ind(n, closes=[100, 106, 105])
    sym = _sym(
        partial_tp_enabled=True, partial_tp_pct=0.5, partial_tp_r=1.0,
        use_fixed_tp=False, fvb_exit_enabled=False,
        min_sl_pct=6.0,  # SL at 94 → 1R = 106
    )
    state = EngineState("T", bars, ind, sym, fees, bar_minutes=15)
    open_position(state, EntrySignal(Direction.LONG, 0, "test"))
    # Force SL distance known
    state.open_position.initial_sl = 94.0
    state.open_position.current_sl = 94.0
    step(state, 1)
    assert state.open_position is not None
    assert state.open_position.partial_tp_hit
    assert abs(state.open_position.size - 0.5) < 1e-9
    assert len(state.closed_trades) == 1
    assert state.closed_trades[0].reason == ExitReason.PARTIAL_TP


def test_align_ltf_series():
    htf = [
        Bar(ts=0, open=1, high=1, low=1, close=1, volume=1),
        Bar(ts=15_000, open=1, high=1, low=1, close=1, volume=1),
        Bar(ts=30_000, open=1, high=1, low=1, close=1, volume=1),
    ]
    ltf = [
        Bar(ts=0, open=1, high=1, low=1, close=1, volume=1),
        Bar(ts=5_000, open=1, high=1, low=1, close=1, volume=1),
        Bar(ts=10_000, open=1, high=1, low=1, close=1, volume=1),
        Bar(ts=20_000, open=1, high=1, low=1, close=1, volume=1),
        Bar(ts=25_000, open=1, high=1, low=1, close=1, volume=1),
    ]
    series = [1.0, 2.0, 3.0, 4.0, 5.0]
    aligned = align_series_to_bars(htf, ltf, series)
    assert aligned[0] == 1.0
    assert aligned[1] == 3.0  # last ltf ts<=15000 is 10000 → 3.0
    assert aligned[2] == 5.0
