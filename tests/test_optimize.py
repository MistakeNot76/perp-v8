"""Optimizer smoke tests with tiny grid + synthetic bars."""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from optimize.runner import optimize_symbol, persist_best, iter_grid, DEFAULT_GRID
from tests.test_backtest import make_synthetic_bars, write_test_data


def _cfg(tmp_path):
    return {
        "system": {"data_dir": str(tmp_path), "mode": "paper"},
        "execution": {
            "leverage": 15,
            "notional_per_trade": 100,
            "partial_tp": {"enabled": False, "pct": 0.5, "r_multiple": 1.0},
        },
        "strategy": {
            "tf": "5m",
            "fvb_length": 8,
            "fvb_band_mult": 1.0,
            "bxt_l1": 3,
            "bxt_l2": 10,
            "bxt_l3": 3,
            "bxt_ll1": 10,
            "bxt_ll2": 3,
            "hurst_window": 20,
            "adx_period": 5,
            "adx_max": 80,
            "adx_trend_max": 90,
            "rsi2_oversold": 40,
            "rsi2_overbought": 60,
            "hurst_max": 0.99,
            "confirmation_bars": 3,
        },
        "exits": {
            "tp_atr_mult": 2.0,
            "sl_atr_mult": 1.5,
            "min_tp_pct": 2.0,
            "min_sl_pct": 1.0,
            "breakeven_bars": 2,
            "trail_after_be": 1.0,
            "max_bars": 50,
        },
        "fees": {
            "maker_pct": 0.02,
            "taker_pct": 0.06,
            "slippage_pct": 0.05,
            "funding_pct_per_8h": 0.01,
        },
        "symbol_params": {},
    }


def test_iter_grid_product():
    g = {"a": [1, 2], "b": [10]}
    rows = list(iter_grid(g))
    assert rows == [{"a": 1, "b": 10}, {"a": 2, "b": 10}]


def test_optimize_symbol_tiny_grid(tmp_path):
    cfg = _cfg(tmp_path)
    bars = make_synthetic_bars(n=400, vol=4.0)
    write_test_data(tmp_path, "OPTUSDT", bars)
    grid = {
        "fvb_length": [4, 8],
        "fvb_band_mult": [1.0, 1.5],
        "bxt_l1": [3],
        "bxt_l2": [10],
        "confirmation_bars": [3],
    }
    result = optimize_symbol(
        "OPTUSDT",
        cfg,
        tf="5m",
        grid=grid,
        train_frac=0.7,
        min_trades=1,
        max_dd=10_000,
        top_n=5,
    )
    assert result.get("error") is None
    assert result["grid_size"] == 4
    assert "best" in result
    # Persist
    payload = {"results": [result]}
    params_dir = tmp_path / "params"
    written = persist_best(payload, cfg, params_dir=str(params_dir), apply_config=False)
    if result.get("best"):
        assert len(written["written"]) == 1
        p = params_dir / "OPTUSDT.json"
        assert p.exists()
        data = json.loads(p.read_text())
        assert "fvb_length" in data
        assert "bxt_l1" in data
