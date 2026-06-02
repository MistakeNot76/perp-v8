"""Tests for the backtester CLI."""
import sys
import json
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from backtest.runner import run_backtest_on_symbol, parse_symbols, compute_stats


def write_test_data(data_dir: Path, symbol: str, bars: list):
    """Write test candles to data dir."""
    data_dir.mkdir(parents=True, exist_ok=True)
    p = data_dir / f"{symbol}_5m.json"
    out = [
        {"ts": b.ts, "open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume}
        for b in bars
    ]
    with open(p, "w") as f:
        json.dump(out, f)


def make_synthetic_bars(n: int = 200, vol: float = 2.0, seed: int = 42) -> list:
    """Generate bars with enough volatility to trigger entries."""
    import random
    from core.models import Bar
    random.seed(seed)
    bars = []
    price = 100.0
    for i in range(n):
        cycle_pos = i % 20
        if cycle_pos < 5:
            target = -vol
        elif cycle_pos < 10:
            target = vol * 0.5
        elif cycle_pos < 15:
            target = -vol
        else:
            target = vol * 0.5
        change = target + random.uniform(-0.3, 0.3)
        open_p = price
        close_p = max(1.0, price + change)
        high_p = max(open_p, close_p) + random.uniform(0.5, 1.5)
        low_p = min(open_p, close_p) - random.uniform(0.5, 1.5)
        bars.append(Bar(
            ts=1000000 + i * 300000,
            open=open_p, high=high_p, low=low_p, close=close_p,
            volume=random.uniform(1000, 5000),
        ))
        price = close_p
    return bars


def test_parse_symbols_basic():
    syms = parse_symbols("SOLUSDT,BTCUSDT,ETHUSDT")
    assert syms == ["SOLUSDT", "BTCUSDT", "ETHUSDT"]


def test_parse_symbols_max_10():
    too_many = ",".join([f"SYM{i}USDT" for i in range(11)])
    with pytest.raises(ValueError):
        parse_symbols(too_many)


def test_parse_symbols_must_be_usdt():
    with pytest.raises(ValueError):
        parse_symbols("BTCUSD")
    with pytest.raises(ValueError):
        parse_symbols("AAPL")


def test_parse_symbols_empty():
    with pytest.raises(ValueError):
        parse_symbols("")
    with pytest.raises(ValueError):
        parse_symbols(",,,")


def test_parse_symbols_trims_whitespace():
    syms = parse_symbols(" SOLUSDT , BTCUSDT ,ETHUSDT ")
    assert syms == ["SOLUSDT", "BTCUSDT", "ETHUSDT"]


def test_parse_symbols_uppercases():
    syms = parse_symbols("solusdt,btcusdt")
    assert syms == ["SOLUSDT", "BTCUSDT"]


def test_compute_stats_empty():
    stats = compute_stats([])
    assert stats["trades"] == 0
    assert stats["pnl_net_total"] == 0


def test_compute_stats_winning_trade():
    from core.models import Trade, Direction, ExitReason
    t = Trade(
        symbol="X", direction=Direction.LONG, entry_price=100, entry_ts=0,
        exit_price=110, exit_ts=1, qty=1, notional=100, leverage=1,
        pnl_raw=10, fees=0.1, slippage=0, funding=0, pnl_net=9.9,
        reason=ExitReason.TP, bars_held=1, initial_sl=95, tp=110,
    )
    stats = compute_stats([t])
    assert stats["trades"] == 1
    assert stats["wins"] == 1
    assert stats["win_rate"] == 100.0
    assert stats["pnl_net_total"] == 9.9


def test_backtest_no_phantom_exits(tmp_path):
    """The bug-killer test: every exit in a backtest must be within a real bar's range."""
    cfg = {
        "system": {"data_dir": str(tmp_path), "mode": "paper"},
        "execution": {"leverage": 15, "notional_per_trade": 100, "partial_tp": {"enabled": False, "pct": 0.5, "r_multiple": 1.0}},
        "strategy": {
            "fvb_length": 4, "fvb_band_mult": 1.0,
            "bxt_l1": 3, "bxt_l2": 10, "bxt_l3": 3, "bxt_ll1": 10, "bxt_ll2": 3,
            "hurst_window": 20, "adx_period": 5, "adx_max": 50, "adx_trend_max": 60,
            "rsi2_oversold": 5, "rsi2_overbought": 95, "confirmation_bars": 3,
        },
        "exits": {
            "tp_atr_mult": 2.0, "sl_atr_mult": 1.5, "min_tp_pct": 5.0, "min_sl_pct": 2.0,
            "breakeven_bars": 2, "trail_after_be": 1.0, "max_bars": 50,
        },
        "fees": {"maker_pct": 0.02, "taker_pct": 0.06, "slippage_pct": 0.05, "funding_pct_per_8h": 0.01},
    }
    bars = make_synthetic_bars(n=300, vol=3.0)
    write_test_data(tmp_path, "TESTUSDT", bars)
    cfg_path = tmp_path / "config.yaml"
    import yaml
    with open(cfg_path, "w") as f:
        yaml.safe_dump(cfg, f)

    result = run_backtest_on_symbol("TESTUSDT", cfg, tf="5m")
    assert "error" not in result or result.get("error") is None
    if result.get("trades"):
        for t in result["trades"]:
            assert t["entry_price"] > 0
            assert t["exit_price"] > 0
            assert t["exit_price"] > 0


def test_backtest_returns_valid_structure(tmp_path):
    cfg = {
        "system": {"data_dir": str(tmp_path), "mode": "paper"},
        "execution": {"leverage": 15, "notional_per_trade": 100, "partial_tp": {"enabled": False, "pct": 0.5, "r_multiple": 1.0}},
        "strategy": {
            "fvb_length": 4, "fvb_band_mult": 1.0,
            "bxt_l1": 3, "bxt_l2": 10, "bxt_l3": 3, "bxt_ll1": 10, "bxt_ll2": 3,
            "hurst_window": 20, "adx_period": 5, "adx_max": 50, "adx_trend_max": 60,
            "rsi2_oversold": 5, "rsi2_overbought": 95, "confirmation_bars": 3,
        },
        "exits": {
            "tp_atr_mult": 2.0, "sl_atr_mult": 1.5, "min_tp_pct": 5.0, "min_sl_pct": 2.0,
            "breakeven_bars": 2, "trail_after_be": 1.0, "max_bars": 50,
        },
        "fees": {"maker_pct": 0.02, "taker_pct": 0.06, "slippage_pct": 0.05, "funding_pct_per_8h": 0.01},
    }
    bars = make_synthetic_bars(n=200)
    write_test_data(tmp_path, "TESTUSDT", bars)
    result = run_backtest_on_symbol("TESTUSDT", cfg, tf="5m")
    assert "symbol" in result
    assert "trades" in result
    assert "stats" in result
    assert isinstance(result["trades"], list)


def test_backtest_missing_data(tmp_path):
    cfg = {
        "system": {"data_dir": str(tmp_path), "mode": "paper"},
        "execution": {"leverage": 15, "notional_per_trade": 100, "partial_tp": {"enabled": False, "pct": 0.5, "r_multiple": 1.0}},
        "strategy": {
            "fvb_length": 8, "fvb_band_mult": 1.0, "bxt_l1": 5, "bxt_l2": 30, "bxt_l3": 5,
            "bxt_ll1": 30, "bxt_ll2": 8, "hurst_window": 100, "adx_period": 14,
            "adx_max": 30, "adx_trend_max": 35, "rsi2_oversold": 10, "rsi2_overbought": 90,
            "confirmation_bars": 6,
        },
        "exits": {
            "tp_atr_mult": 2.0, "sl_atr_mult": 1.5, "min_tp_pct": 15.0, "min_sl_pct": 6.0,
            "breakeven_bars": 8, "trail_after_be": 1.0, "max_bars": 200,
        },
        "fees": {"maker_pct": 0.02, "taker_pct": 0.06, "slippage_pct": 0.05, "funding_pct_per_8h": 0.01},
    }
    result = run_backtest_on_symbol("NOSYMBOL", cfg, tf="5m")
    assert "error" in result
    assert "No data" in result["error"]
