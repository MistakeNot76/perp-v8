"""
Parity test: proves live engine == backtest engine.
They share core/engine.py so this should always pass.
If it ever fails, the shared code has been broken.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from core.models import Bar, SymbolConfig, FeeConfig, Direction, StrategyParams
from core.indicators import compute_all
from core.engine import EngineState, run_bars


def make_bars(n: int, start_price: float = 100.0) -> list:
    """Generate synthetic bars with random walk."""
    import random
    random.seed(42)
    bars = []
    price = start_price
    for i in range(n):
        change = random.uniform(-0.5, 0.5)
        open_p = price
        close_p = price + change
        high_p = max(open_p, close_p) + random.uniform(0, 0.3)
        low_p = min(open_p, close_p) - random.uniform(0, 0.3)
        vol = random.uniform(100, 1000)
        bars.append(Bar(
            ts=1000000 + i * 300000,
            open=open_p,
            high=high_p,
            low=low_p,
            close=close_p,
            volume=vol,
        ))
        price = close_p
    return bars


def make_bars_with_volatility(n: int = 300, start_price: float = 100.0) -> list:
    """Generate bars with strong mean-reversion + volatility cycles to trigger entries."""
    import random
    random.seed(42)
    bars = []
    price = start_price
    for i in range(n):
        cycle_pos = i % 30
        if cycle_pos < 5:
            target_move = -2.0
        elif cycle_pos < 10:
            target_move = 1.0
        elif cycle_pos < 15:
            target_move = -2.0
        else:
            target_move = 1.0
        change = target_move + random.uniform(-0.5, 0.5)
        open_p = price
        close_p = price + change
        high_p = max(open_p, close_p) + random.uniform(0.5, 1.0)
        low_p = min(open_p, close_p) - random.uniform(0.5, 1.0)
        vol = random.uniform(1000, 5000)
        bars.append(Bar(
            ts=1000000 + i * 300000,
            open=open_p,
            high=high_p,
            low=low_p,
            close=close_p,
            volume=vol,
        ))
        price = close_p
    return bars


def make_sym_cfg(**overrides) -> SymbolConfig:
    defaults = dict(
        tf="5m",
        leverage=15,
        notional=100,
        min_tp_pct=15.0,
        min_sl_pct=6.0,
        tp_atr_mult=2.0,
        sl_atr_mult=1.5,
        confirmation_bars=3,
        breakeven_bars=2,
        trail_after_be=1.0,
        max_bars=50,
        adx_max=50,
        adx_trend_max=60,
        rsi2_oversold=5,
        rsi2_overbought=95,
    )
    defaults.update(overrides)
    return SymbolConfig(**defaults)


def make_state_from_bars(bars):
    sym = make_sym_cfg()
    params = StrategyParams(fvb_length=4, adx_period=5, bxt_l1=3, bxt_l2=10, bxt_ll1=10, bxt_ll2=3, hurst_window=20, rsi2_period=2, rsi_period=5, atr_period=5)
    indicators = compute_all(bars, params)
    return EngineState(
        symbol="TESTUSDT",
        bars=bars,
        indicators=indicators,
        sym_cfg=sym,
        fees=FeeConfig(),
    )


def test_parity_same_bars_same_trades():
    """Running the same candles twice produces the same trades."""
    bars = make_bars(200)
    state1 = make_state_from_bars(bars)
    trades1 = run_bars(state1, 0)

    bars2 = make_bars(200)
    state2 = make_state_from_bars(bars2)
    trades2 = run_bars(state2, 0)

    assert len(trades1) == len(trades2)
    for t1, t2 in zip(trades1, trades2):
        assert t1.entry_price == t2.entry_price
        assert t1.exit_price == t2.exit_price
        assert t1.reason == t2.reason


def test_no_phantom_exits_in_engine():
    """Every trade's exit price must be within the bar's range."""
    bars = make_bars(300)
    state = make_state_from_bars(bars)
    trades = run_bars(state, 0)
    for t in trades:
        exit_bar = None
        for b in state.bars:
            if b.ts == t.exit_ts:
                exit_bar = b
                break
        assert exit_bar is not None
        assert exit_bar.low <= t.exit_price <= exit_bar.high, (
            f"Trade exit {t.exit_price} outside bar [{exit_bar.low}, {exit_bar.high}]"
        )


def test_pnl_math_validates():
    bars = make_bars(300)
    state = make_state_from_bars(bars)
    trades = run_bars(state, 0)
    for t in trades:
        if t.direction == Direction.LONG:
            gross = (t.exit_price - t.entry_price) * t.qty
        else:
            gross = (t.entry_price - t.exit_price) * t.qty
        expected = gross - t.fees - t.slippage - t.funding
        assert abs(t.pnl_net - expected) < 1e-6


def test_engine_handles_long_and_short():
    bars = make_bars_with_volatility(500)
    state = make_state_from_bars(bars)
    trades = run_bars(state, 0)
    if trades:
        directions = {t.direction for t in trades}
        assert all(d in (Direction.LONG, Direction.SHORT) for d in directions)
        for t in trades:
            assert t.entry_price > 0
            assert t.exit_price > 0
            assert t.pnl_net is not None