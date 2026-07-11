"""Tests for per-symbol params merge and apply_overrides."""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config_loader import (
    apply_overrides,
    get_strategy_params,
    get_symbol_config,
    get_symbol_overrides,
    save_symbol_params,
    load_symbol_params_file,
)


BASE_CFG = {
    "system": {"data_dir": "data/history", "mode": "paper"},
    "execution": {
        "leverage": 15,
        "notional_per_trade": 100,
        "partial_tp": {"enabled": False, "pct": 0.5, "r_multiple": 1.0},
    },
    "strategy": {
        "tf": "15m",
        "fvb_length": 20,
        "fvb_band_mult": 1.5,
        "bxt_l1": 5,
        "bxt_l2": 30,
        "bxt_l3": 5,
        "bxt_ll1": 30,
        "bxt_ll2": 8,
        "hurst_window": 100,
        "adx_period": 14,
        "adx_max": 30,
        "adx_trend_max": 35,
        "rsi2_oversold": 10,
        "rsi2_overbought": 90,
        "hurst_max": 0.85,
        "confirmation_bars": 6,
    },
    "exits": {
        "tp_atr_mult": 2.0,
        "sl_atr_mult": 1.5,
        "min_tp_pct": 2.0,
        "min_sl_pct": 1.0,
        "breakeven_bars": 8,
        "trail_after_be": 1.0,
        "max_bars": 200,
    },
    "fees": {
        "maker_pct": 0.02,
        "taker_pct": 0.06,
        "slippage_pct": 0.05,
        "funding_pct_per_8h": 0.01,
    },
    "symbol_params": {},
}


def test_apply_overrides_nested_and_notional_alias():
    cfg = apply_overrides(
        BASE_CFG,
        {
            "strategy": {"fvb_length": 12},
            "notional": 250,
            "leverage": 10,
        },
    )
    assert cfg["strategy"]["fvb_length"] == 12
    assert cfg["execution"]["notional_per_trade"] == 250
    assert cfg["execution"]["leverage"] == 10


def test_symbol_params_yaml_override(tmp_path):
    cfg = dict(BASE_CFG)
    cfg["system"] = {"data_dir": str(tmp_path / "history"), "mode": "paper"}
    cfg["symbol_params"] = {
        "SOLUSDT": {"fvb_length": 8, "bxt_l1": 3, "confirmation_bars": 4}
    }
    # no JSON file
    (tmp_path / "history").mkdir()
    sp = get_strategy_params(cfg, "SOLUSDT")
    assert sp.fvb_length == 8
    assert sp.bxt_l1 == 3
    sc = get_symbol_config(cfg, "SOLUSDT")
    assert sc.confirmation_bars == 4


def test_symbol_params_json_wins_over_yaml(tmp_path):
    cfg = dict(BASE_CFG)
    hist = tmp_path / "history"
    params = tmp_path / "params"
    hist.mkdir()
    params.mkdir()
    cfg["system"] = {"data_dir": str(hist), "mode": "paper"}
    cfg["symbol_params"] = {"BTCUSDT": {"fvb_length": 8}}
    save_symbol_params("BTCUSDT", {"fvb_length": 24, "fvb_band_mult": 2.0}, str(params))
    # get_symbol_overrides resolves params as sibling of history
    ov = get_symbol_overrides(cfg, "BTCUSDT")
    assert ov["fvb_length"] == 24
    assert ov["fvb_band_mult"] == 2.0


def test_skip_param_files_flag(tmp_path):
    cfg = dict(BASE_CFG)
    hist = tmp_path / "history"
    params = tmp_path / "params"
    hist.mkdir()
    params.mkdir()
    cfg["system"] = {"data_dir": str(hist), "mode": "paper"}
    save_symbol_params("ETHUSDT", {"fvb_length": 99}, str(params))
    cfg["_skip_param_files"] = True
    ov = get_symbol_overrides(cfg, "ETHUSDT")
    assert ov.get("fvb_length") is None
