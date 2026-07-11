"""YAML config loader. Single source of truth for all system parameters."""
from pathlib import Path
from typing import Any, Dict, List, Optional
import copy
import json
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


def _deep_merge(base: dict, override: dict) -> dict:
    """Return a deep copy of base with override keys merged in."""
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_symbol_params_file(symbol: str, params_dir: str = "data/params") -> dict:
    """Load optional per-symbol JSON overrides from data/params/{SYMBOL}.json."""
    p = Path(params_dir) / f"{symbol.upper()}.json"
    if not p.exists():
        return {}
    with open(p) as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def get_symbol_overrides(cfg: dict, symbol: str, params_dir: Optional[str] = None) -> dict:
    """
    Merge per-symbol overrides from config.yaml `symbol_params` and optional JSON file.

    JSON file wins over yaml map for the same keys (optimizer writes JSON).
    Set cfg['_skip_param_files']=True to ignore data/params during grid search.
    """
    sym = symbol.upper()
    yaml_map = cfg.get("symbol_params") or {}
    from_yaml = yaml_map.get(sym) or yaml_map.get(symbol) or {}
    if not isinstance(from_yaml, dict):
        from_yaml = {}

    if cfg.get("_skip_param_files"):
        return dict(from_yaml)

    data_dir = params_dir
    if data_dir is None:
        data_dir = str(Path(cfg.get("system", {}).get("data_dir", "data/history")).parent / "params")
    from_file = load_symbol_params_file(sym, data_dir)
    return _deep_merge(from_yaml, from_file)


def _strategy_dict(cfg: dict, symbol: Optional[str] = None) -> dict:
    s = dict(cfg["strategy"])
    if symbol:
        ov = get_symbol_overrides(cfg, symbol)
        # Flat keys or nested under "strategy"
        strat_ov = ov.get("strategy") if isinstance(ov.get("strategy"), dict) else {}
        flat = {k: v for k, v in ov.items() if k != "strategy" and k != "exits" and k != "execution"}
        s.update(flat)
        s.update(strat_ov)
    return s


def get_strategy_params(cfg: dict, symbol: Optional[str] = None) -> StrategyParams:
    s = _strategy_dict(cfg, symbol)
    ex = cfg.get("exits") or {}
    bxt_exit = ex.get("bxt_exit") or {}
    same = bxt_exit.get("same_tf") or {}
    ltf = bxt_exit.get("lower_tf") or {}
    return StrategyParams(
        fvb_length=int(s["fvb_length"]),
        fvb_band_mult=float(s["fvb_band_mult"]),
        bxt_l1=int(s["bxt_l1"]),
        bxt_l2=int(s["bxt_l2"]),
        bxt_l3=int(s.get("bxt_l3", 5)),
        bxt_ll1=int(s.get("bxt_ll1", 30)),
        bxt_ll2=int(s.get("bxt_ll2", 8)),
        hurst_window=int(s.get("hurst_window", 100)),
        adx_period=int(s.get("adx_period", 14)),
        bxt_exit_l1=int(same.get("l1", s.get("bxt_exit_l1", 3))),
        bxt_exit_l2=int(same.get("l2", s.get("bxt_exit_l2", 15))),
        bxt_ltf_l1=int(ltf.get("l1", s.get("bxt_ltf_l1", 3))),
        bxt_ltf_l2=int(ltf.get("l2", s.get("bxt_ltf_l2", 10))),
    )


