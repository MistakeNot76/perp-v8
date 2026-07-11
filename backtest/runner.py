"""
Backtest CLI + programmatic API. Shared by CLI, dashboard, and optimizer.

    python -m backtest.runner --symbols SOLUSDT,BTCUSDT,ETHUSDT
    python -m backtest.runner --symbols SOLUSDT --tf 5m --days 90
"""
import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config_loader import (
    load_config,
    get_symbol_config,
    get_strategy_params,
    get_fee_config,
    apply_overrides,
)
from core.data_loader import load_candles
from core.indicators import compute_all
from core.engine import EngineState, run_bars
from core.models import StrategyParams, Trade, Bar
from core.timeframes import tf_to_minutes, bars_per_day
from backtest.report import generate_report


def parse_symbols(arg: str) -> list:
    """Parse comma-separated symbol list. Max 10 symbols."""
    syms = [s.strip().upper() for s in arg.split(",") if s.strip()]
    if len(syms) > 10:
        raise ValueError(f"Max 10 symbols allowed, got {len(syms)}")
    if not syms:
        raise ValueError("At least 1 symbol required")
    for s in syms:
        if not s.endswith("USDT"):
            raise ValueError(f"Symbol must end with USDT: {s}")
    return syms


def _trade_to_dict(t: Trade) -> dict:
    return {
        "symbol": t.symbol,
        "direction": t.direction.value,
        "side": t.direction.value,
        "entry_price": t.entry_price,
        "entry_ts": t.entry_ts,
        "exit_price": t.exit_price,
        "exit_ts": t.exit_ts,
        "qty": t.qty,
        "notional": t.notional,
        "leverage": t.leverage,
        "pnl_raw": t.pnl_raw,
        "fees": t.fees,
        "slippage": t.slippage,
        "funding": t.funding,
        "pnl_net": t.pnl_net,
        "pnl": t.pnl_net,
        "reason": t.reason.value,
        "entry_reason": t.entry_reason,
        "bars_held": t.bars_held,
        "initial_sl": t.initial_sl,
        "tp": t.tp,
    }


def compute_stats(trades: list) -> dict:
    """Compute per-symbol backtest stats. Accepts Trade objects or dicts."""
    if not trades:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "pnl_net_total": 0.0,
            "pnl_net_avg": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "avg_bars_held": 0.0,
            "fees_total": 0.0,
            "slippage_total": 0.0,
            "funding_total": 0.0,
        }

    def _pnl(t):
        return t.pnl_net if hasattr(t, "pnl_net") else float(t.get("pnl_net", 0))

    def _bars(t):
        return t.bars_held if hasattr(t, "bars_held") else int(t.get("bars_held", 0))

    def _fees(t):
        return t.fees if hasattr(t, "fees") else float(t.get("fees", 0))

    def _slip(t):
        return t.slippage if hasattr(t, "slippage") else float(t.get("slippage", 0))

    def _fund(t):
        return t.funding if hasattr(t, "funding") else float(t.get("funding", 0))

    wins = [t for t in trades if _pnl(t) > 0]
    losses = [t for t in trades if _pnl(t) <= 0]
    gross_profit = sum(_pnl(t) for t in wins)
    gross_loss = abs(sum(_pnl(t) for t in losses))
    pnl_total = sum(_pnl(t) for t in trades)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        equity += _pnl(t)
        peak = max(peak, equity)
        dd = peak - equity
        max_dd = max(max_dd, dd)
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(trades) * 100,
        "pnl_net_total": pnl_total,
        "pnl_net_avg": pnl_total / len(trades),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0),
        "max_drawdown": max_dd,
        "avg_bars_held": sum(_bars(t) for t in trades) / len(trades),
        "fees_total": sum(_fees(t) for t in trades),
        "slippage_total": sum(_slip(t) for t in trades),
        "funding_total": sum(_fund(t) for t in trades),
    }


def _equity_curve(trades: List[dict]) -> List[dict]:
    eq = 0.0
    curve = []
    for t in trades:
        eq += float(t.get("pnl_net", 0))
        curve.append({"ts": t.get("exit_ts", 0), "equity": eq})
    return curve


def run_backtest_on_bars(
    symbol: str,
    bars: List[Bar],
    cfg: dict,
    strategy_params: Optional[StrategyParams] = None,
    tf: Optional[str] = None,
) -> dict:
    """Run engine on a provided bar list. Used by optimizer train/test splits."""
    sym_cfg = get_symbol_config(cfg, symbol)
    if tf:
        sym_cfg.tf = tf
    if strategy_params is None:
        strategy_params = get_strategy_params(cfg, symbol)
    fees = get_fee_config(cfg)

    if len(bars) < 50:
        return {
            "symbol": symbol,
            "error": f"Insufficient data: {len(bars)} bars",
            "trades": [],
            "stats": None,
            "equity_curve": [],
        }

    indicators = compute_all(bars, strategy_params)
    state = EngineState(
        symbol=symbol,
        bars=bars,
        indicators=indicators,
        sym_cfg=sym_cfg,
        fees=fees,
        bar_minutes=tf_to_minutes(sym_cfg.tf),
    )
    trades = run_bars(state, 0)
    trade_dicts = [_trade_to_dict(t) for t in trades]
    stats = compute_stats(trades)
    return {
        "symbol": symbol,
        "tf": sym_cfg.tf,
        "bars": len(bars),
        "from_ts": bars[0].ts,
        "to_ts": bars[-1].ts,
        "trades": trade_dicts,
        "stats": stats,
        "equity_curve": _equity_curve(trade_dicts),
        "params": {
            "fvb_length": strategy_params.fvb_length,
            "fvb_band_mult": strategy_params.fvb_band_mult,
            "bxt_l1": strategy_params.bxt_l1,
            "bxt_l2": strategy_params.bxt_l2,
            "confirmation_bars": sym_cfg.confirmation_bars,
        },
    }


