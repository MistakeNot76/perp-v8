"""
Bar-by-bar position simulator. SHARED by live and backtest.
This is the single source of truth for how positions are managed.
"""
from typing import List, Optional
from dataclasses import dataclass, field

from core.models import Bar, Position, Trade, Direction, SymbolConfig, FeeConfig, ExitReason
from core.indicators import Indicators
from core.entry_rules import check_entry, EntrySignal
from core.exit_rules import check_bar_exit, compute_tp_sl
from core.validator import validate_exit_price, validate_trade_math


@dataclass
class EngineState:
    symbol: str
    bars: List[Bar]
    indicators: Indicators
    sym_cfg: SymbolConfig
    fees: FeeConfig
    bar_minutes: int = 5
    open_position: Optional[Position] = None
    closed_trades: List[Trade] = field(default_factory=list)
    current_bar_idx: int = 0
    equity_curve: List[float] = field(default_factory=list)


def open_position(
    state: EngineState,
    signal: EntrySignal,
) -> Position:
    """Open a new position at the signal bar's close."""
    bar = state.bars[signal.bar_idx]
    entry_price = bar.close
    tp, sl = compute_tp_sl(signal.direction, entry_price, 0, state.sym_cfg)

    if signal.direction == Direction.LONG:
        high_water = entry_price
        low_water = entry_price
    else:
        high_water = entry_price
        low_water = entry_price

    pos = Position(
        symbol=state.symbol,
        direction=signal.direction,
        entry_price=entry_price,
        entry_bar_idx=signal.bar_idx,
        entry_ts=bar.ts,
        notional=state.sym_cfg.notional,
        leverage=state.sym_cfg.leverage,
        size=state.sym_cfg.notional / entry_price,
        initial_sl=sl,
        current_sl=sl,
        tp=tp,
        high_water=high_water,
        low_water=low_water,
    )
    state.open_position = pos
    return pos


def close_position(
    state: EngineState,
    exit_price: float,
    exit_bar: Bar,
    reason: ExitReason,
) -> Trade:
    """Close the open position. Validates math before returning."""
    pos = state.open_position
    if pos is None:
        raise ValueError("No open position to close")

    validate_exit_price(exit_price, exit_bar, pos.symbol)

    if pos.direction == Direction.LONG:
        gross = (exit_price - pos.entry_price) * pos.size
    else:
        gross = (pos.entry_price - exit_price) * pos.size

    fees = state.fees.entry_cost(pos.notional) + state.fees.exit_cost(pos.notional)
    funding = state.fees.funding_cost(pos.notional, pos.bars_held, state.bar_minutes)
    net = gross - fees - funding

    trade = Trade(
        symbol=pos.symbol,
        direction=pos.direction,
        entry_price=pos.entry_price,
        entry_ts=pos.entry_ts,
        exit_price=exit_price,
        exit_ts=exit_bar.ts,
        qty=pos.size,
        notional=pos.notional,
        leverage=pos.leverage,
        pnl_raw=gross,
        fees=fees,
        slippage=state.fees.slippage_pct * pos.notional / 100,
        funding=funding,
        pnl_net=net,
        reason=reason,
        bars_held=pos.bars_held,
        initial_sl=pos.initial_sl,
        tp=pos.tp,
    )
    validate_trade_math(trade, fees, trade.slippage)
    state.closed_trades.append(trade)
    state.equity_curve.append(net)
    state.open_position = None
    return trade


def step(state: EngineState, bar_idx: int) -> None:
    """Process one bar: check exits first, then entries."""
    if bar_idx >= len(state.bars):
        return
    bar = state.bars[bar_idx]
    state.current_bar_idx = bar_idx

    if state.open_position is not None:
        decision = check_bar_exit(state.open_position, bar, state.sym_cfg)
        if decision.should_exit and decision.exit_price is not None:
            close_position(state, decision.exit_price, bar, decision.reason)
            return

    if state.open_position is None:
        signal = check_entry(state.indicators, bar_idx, state.sym_cfg)
        if signal is not None:
            open_position(state, signal)


def run_bars(state: EngineState, start_idx: int = 0) -> List[Trade]:
    """Run the engine from start_idx through all bars."""
    for i in range(start_idx, len(state.bars)):
        step(state, i)
    if state.open_position is not None:
        last_bar = state.bars[-1]
        close_position(state, last_bar.close, last_bar, ExitReason.MAX_BARS)
    return state.closed_trades
