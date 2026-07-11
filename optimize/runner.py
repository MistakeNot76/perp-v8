"""
Per-perp grid search + simple walk-forward for FVB / BXT settings.

    python -m optimize.runner --symbols SOLUSDT,BTCUSDT --days 90
    python -m optimize.runner --symbols SOLUSDT --apply-config
"""
from __future__ import annotations

import argparse
import copy
import itertools
import json
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config_loader import (
    load_config,
    apply_overrides,
    save_symbol_params,
    merge_symbol_params_into_config,
    save_config,
    get_strategy_params,
    get_symbol_config,
)
from core.data_loader import load_candles
from core.models import StrategyParams
from core.timeframes import bars_per_day
from backtest.runner import parse_symbols, run_backtest_on_bars, compute_stats


DEFAULT_GRID = {
    "fvb_length": [8, 12, 16, 20, 24],
    "fvb_band_mult": [1.0, 1.25, 1.5, 1.75, 2.0],
    "bxt_l1": [3, 5, 8],
    "bxt_l2": [20, 30, 40, 50],
    "confirmation_bars": [3, 6, 9],
}


@dataclass
class TrialResult:
    params: dict
    train_stats: dict
    test_stats: Optional[dict]
    score: float


def _pf(stats: Optional[dict]) -> float:
    if not stats:
        return 0.0
    pf = stats.get("profit_factor", 0.0)
    if pf == float("inf"):
        return 100.0
    return float(pf or 0.0)


def _score(stats: dict, min_trades: int, max_dd: float) -> float:
    """Rank key: profit factor, with hard floors on trades and drawdown."""
    if not stats or stats.get("trades", 0) < min_trades:
        return -1.0
    if stats.get("max_drawdown", 0) > max_dd:
        return -1.0
    pf = _pf(stats)
    # Tie-break with net pnl
    return pf * 1000.0 + float(stats.get("pnl_net_total", 0) or 0)


def iter_grid(grid: Dict[str, List[Any]]) -> Iterable[dict]:
    keys = list(grid.keys())
    for values in itertools.product(*(grid[k] for k in keys)):
        yield dict(zip(keys, values))


def _split_bars(bars: list, train_frac: float) -> Tuple[list, list]:
    if len(bars) < 100:
        return bars, []
    cut = max(50, int(len(bars) * train_frac))
    cut = min(cut, len(bars) - 20) if len(bars) > 70 else cut
    return bars[:cut], bars[cut:]


def _apply_trial_to_cfg(cfg: dict, params: dict) -> dict:
    """Build a config copy with trial strategy params (global, no symbol file)."""
    ov = {
        "strategy": {
            "fvb_length": int(params["fvb_length"]),
            "fvb_band_mult": float(params["fvb_band_mult"]),
            "bxt_l1": int(params["bxt_l1"]),
            "bxt_l2": int(params["bxt_l2"]),
            "confirmation_bars": int(params["confirmation_bars"]),
        }
    }
    # Clear symbol_params so trial is pure
    out = apply_overrides(cfg, ov)
    out["symbol_params"] = {}
    out["_skip_param_files"] = True
    return out


def evaluate_params(
    symbol: str,
    bars: list,
    cfg: dict,
    params: dict,
    tf: Optional[str] = None,
) -> dict:
    trial_cfg = _apply_trial_to_cfg(cfg, params)
    # Force confirmation_bars onto strategy then symbol config via overrides already applied
    sp = get_strategy_params(trial_cfg, symbol=None)
    # confirmation_bars lives on SymbolConfig — set via strategy key in apply_overrides
    return run_backtest_on_bars(symbol, bars, trial_cfg, strategy_params=sp, tf=tf)