def get_symbol_config(cfg: dict, symbol: str) -> SymbolConfig:
    ex = cfg["execution"]
    st = _strategy_dict(cfg, symbol)
    ex_exit = dict(cfg["exits"])
    ov = get_symbol_overrides(cfg, symbol)
    if isinstance(ov.get("exits"), dict):
        ex_exit = _deep_merge(ex_exit, ov["exits"])
    exec_ov = ov.get("execution") if isinstance(ov.get("execution"), dict) else {}
    leverage = int(exec_ov.get("leverage", ex["leverage"]))
    notional = float(exec_ov.get("notional_per_trade", exec_ov.get("notional", ex["notional_per_trade"])))

    # Flat exit/filter overrides allowed at symbol_params top level
    for k in (
        "min_tp_pct", "min_sl_pct", "tp_atr_mult", "sl_atr_mult",
        "breakeven_bars", "trail_after_be", "max_bars",
        "confirmation_bars", "adx_max", "adx_trend_max",
        "rsi2_oversold", "rsi2_overbought", "hurst_max",
        "use_fixed_tp", "use_trail", "fvb_exit_enabled", "fvb_exit_target",
        "bxt_exit_same_tf_enabled", "bxt_exit_ltf_enabled",
    ):
        if k in ov:
            if k in ("confirmation_bars", "adx_max", "adx_trend_max",
                     "rsi2_oversold", "rsi2_overbought", "hurst_max"):
                st[k] = ov[k]
            else:
                ex_exit[k] = ov[k]

    fvb_exit = ex_exit.get("fvb_exit") or {}
    if not isinstance(fvb_exit, dict):
        fvb_exit = {}
    if "fvb_exit_target" in ex_exit and "target" not in fvb_exit:
        fvb_exit["target"] = ex_exit["fvb_exit_target"]
    if "fvb_exit_enabled" in ex_exit and "enabled" not in fvb_exit:
        fvb_exit["enabled"] = ex_exit["fvb_exit_enabled"]

    bxt_exit = ex_exit.get("bxt_exit") or {}
    if not isinstance(bxt_exit, dict):
        bxt_exit = {}
    same_tf = bxt_exit.get("same_tf") or {}
    lower_tf = bxt_exit.get("lower_tf") or {}
    if "bxt_exit_same_tf_enabled" in ex_exit:
        same_tf = {**same_tf, "enabled": ex_exit["bxt_exit_same_tf_enabled"]}
    if "bxt_exit_ltf_enabled" in ex_exit:
        lower_tf = {**lower_tf, "enabled": ex_exit["bxt_exit_ltf_enabled"]}

    # partial_tp may live under execution (legacy) or exits
    partial = ex_exit.get("partial_tp") or ex.get("partial_tp") or {}
    if isinstance(partial, dict):
        p_enabled = bool(partial.get("enabled", False))
        p_pct = float(partial.get("pct", 0.5))
        p_r = float(partial.get("r_multiple", 1.0))
    else:
        p_enabled, p_pct, p_r = False, 0.5, 1.0

    return SymbolConfig(
        tf=st.get("tf", "15m"),
        leverage=leverage,
        notional=notional,
        min_tp_pct=float(ex_exit["min_tp_pct"]),
        min_sl_pct=float(ex_exit["min_sl_pct"]),
        tp_atr_mult=float(ex_exit["tp_atr_mult"]),
        sl_atr_mult=float(ex_exit["sl_atr_mult"]),
        confirmation_bars=int(st["confirmation_bars"]),
        breakeven_bars=int(ex_exit["breakeven_bars"]),
        trail_after_be=float(ex_exit["trail_after_be"]),
        max_bars=int(ex_exit["max_bars"]),
        adx_max=float(st["adx_max"]),
        adx_trend_max=float(st.get("adx_trend_max", 35)),
        rsi2_oversold=float(st["rsi2_oversold"]),
        rsi2_overbought=float(st["rsi2_overbought"]),
        hurst_max=float(st.get("hurst_max", 0.85)),
        partial_tp_enabled=p_enabled,
        partial_tp_pct=p_pct,
        partial_tp_r=p_r,
        use_fixed_tp=bool(ex_exit.get("use_fixed_tp", True)),
        use_trail=bool(ex_exit.get("use_trail", True)),
        fvb_exit_enabled=bool(fvb_exit.get("enabled", ex_exit.get("fvb_exit_enabled", True))),
        fvb_exit_target=str(fvb_exit.get("target", ex_exit.get("fvb_exit_target", "vwap"))),
        bxt_exit_same_tf_enabled=bool(same_tf.get("enabled", ex_exit.get("bxt_exit_same_tf_enabled", True))),
        bxt_exit_ltf_enabled=bool(lower_tf.get("enabled", ex_exit.get("bxt_exit_ltf_enabled", True))),
        bxt_exit_confirmation_bars=int(same_tf.get("confirmation_bars", 2)),
        bxt_ltf_confirmation_bars=int(lower_tf.get("confirmation_bars", 2)),
        bxt_exit_l1=int(same_tf.get("l1", 3)),
        bxt_exit_l2=int(same_tf.get("l2", 15)),
        bxt_ltf=str(lower_tf.get("tf", "5m")),
        bxt_ltf_l1=int(lower_tf.get("l1", 3)),
        bxt_ltf_l2=int(lower_tf.get("l2", 10)),
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


def apply_overrides(cfg: dict, overrides: Optional[Dict[str, Any]] = None) -> dict:
    """
    Deep-merge nested overrides into a config copy.

    Accepts either nested sections (strategy/exits/fees/execution) or flat
    keys that belong to those sections. Maps execution.notional -> notional_per_trade.
    """
    out = copy.deepcopy(cfg)
    if not overrides:
        return out

    section_keys = {"strategy", "exits", "fees", "execution", "system", "risk", "symbol_params"}
    nested: Dict[str, Any] = {}
    flat: Dict[str, Any] = {}
    for k, v in overrides.items():
        if k in section_keys and isinstance(v, dict):
            nested[k] = v
        else:
            flat[k] = v

    out = _deep_merge(out, nested)

    # Remap notional alias
    if "execution" in out and isinstance(out["execution"], dict):
        if "notional" in out["execution"] and "notional_per_trade" not in out["execution"]:
            out["execution"]["notional_per_trade"] = out["execution"].pop("notional")
        elif "notional" in out["execution"]:
            out["execution"]["notional_per_trade"] = out["execution"].pop("notional")

    if "notional" in flat:
        out.setdefault("execution", {})["notional_per_trade"] = flat.pop("notional")
    if "leverage" in flat:
        out.setdefault("execution", {})["leverage"] = flat.pop("leverage")

    strategy_keys = set(out.get("strategy", {}).keys())
    exits_keys = set(out.get("exits", {}).keys())
    fees_keys = set(out.get("fees", {}).keys())
    exec_keys = set(out.get("execution", {}).keys()) | {"notional_per_trade", "notional"}

    for k, v in flat.items():
        if k in strategy_keys or k in {
            "fvb_length", "fvb_band_mult", "bxt_l1", "bxt_l2", "bxt_l3",
            "confirmation_bars", "adx_max", "rsi2_oversold", "rsi2_overbought",
            "hurst_max", "tf",
        }:
            out.setdefault("strategy", {})[k] = v
        elif k in exits_keys:
            out.setdefault("exits", {})[k] = v
        elif k in fees_keys:
            out.setdefault("fees", {})[k] = v
        elif k in exec_keys:
            key = "notional_per_trade" if k == "notional" else k
            out.setdefault("execution", {})[key] = v

    return out


def save_symbol_params(symbol: str, params: dict, params_dir: str = "data/params") -> Path:
    """Write per-symbol optimized params to JSON."""
    d = Path(params_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{symbol.upper()}.json"
    with open(path, "w") as f:
        json.dump(params, f, indent=2)
    return path


def merge_symbol_params_into_config(cfg: dict, symbol: str, params: dict) -> dict:
    """Update cfg['symbol_params'][SYMBOL] with params (returns mutated cfg)."""
    sym = symbol.upper()
    cfg.setdefault("symbol_params", {})
    existing = cfg["symbol_params"].get(sym) or {}
    if not isinstance(existing, dict):
        existing = {}
    cfg["symbol_params"][sym] = _deep_merge(existing, params)
    return cfg


def save_config(cfg: dict, path: str = "config.yaml") -> None:
    """Write config back to disk (for live edits from dashboard)."""
    with open(path, "w") as f:
        yaml.safe_dump(cfg, f, default_flow_style=False, sort_keys=False)
