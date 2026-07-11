"""
Validator: hard invariants that catch fabrication bugs at runtime.

Every exit price MUST be within the bar's [low, high] range.
Every PnL calculation MUST match the real math.

Failures are logged to data/logs/validator_failures.log.
"""
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from core.models import Bar, Trade, Direction


class ValidationError(Exception):
    pass


def validate_exit_price(exit_price: float, bar: Bar, symbol: str) -> None:
    """Exit price must be within the bar's [low, high] range."""
    if exit_price < bar.low - 1e-9 or exit_price > bar.high + 1e-9:
        _log_failure(
            symbol,
            f"PHANTOM EXIT: exit={exit_price:.6f} not in bar range [{bar.low:.6f}, {bar.high:.6f}]",
            bar,
        )
        raise ValidationError(
            f"Phantom exit price for {symbol}: {exit_price} not in bar range [{bar.low}, {bar.high}]"
        )


def validate_trade_math(trade: Trade, fees: float, slippage: float) -> None:
    """PnL uses notional size: gross = price_delta * qty, where qty = notional/entry.

    Leverage does NOT multiply PnL (it only sets margin = notional/leverage).
    Net = gross - fees - slippage - funding.
    """
    if trade.direction == Direction.LONG:
        gross = (trade.exit_price - trade.entry_price) * trade.qty
    else:
        gross = (trade.entry_price - trade.exit_price) * trade.qty
    expected_pnl_raw = gross
    expected_net = gross - trade.fees - trade.slippage - trade.funding
    if abs(trade.pnl_raw - expected_pnl_raw) > 1e-6:
        _log_failure(
            trade.symbol,
            f"PNL RAW MISMATCH: claimed={trade.pnl_raw:.6f} expected={expected_pnl_raw:.6f}",
            None,
        )
        raise ValidationError(f"PnL raw mismatch for {trade.symbol}")
    if abs(trade.pnl_net - expected_net) > 1e-6:
        _log_failure(
            trade.symbol,
            f"PNL NET MISMATCH: claimed={trade.pnl_net:.6f} expected={expected_net:.6f}",
            None,
        )
        raise ValidationError(f"PnL net mismatch for {trade.symbol}")


def _log_failure(symbol: str, msg: str, bar: Optional[Bar]) -> None:
    log_dir = Path("data/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "validator_failures.log"
    ts = datetime.now(timezone.utc).isoformat()
    with open(log_file, "a") as f:
        f.write(f"{ts} [{symbol}] {msg}\n")
        if bar:
            f.write(f"  bar: ts={bar.ts} o={bar.open:.6f} h={bar.high:.6f} l={bar.low:.6f} c={bar.close:.6f}\n")


def safe_exit_price(price: float, bar: Bar) -> float:
    """Clamp price to bar range. Use when uncertain."""
    return max(bar.low, min(bar.high, price))
