"""Human-readable markdown report for backtest results."""
from typing import List


def generate_report(results: list) -> str:
    """Generate a markdown summary of backtest results."""
    lines = ["# Backtest Report", ""]
    lines.append(f"Run: {results[0].get('symbol', '?')}")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append("| Symbol | TF | Trades | Wins | Losses | Win% | PnL Net | PF | Max DD | Avg Bars |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        if r.get("error"):
            lines.append(f"| {r['symbol']} | - | ERROR: {r['error']} | | | | | | | |")
            continue
        s = r.get("stats")
        if not s or s["trades"] == 0:
            lines.append(f"| {r['symbol']} | {r.get('tf','?')} | 0 | - | - | - | - | - | - | - |")
            continue
        pf = "inf" if s["profit_factor"] == float("inf") else f"{s['profit_factor']:.2f}"
        lines.append(
            f"| {r['symbol']} | {r.get('tf','?')} | {s['trades']} | {s['wins']} | {s['losses']} | "
            f"{s['win_rate']:.1f}% | ${s['pnl_net_total']:.2f} | {pf} | ${s['max_drawdown']:.2f} | {s['avg_bars_held']:.1f} |"
        )
    lines.append("")

    total_trades = sum(r.get("stats", {}).get("trades", 0) for r in results if r.get("stats"))
    total_pnl = sum(r.get("stats", {}).get("pnl_net_total", 0) for r in results if r.get("stats"))
    lines.append(f"**Total trades:** {total_trades}")
    lines.append(f"**Total PnL:** ${total_pnl:.2f}")
    lines.append("")

    for r in results:
        if r.get("error") or not r.get("trades"):
            continue
        sym = r["symbol"]
        lines.append(f"## {sym} — {r.get('tf','?')}")
        lines.append("")
        lines.append(f"Bars: {r.get('bars', 0)} | From: {r.get('from_ts', 0)} | To: {r.get('to_ts', 0)}")
        lines.append("")
        lines.append("| # | Direction | Entry | Exit | PnL | Reason | Bars |")
        lines.append("|---|---|---|---|---|---|---|")
        for i, t in enumerate(r["trades"][:50]):
            lines.append(
                f"| {i+1} | {t['direction']} | {t['entry_price']:.4f} | {t['exit_price']:.4f} | "
                f"${t['pnl_net']:.2f} | {t['reason']} | {t['bars_held']} |"
            )
        if len(r["trades"]) > 50:
            lines.append(f"| ... | ({len(r['trades'])-50} more trades) | | | | | |")
        lines.append("")

    return "\n".join(lines)
