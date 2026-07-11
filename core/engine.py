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
    bar_minutes: int = 15  # default; backtest runner overrides from tf
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
    atr = state.indicators.atr[signal.bar_idx] if signal.bar_idx < len(state.indicators.atr) else None
    tp, sl = compute_tp_sl(signal.direction, entry_price, atr, state.sym_cfg)

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
        high_water=entry_price,
        low_water=entry_price,
        entry_reason=signal.reason,
    )
    state.open_position = pos
    return pos


def _build_trade(
    pos: Position,
    exit_price: float,
    exit_bar: Bar,
    reason: ExitReason,
    qty: float,
    notional: float,
    fees_obj: FeeConfig,
    bar_minutes: int,
    include_entry_fee: bool,
) -> Trade:
    if pos.direction == Direction.LONG:
        gross = (exit_price - pos.entry_price) * qty
    else:
        gross = (pos.entry_price - exit_price) * qty

    entry_fee = fees_obj.entry_cost(notional) if include_entry_fee else 0.0
    exit_fee = fees_obj.exit_cost(notional)
    # Slippage: full round-trip on first (full/partial) close that includes entry;
    # exit-only leg on subsequent remainder close.
    if include_entry_fee:
        slippage = fees_obj.round_trip_slippage(notional)
    else:
        slippage = notional * fees_obj.slippage_pct / 100
    funding = fees_obj.funding_cost(notional, pos.bars_held, bar_minutes)
    fees = entry_fee + exit_fee
    net = gross - fees - slippage - funding

    trade = Trade(
        symbol=pos.symbol,
        direction=pos.direction,
        entry_price=pos.entry_price,
        entry_ts=pos.entry_ts,
        exit_price=exit_price,
        exit_ts=exit_bar.ts,
        qty=qty,
        notional=notional,
        leverage=pos.leverage,
        pnl_raw=gross,
        fees=fees,
        slippage=slippage,
        funding=funding,
        pnl_net=net,
        reason=reason,
        bars_held=pos.bars_held,
        initial_sl=pos.initial_sl,
        tp=pos.tp,
        partial_tp_hit=pos.partial_tp_hit or reason == ExitReason.PARTIAL_TP,
        entry_reason=pos.entry_reason,
    )
    validate_trade_math(trade, fees, trade.slippage)
    return trade


def close_position(
    state: EngineState,
    exit_price: float,
    exit_bar: Bar,
    reason: ExitReason,
) -> Trade:
    """Close the open position fully. Validates math before returning."""
    pos = state.open_position
    if pos is None:
        raise ValueError("No open position to close")

    validate_exit_price(exit_price, exit_bar, pos.symbol)
    # If a partial already took the entry fee/slippage, remainder is exit-only costs
    include_entry = not pos.partial_tp_hit
    trade = _build_trade(
        pos,
        exit_price,
        exit_bar,
        reason,
        qty=pos.size,
        notional=pos.notional,
        fees_obj=state.fees,
        bar_minutes=state.bar_minutes,
        include_entry_fee=include_entry,
    )
    state.closed_trades.append(trade)
    state.equity_curve.append(trade.pnl_net)
    state.open_position = None
    return trade


def partial_close_position(
    state: EngineState,
    exit_price: float,
    exit_bar: Bar,
    qty: float,
) -> Trade:
    """Scale out part of the position; move SL to breakeven; keep remainder open."""
    pos = state.open_position
    if pos is None:
        raise ValueError("No open position to partially close")
    if qty <= 0 or qty >= pos.size:
        raise ValueError(f"Invalid partial qty {qty} for size {pos.size}")

    validate_exit_price(exit_price, exit_bar, pos.symbol)
    frac = qty / pos.size
    notional = pos.notional * frac
    trade = _build_trade(
        pos,
        exit_price,
        exit_bar,
        ExitReason.PARTIAL_TP,
        qty=qty,
        notional=notional,
        fees_obj=state.fees,
        bar_minutes=state.bar_minutes,
        include_entry_fee=True,
    )
    state.closed_trades.append(trade)
    state.equity_curve.append(trade.pnl_net)

    pos.size -= qty
    pos.notional -= notional
    pos.partial_tp_hit = True
    pos.partial_tp_qty += qty
    # Lock in breakeven on the runner
    if pos.direction == Direction.LONG:
        pos.current_sl = max(pos.current_sl, pos.entry_price)
    else:
        pos.current_sl = min(pos.current_sl, pos.entry_price)
    return trade


def step(state: EngineState, bar_idx: int) -> None:
    """Process one bar: check exits first, then entries."""
    if bar_idx >= len(state.bars):
        return
    bar = state.bars[bar_idx]
    state.current_bar_idx = bar_idx

    if state.open_position is not None:
        decision = check_bar_exit(
            state.open_position,
            bar,
            state.sym_cfg,
            indicators=state.indicators,
            bar_idx=bar_idx,
        )
        if decision.should_exit and decision.exit_price is not None:
            if decision.is_partial and decision.partial_exit_qty > 0:
                partial_close_position(state, decision.exit_price, bar, decision.partial_exit_qty)
                # Remainder stays open; no new entry this bar
                return
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