def run_backtest_on_symbol(
    symbol: str,
    cfg: dict,
    tf: str = None,
    days: int = None,
    bars: Optional[List[Bar]] = None,
) -> dict:
    """Run backtest on a single symbol. Returns structured result dict."""
    sym_cfg = get_symbol_config(cfg, symbol)
    if tf:
        sym_cfg.tf = tf

    strategy_params = get_strategy_params(cfg, symbol)
    data_dir = cfg["system"]["data_dir"]

    if bars is None:
        try:
            bars = load_candles(symbol, sym_cfg.tf, data_dir)
        except FileNotFoundError:
            return {
                "symbol": symbol,
                "error": f"No data for {symbol} {sym_cfg.tf} in {data_dir}",
                "trades": [],
                "stats": None,
                "equity_curve": [],
            }

    if days and len(bars) > days * bars_per_day(sym_cfg.tf):
        cutoff = bars[-1].ts - days * 86400 * 1000
        bars = [b for b in bars if b.ts >= cutoff]

    return run_backtest_on_bars(symbol, bars, cfg, strategy_params=strategy_params, tf=sym_cfg.tf)


def run_backtest(
    symbols: List[str],
    cfg: dict,
    tf: Optional[str] = None,
    days: Optional[int] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> dict:
    """
    Programmatic multi-symbol backtest.

    Returns:
      {
        results: [...],
        totals: {...},
        symbols: [...dashboard-shaped...],
      }
    """
    bt_cfg = apply_overrides(cfg, overrides)
    results = []
    for sym in symbols:
        results.append(run_backtest_on_symbol(sym, bt_cfg, tf=tf, days=days))

    total_trades = 0
    total_pnl = 0.0
    total_wins = 0
    gross_profit = 0.0
    gross_loss = 0.0
    dash_symbols = []
    all_trades = []

    for r in results:
        s = r.get("stats") or {}
        n = int(s.get("trades", 0) or 0)
        total_trades += n
        pnl = float(s.get("pnl_net_total", 0) or 0)
        total_pnl += pnl
        wins = int(s.get("wins", 0) or 0)
        total_wins += wins
        # Reconstruct PF components from trades
        for t in r.get("trades") or []:
            all_trades.append(t)
            p = float(t.get("pnl_net", 0))
            if p > 0:
                gross_profit += p
            else:
                gross_loss += abs(p)

        dash_symbols.append({
            "symbol": r["symbol"],
            "tf": r.get("tf"),
            "error": r.get("error"),
            "trades": n,
            "wins": int(s.get("wins", 0) or 0),
            "losses": int(s.get("losses", 0) or 0),
            "win_rate": float(s.get("win_rate", 0) or 0),  # 0-100
            "pnl": pnl,
            "pnl_net_total": pnl,
            "profit_factor": s.get("profit_factor", 0),
            "max_drawdown": float(s.get("max_drawdown", 0) or 0),
            "max_drawdown_pct": None,
            "avg_bars_held": float(s.get("avg_bars_held", 0) or 0),
            "equity_curve": r.get("equity_curve") or [],
            "trade_list": r.get("trades") or [],
            "bars": r.get("bars"),
            "params": r.get("params"),
            "fees_total": float(s.get("fees_total", 0) or 0),
            "slippage_total": float(s.get("slippage_total", 0) or 0),
            "funding_total": float(s.get("funding_total", 0) or 0),
        })

    pf = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
    totals = {
        "trades": total_trades,
        "pnl": total_pnl,
        "win_rate": (total_wins / total_trades * 100) if total_trades else 0.0,
        "profit_factor": pf if pf != float("inf") else None,
        "wins": total_wins,
    }

    return {
        "run_ts": datetime.now(timezone.utc).isoformat(),
        "results": results,
        "symbols": dash_symbols,
        "totals": totals,
        "trade_list": all_trades,
    }


def main():
    parser = argparse.ArgumentParser(description="perp-v8 backtester")
    parser.add_argument("--symbols", required=True, help="Comma-separated symbols, max 10 (e.g. SOLUSDT,BTCUSDT)")
    parser.add_argument("--tf", default=None, help="Timeframe (e.g. 5m, 15m). Default from config")
    parser.add_argument("--days", type=int, default=None, help="Limit to last N days")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--output", default=None, help="Output JSON path (default: data/logs/backtest_<ts>.json)")
    parser.add_argument("--overrides", default=None, help="JSON string of config overrides")
    args = parser.parse_args()

    symbols = parse_symbols(args.symbols)
    cfg = load_config(args.config)
    overrides = json.loads(args.overrides) if args.overrides else None

    print(f"=== Backtest: {len(symbols)} symbols, tf={args.tf or 'config'}, days={args.days or 'all'} ===")
    payload = run_backtest(symbols, cfg, tf=args.tf, days=args.days, overrides=overrides)
    results = payload["results"]

    for r in results:
        if r.get("error"):
            print(f"  {r['symbol']}... ERROR: {r['error']}")
        elif r["stats"] and r["stats"]["trades"] > 0:
            s = r["stats"]
            print(f"  {r['symbol']}... {s['trades']} trades, {s['win_rate']:.1f}% WR, ${s['pnl_net_total']:.2f} PnL, PF {s['profit_factor']:.2f}")
        else:
            print(f"  {r['symbol']}... no trades")

    if not args.output:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        args.output = f"data/logs/backtest_{ts}.json"
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"\nResults saved to {args.output}")

    report_path = args.output.replace(".json", ".md")
    report = generate_report(results)
    with open(report_path, "w") as f:
        f.write(report)
    print(f"Report saved to {report_path}")


if __name__ == "__main__":
    main()
