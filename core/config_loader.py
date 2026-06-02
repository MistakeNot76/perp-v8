"""YAML config loader. Single source of truth for all system parameters."""
from pathlib import Path
from typing import List
import yaml

from core.models import SymbolConfig, StrategyParams, FeeConfig, Mode


def _req(d: dict, key: str, path: str):
    if key not in d:
        raise KeyError(f"Missing required config key: {path}.{key}")
    return d[key]


def load_config(path: str = "config.yaml") -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    with open(p) as f:
        return yaml.safe_load(f)


def get_symbols(cfg: dict) -> List[str]:
    return [s.upper() for s in cfg.get("symbols", [])]


def get_mode(cfg: dict) -> Mode:
    return Mode(cfg["system"]["mode"].lower())


def get_strategy_params(cfg: dict) -> StrategyParams:
    s = cfg["strategy"]
    return StrategyParams(
        fvb_length=s["fvb_length"],
        fvb_band_mult=s["fvb_band_mult"],
        bxt_l1=s["bxt_l1"],
        bxt_l2=s["bxt_l2"],
        bxt_l3=s["bxt_l3"],
        bxt_ll1=s["bxt_ll1"],
        bxt_ll2=s["bxt_ll2"],
        hurst_window=s["hurst_window"],
        adx_period=s["adx_period"],
    )


def get_symbol_config(cfg: dict, symbol: str) -> SymbolConfig:
    ex = cfg["execution"]
    st = cfg["strategy"]
    ex_exit = cfg["exits"]
    return SymbolConfig(
        tf="5m",
        leverage=ex["leverage"],
        notional=ex["notional_per_trade"],
        min_tp_pct=ex_exit["min_tp_pct"],
        min_sl_pct=ex_exit["min_sl_pct"],
        tp_atr_mult=ex_exit["tp_atr_mult"],
        sl_atr_mult=ex_exit["sl_atr_mult"],
        confirmation_bars=st["confirmation_bars"],
        breakeven_bars=ex_exit["breakeven_bars"],
        trail_after_be=ex_exit["trail_after_be"],
        max_bars=ex_exit["max_bars"],
        adx_max=st["adx_max"],
        adx_trend_max=st["adx_trend_max"],
        rsi2_oversold=st["rsi2_oversold"],
        rsi2_overbought=st["rsi2_overbought"],
        partial_tp_enabled=ex["partial_tp"]["enabled"],
        partial_tp_pct=ex["partial_tp"]["pct"],
        partial_tp_r=ex["partial_tp"]["r_multiple"],
    )


def get_fee_config(cfg: dict) -> FeeConfig:
    f = cfg["fees"]
    return FeeConfig(
        maker_pct=f["maker_pct"],
        taker_pct=f["taker_pct"],
        slippage_pct=f["slippage_pct"],
        funding_pct_per_8h=f["funding_pct_per_8h"],
    )


def get_dashboard_port(cfg: dict) -> int:
    return cfg["dashboard"]["port"]


def save_config(cfg: dict, path: str = "config.yaml") -> None:
    """Write config back to disk (for live edits from dashboard)."""
    with open(path, "w") as f:
        yaml.safe_dump(cfg, f, default_flow_style=False, sort_keys=False)
