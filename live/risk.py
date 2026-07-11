"""
Risk limits for live trading.

Enforces config risk/execution caps and can auto-arm the kill switch.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class RiskSnapshot:
    equity: float
    peak_equity: float
    daily_pnl: float
    open_positions: int
    total_notional: float
    kill_switch: bool
    breach: Optional[str] = None


def _f(cfg: dict, *keys: str, default: float = 0.0) -> float:
    cur: Any = cfg
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    try:
        return float(cur)
    except (TypeError, ValueError):
        return default


def check_risk_limits(
    cfg: dict,
    *,
    equity: float,
    peak_equity: float,
    daily_pnl: float,
    open_positions: int,
    total_notional: float,
    starting_equity: float,
) -> RiskSnapshot:
    """Return a snapshot; set breach if any limit is violated."""
    kill = bool(cfg.get("risk", {}).get("kill_switch", False))
    max_daily = _f(cfg, "risk", "max_daily_loss_pct", default=5.0)
    max_dd = _f(cfg, "risk", "max_drawdown_pct", default=15.0)
    max_open = int(_f(cfg, "execution", "max_open_positions", default=20))
    max_notional = _f(cfg, "execution", "max_total_notional", default=4000.0)

    breach: Optional[str] = None
    base = starting_equity if starting_equity > 0 else max(equity, 1.0)

    if daily_pnl < 0 and abs(daily_pnl) / base * 100 >= max_daily:
        breach = f"max_daily_loss_pct ({max_daily}%): daily_pnl={daily_pnl:.2f}"
    elif peak_equity > 0 and (peak_equity - equity) / peak_equity * 100 >= max_dd:
        breach = f"max_drawdown_pct ({max_dd}%): equity={equity:.2f} peak={peak_equity:.2f}"
    elif open_positions > max_open:
        breach = f"max_open_positions ({max_open}): open={open_positions}"
    elif total_notional > max_notional + 1e-9:
        breach = f"max_total_notional ({max_notional}): notional={total_notional:.2f}"

    return RiskSnapshot(
        equity=equity,
        peak_equity=peak_equity,
        daily_pnl=daily_pnl,
        open_positions=open_positions,
        total_notional=total_notional,
        kill_switch=kill or breach is not None,
        breach=breach,
    )


def can_open_new(
    cfg: dict,
    *,
    open_positions: int,
    total_notional: float,
    new_notional: float,
) -> Tuple[bool, str]:
    """Pre-entry gate for position count and notional caps."""
    max_open = int(_f(cfg, "execution", "max_open_positions", default=20))
    max_notional = _f(cfg, "execution", "max_total_notional", default=4000.0)
    if open_positions >= max_open:
        return False, f"max_open_positions ({max_open})"
    if total_notional + new_notional > max_notional + 1e-9:
        return False, f"max_total_notional ({max_notional})"
    return True, ""


def set_kill_switch(cfg: dict, on: bool) -> dict:
    cfg = dict(cfg)
    risk = dict(cfg.get("risk") or {})
    risk["kill_switch"] = bool(on)
    cfg["risk"] = risk
    return cfg
