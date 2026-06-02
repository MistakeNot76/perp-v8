"""
Backtest CLI. Run with:
    python -m backtest.runner --symbols SOLUSDT,BTCUSDT,ETHUSDT
    python -m backtest.runner --symbols SOLUSDT --tf 5m --days 90
"""
import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config_loader import load_config, get_symbol_config, get_strategy_params, get_fee_config
from core.data_loader import load_candles, resample
from core.indicators import compute_all
from core.engine import EngineState, run_bars
from core.models import SymbolConfig, FeeConfig, StrategyParams, Bar, Trade
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


def run_backtest_on_symbol(
    symbol: str,
    cfg: dict,
    tf: str = None,
    days: int = None,
) -> dict:
    """Run backtest on a single symbol. Returns stats dict."""
    sym_cfg = get_symbol_config(cfg, symbol)
    if tf:
        sym_cfg.tf = tf

    strategy_params = get_strategy_params(cfg)
    fees = get_fee_config(cfg)
    data_dir = cfg["system"]["data_dir"]

    try:
        bars = load_candles(symbol, sym_cfg.tf, data_dir)
    except FileNotFoundError:
        return {
            "symbol": symbol,
            "error": f"No data for {symbol} {sym_cfg.tf} in {data_dir}",
            "trades": [],
            "stats": None,
        }

    if days and len(bars) > days * (1440 // int(sym_cfg.tf.replace("m", ""))):
        cutoff = bars[-1].ts - days * 86400 * 1000
        bars = [b for b in bars if b.ts >= cutoff]

    if len(bars) < 50:
        return {
            "symbol": symbol,
            "error": f"Insufficient data: {len(bars)} bars",
            "trades": [],
            "stats": None,
        }

    indicators = compute_all(bars, strategy_params)
    state = EngineState(
        symbol=symbol,
        bars=bars,
        indicators=indicators,
        sym_cfg=sym_cfg,
        fees=fees,
        bar_minutes=int(sym_cfg.tf.replace("m", "")),
    )
    trades = run_bars(state, 0)
    stats = compute_stats(trades)
    return {
        "symbol": symbol,
        "tf": sym_cfg.tf,
        "bars": len(bars),
        "from_ts": bars[0].ts,
        "to_ts": bars[-1].ts,
        "trades": [_trade_to_dict(t) for t in trades],
        "stats": stats,
    }


def _trade_to_dict(t: Trade) -> dict:
    return {
        "direction": t.direction.value,
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
        "reason": t.reason.value,
        "bars_held": t.bars_held,
    }


def compute_stats(trades: list) -> dict:
    """Compute per-symbol backtest stats."""
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
        }
    wins = [t for t in trades if t.pnl_net > 0]
    losses = [t for t in trades if t.pnl_net <= 0]
    gross_profit = sum(t.pnl_net for t in wins)
    gross_loss = abs(sum(t.pnl_net for t in losses))
    pnl_total = sum(t.pnl_net for t in trades)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        equity += t.pnl_net
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
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float("inf"),
        "max_drawdown": max_dd,
        "avg_bars_held": sum(t.bars_held for t in trades) / len(trades),
    }


def main():
    parser = argparse.ArgumentParser(description="perp-v8 backtester")
    parser.add_argument("--symbols", required=True, help="Comma-separated symbols, max 10 (e.g. SOLUSDT,BTCUSDT)")
    parser.add_argument("--tf", default=None, help="Timeframe (e.g. 5m, 15m). Default from config")
    parser.add_argument("--days", type=int, default=None, help="Limit to last N days")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--output", default=None, help="Output JSON path (default: data/logs/backtest_<ts>.json)")
    args = parser.parse_args()

    symbols = parse_symbols(args.symbols)
    cfg = load_config(args.config)

    print(f"=== Backtest: {len(symbols)} symbols, tf={args.tf or 'config'}, days={args.days or 'all'} ===")
    results = []
    for sym in symbols:
        print(f"  {sym}... ", end="", flush=True)
        r = run_backtest_on_symbol(sym, cfg, tf=args.tf, days=args.days)
        if r.get("error"):
            print(f"ERROR: {r['error']}")
        elif r["stats"] and r["stats"]["trades"] > 0:
            s = r["stats"]
            print(f"{s['trades']} trades, {s['win_rate']:.1f}% WR, ${s['pnl_net_total']:.2f} PnL, PF {s['profit_factor']:.2f}")
        else:
            print("no trades")
        results.append(r)

    if not args.output:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        args.output = f"data/logs/backtest_{ts}.json"
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump({"run_ts": datetime.now(timezone.utc).isoformat(), "results": results}, f, indent=2)
    print(f"\nResults saved to {args.output}")

    report_path = args.output.replace(".json", ".md")
    report = generate_report(results)
    with open(report_path, "w") as f:
        f.write(report)
    print(f"Report saved to {report_path}")


if __name__ == "__main__":
    main()