def optimize_symbol(
    symbol: str,
    cfg: dict,
    *,
    tf: Optional[str] = None,
    days: Optional[int] = None,
    grid: Optional[Dict[str, List[Any]]] = None,
    train_frac: float = 0.7,
    min_trades: int = 5,
    max_dd: float = 500.0,
    top_n: int = 10,
) -> dict:
    """
    Grid-search FVB/BXT params for one symbol with train/test walk-forward.
    """
    grid = grid or DEFAULT_GRID
    sym_cfg = get_symbol_config(cfg, symbol)
    use_tf = tf or sym_cfg.tf
    data_dir = cfg["system"]["data_dir"]

    try:
        bars = load_candles(symbol, use_tf, data_dir)
    except FileNotFoundError as e:
        return {"symbol": symbol, "error": str(e), "best": None, "ranked": []}

    if days and len(bars) > days * bars_per_day(use_tf):
        cutoff = bars[-1].ts - days * 86400 * 1000
        bars = [b for b in bars if b.ts >= cutoff]

    train_bars, test_bars = _split_bars(bars, train_frac)
    if len(train_bars) < 50:
        return {
            "symbol": symbol,
            "error": f"Insufficient train bars: {len(train_bars)}",
            "best": None,
            "ranked": [],
        }

    train_ranked: List[TrialResult] = []
    for params in iter_grid(grid):
        # Skip invalid MA pairs
        if int(params["bxt_l1"]) >= int(params["bxt_l2"]):
            continue
        result = evaluate_params(symbol, train_bars, cfg, params, tf=use_tf)
        stats = result.get("stats") or {}
        score = _score(stats, min_trades=min_trades, max_dd=max_dd)
        train_ranked.append(TrialResult(params=params, train_stats=stats, test_stats=None, score=score))

    train_ranked.sort(key=lambda t: t.score, reverse=True)
    candidates = [t for t in train_ranked if t.score >= 0][:top_n]
    if not candidates:
        # Fall back to best train even if floors failed
        candidates = train_ranked[: min(top_n, 5)]

    final: List[TrialResult] = []
    for trial in candidates:
        test_stats = None
        if test_bars and len(test_bars) >= 50:
            test_result = evaluate_params(symbol, test_bars, cfg, trial.params, tf=use_tf)
            test_stats = test_result.get("stats") or {}
            # Prefer test PF when available; still require some trades on test if possible
            if test_stats.get("trades", 0) > 0:
                score = _score(test_stats, min_trades=max(1, min_trades // 3), max_dd=max_dd)
            else:
                score = trial.score * 0.1
        else:
            score = trial.score
        final.append(
            TrialResult(
                params=trial.params,
                train_stats=trial.train_stats,
                test_stats=test_stats,
                score=score,
            )
        )

    final.sort(key=lambda t: t.score, reverse=True)
    best = final[0] if final else None

    best_payload = None
    if best:
        best_payload = {
            "params": best.params,
            "train_stats": best.train_stats,
            "test_stats": best.test_stats,
            "score": best.score,
        }

    return {
        "symbol": symbol,
        "tf": use_tf,
        "bars": len(bars),
        "train_bars": len(train_bars),
        "test_bars": len(test_bars),
        "grid_size": sum(1 for p in iter_grid(grid) if int(p["bxt_l1"]) < int(p["bxt_l2"])),
        "best": best_payload,
        "ranked": [
            {
                "params": t.params,
                "train_stats": t.train_stats,
                "test_stats": t.test_stats,
                "score": t.score,
            }
            for t in final
        ],
    }


def optimize_symbols(
    symbols: List[str],
    cfg: dict,
    **kwargs,
) -> dict:
    results = []
    for sym in symbols:
        results.append(optimize_symbol(sym, cfg, **kwargs))
    return {
        "run_ts": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }


def persist_best(
    opt_result: dict,
    cfg: dict,
    *,
    params_dir: str = "data/params",
    apply_config: bool = False,
    config_path: str = "config.yaml",
) -> dict:
    """Write best params to JSON and optionally merge into config.yaml."""
    written = []
    for r in opt_result.get("results") or []:
        best = r.get("best")
        if not best or r.get("error"):
            continue
        sym = r["symbol"]
        params = {
            "fvb_length": best["params"]["fvb_length"],
            "fvb_band_mult": best["params"]["fvb_band_mult"],
            "bxt_l1": best["params"]["bxt_l1"],
            "bxt_l2": best["params"]["bxt_l2"],
            "confirmation_bars": best["params"]["confirmation_bars"],
            "_meta": {
                "optimized_at": datetime.now(timezone.utc).isoformat(),
                "train_stats": best.get("train_stats"),
                "test_stats": best.get("test_stats"),
                "score": best.get("score"),
                "tf": r.get("tf"),
            },
        }
        path = save_symbol_params(sym, params, params_dir=params_dir)
        written.append({"symbol": sym, "path": str(path), "params": params})
        if apply_config:
            merge_symbol_params_into_config(cfg, sym, {
                "fvb_length": params["fvb_length"],
                "fvb_band_mult": params["fvb_band_mult"],
                "bxt_l1": params["bxt_l1"],
                "bxt_l2": params["bxt_l2"],
                "confirmation_bars": params["confirmation_bars"],
            })
    if apply_config and written:
        save_config(cfg, config_path)
    return {"written": written, "applied_to_config": apply_config}


def main():
    parser = argparse.ArgumentParser(description="perp-v8 per-perp FVB/BXT optimizer")
    parser.add_argument("--symbols", required=True, help="Comma-separated symbols")
    parser.add_argument("--tf", default=None)
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--train-frac", type=float, default=0.7)
    parser.add_argument("--min-trades", type=int, default=5)
    parser.add_argument("--max-dd", type=float, default=500.0)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--grid", default=None, help="JSON grid override")
    parser.add_argument("--output", default=None)
    parser.add_argument("--params-dir", default="data/params")
    parser.add_argument("--apply-config", action="store_true", help="Merge best into config.yaml symbol_params")
    parser.add_argument("--no-write", action="store_true", help="Do not write data/params JSON")
    args = parser.parse_args()

    symbols = parse_symbols(args.symbols)
    cfg = load_config(args.config)
    grid = json.loads(args.grid) if args.grid else DEFAULT_GRID

    print(f"=== Optimize: {symbols} tf={args.tf or 'config'} days={args.days or 'all'} ===")
    print(f"Grid keys: {list(grid.keys())}")

    payload = optimize_symbols(
        symbols,
        cfg,
        tf=args.tf,
        days=args.days,
        grid=grid,
        train_frac=args.train_frac,
        min_trades=args.min_trades,
        max_dd=args.max_dd,
        top_n=args.top_n,
    )

    for r in payload["results"]:
        if r.get("error"):
            print(f"  {r['symbol']}: ERROR {r['error']}")
            continue
        best = r.get("best")
        if not best:
            print(f"  {r['symbol']}: no viable params")
            continue
        p = best["params"]
        ts = best.get("test_stats") or {}
        tr = best.get("train_stats") or {}
        print(
            f"  {r['symbol']}: fvb={p['fvb_length']}/{p['fvb_band_mult']} "
            f"bxt={p['bxt_l1']}/{p['bxt_l2']} conf={p['confirmation_bars']} "
            f"train_PF={_pf(tr):.2f} test_PF={_pf(ts):.2f} "
            f"train_n={tr.get('trades', 0)} test_n={ts.get('trades', 0)}"
        )

    if not args.no_write:
        persist = persist_best(
            payload,
            cfg,
            params_dir=args.params_dir,
            apply_config=args.apply_config,
            config_path=args.config,
        )
        payload["persisted"] = persist
        for w in persist["written"]:
            print(f"  wrote {w['path']}")

    if not args.output:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        args.output = f"data/logs/optimize_{ts}.json"
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"Results saved to {args.output}")


if __name__ == "__main__":
    main()
