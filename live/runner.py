"""
Live trading runner. Main loop: fetch → simulate → execute.
Uses the SAME engine as backtest. Parity guaranteed.

On OPEN / CLOSE / PARTIAL transitions, places exchange orders (paper records
simulated fills; demo/live hit Bitget). Risk limits and kill switch are
re-read from config each loop so the dashboard can hot-toggle them.
"""
from __future__ import annotations

import os
import time
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config_loader import (
    load_config,
    get_symbols,
    get_mode,
    get_symbol_config,
    get_strategy_params,
    get_fee_config,
)
from core.indicators import compute_all
from core.engine import EngineState, step
from core.models import Mode, Direction, Position, ExitReason
from core.timeframes import tf_to_minutes
from live.exchange import get_exchange
from live.state import (
    append_signal_log,
    save_open_positions,
    load_open_positions,
)
from live.risk import check_risk_limits, can_open_new, set_kill_switch


class LiveRunner:
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = config_path
        self.cfg = load_config(config_path)
        self.mode = get_mode(self.cfg)
        self.symbols = get_symbols(self.cfg)
        self.fees = get_fee_config(self.cfg)
        data_dir = self.cfg.get("system", {}).get("data_dir", "data/history")
        self.exchange = get_exchange(self.mode, data_dir=data_dir)
        self.states: Dict[str, EngineState] = {}
        self.running = True
        self.kill_switch = bool(self.cfg.get("risk", {}).get("kill_switch", False))
        self.starting_equity = float(self.exchange.get_balance())
        self.peak_equity = self.starting_equity
        self._day_key = self._utc_day()
        self._day_start_equity = self.starting_equity
        self._realized_pnl = 0.0

        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

    def _shutdown(self, signum, frame):
        print("\nShutdown requested...")
        self.running = False
        self._persist_positions()

    @staticmethod
    def _utc_day() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _reload_risk_flags(self) -> None:
        """Hot-reload kill switch and risk section from disk."""
        try:
            fresh = load_config(self.config_path)
            self.cfg["risk"] = fresh.get("risk", self.cfg.get("risk", {}))
            self.cfg["execution"] = fresh.get("execution", self.cfg.get("execution", {}))
            self.kill_switch = bool(self.cfg.get("risk", {}).get("kill_switch", False))
        except Exception as e:
            print(f"Config reload warning: {e}")

    def _persist_positions(self) -> None:
        mapping = {
            sym: state.open_position for sym, state in self.states.items()
        }
        save_open_positions(mapping)

    def _restore_positions(self) -> None:
        saved = load_open_positions()
        if not saved:
            return
        for sym, pos in saved.items():
            if sym not in self.states:
                continue
            self.states[sym].open_position = pos
            print(f"[{sym}] Restored open {pos.direction.value} @ {pos.entry_price}")

    def _reconcile_exchange(self) -> None:
        """On demo/live, warn if exchange positions disagree with engine state."""
        if self.mode == Mode.PAPER:
            return
        try:
            exch_pos = self.exchange.get_positions()
        except Exception as e:
            print(f"Reconcile skipped: {e}")
            return
        exch_by_sym = {}
        for p in exch_pos:
            raw = (p.get("symbol") or "").replace("/", "").replace(":USDT", "")
            exch_by_sym[raw] = p
        for sym, state in self.states.items():
            engine_open = state.open_position is not None
            exch = exch_by_sym.get(sym) or exch_by_sym.get(sym.replace("USDT", "/USDT:USDT"))
            exch_open = exch is not None
            if engine_open != exch_open:
                print(
                    f"[RECONCILE] {sym}: engine_open={engine_open} exchange_open={exch_open}"
                )

    def _init_symbol_state(self, symbol: str) -> EngineState:
        sym_cfg = get_symbol_config(self.cfg, symbol)
        strategy_params = get_strategy_params(self.cfg, symbol)
        tf = sym_cfg.tf
        bars = self.exchange.fetch_candles(symbol, tf, limit=500)
        ltf_bars = None
        if sym_cfg.bxt_exit_ltf_enabled and sym_cfg.bxt_ltf:
            try:
                if tf_to_minutes(sym_cfg.bxt_ltf) < tf_to_minutes(tf):
                    ltf_bars = self.exchange.fetch_candles(symbol, sym_cfg.bxt_ltf, limit=1500)
            except Exception:
                ltf_bars = None
        strategy_params.bxt_exit_l1 = sym_cfg.bxt_exit_l1
        strategy_params.bxt_exit_l2 = sym_cfg.bxt_exit_l2
        strategy_params.bxt_ltf_l1 = sym_cfg.bxt_ltf_l1
        strategy_params.bxt_ltf_l2 = sym_cfg.bxt_ltf_l2
        indicators = compute_all(bars, strategy_params, ltf_bars=ltf_bars)
        return EngineState(
            symbol=symbol,
            bars=bars,
            indicators=indicators,
            sym_cfg=sym_cfg,
            fees=self.fees,
            bar_minutes=tf_to_minutes(tf),
        )

    def _execute_open(self, symbol: str, pos: Position) -> Optional[str]:
        try:
            oid = self.exchange.place_order(
                symbol, pos.direction, pos.size, pos.entry_price
            )
            return oid
        except Exception as e:
            print(f"[{symbol}] place_order FAILED: {e}")
            return None

    def _execute_close(self, symbol: str, direction: Direction, qty: float, price: float) -> Optional[str]:
        try:
            oid = self.exchange.close_position(symbol, direction, qty, price)
            return oid
        except Exception as e:
            print(f"[{symbol}] close_position FAILED: {e}")
            return None

    def _on_position_update(self, symbol: str, action: str, record: dict):
        append_signal_log(symbol, {"symbol": symbol, "action": action, **record})
        print(f"[{symbol}] {action}: {record}")

    def _open_count_and_notional(self) -> tuple:
        n = 0
        notional = 0.0
        for state in self.states.values():
            if state.open_position is not None:
                n += 1
                notional += float(state.open_position.notional)
        return n, notional

    def _equity_now(self) -> float:
        try:
            bal = float(self.exchange.get_balance())
        except Exception:
            bal = self.starting_equity + self._realized_pnl
        # Add unrealized from open positions using last close as mark
        upnl = 0.0
        for state in self.states.values():
            pos = state.open_position
            if pos is None or not state.bars:
                continue
            mark = state.bars[-1].close
            if pos.direction == Direction.LONG:
                upnl += (mark - pos.entry_price) * pos.size
            else:
                upnl += (pos.entry_price - mark) * pos.size
        return bal + upnl if self.mode != Mode.PAPER else self.starting_equity + self._realized_pnl + upnl

    def _enforce_risk(self) -> bool:
        """Return True if trading should halt."""
        day = self._utc_day()
        if day != self._day_key:
            self._day_key = day
            self._day_start_equity = self._equity_now()

        equity = self._equity_now()
        self.peak_equity = max(self.peak_equity, equity)
        daily_pnl = equity - self._day_start_equity
        open_n, total_n = self._open_count_and_notional()

        snap = check_risk_limits(
            self.cfg,
            equity=equity,
            peak_equity=self.peak_equity,
            daily_pnl=daily_pnl,
            open_positions=open_n,
            total_notional=total_n,
            starting_equity=self.starting_equity,
        )
        if snap.breach:
            print(f"RISK BREACH: {snap.breach} — arming kill switch")
            self.cfg = set_kill_switch(self.cfg, True)
            try:
                from core.config_loader import save_config
                save_config(self.cfg, self.config_path)
            except Exception:
                # Fallback write
                import yaml
                with open(self.config_path, "w") as f:
                    yaml.safe_dump(self.cfg, f, default_flow_style=False, sort_keys=False)
            self.kill_switch = True
            return True
        return self.kill_switch

    def run(self):
        print(f"=== LiveRunner mode={self.mode.value} symbols={self.symbols} ===")
        for symbol in self.symbols:
            self.states[symbol] = self._init_symbol_state(symbol)
        self._restore_positions()
        self._reconcile_exchange()

        while self.running:
            self._reload_risk_flags()
            if self._enforce_risk():
                print("Kill switch ON. Halting new activity (sleeping).")
                time.sleep(5)
                continue
            try:
                for symbol in self.symbols:
                    state = self.states[symbol]
                    new_bars = self.exchange.fetch_candles(symbol, state.sym_cfg.tf, limit=1)
                    if not (new_bars and new_bars[-1].ts > state.bars[-1].ts):
                        continue

                    state.bars.append(new_bars[-1])
                    sp = get_strategy_params(self.cfg, symbol)
                    sp.bxt_exit_l1 = state.sym_cfg.bxt_exit_l1
                    sp.bxt_exit_l2 = state.sym_cfg.bxt_exit_l2
                    sp.bxt_ltf_l1 = state.sym_cfg.bxt_ltf_l1
                    sp.bxt_ltf_l2 = state.sym_cfg.bxt_ltf_l2
                    ltf_bars = None
                    if state.sym_cfg.bxt_exit_ltf_enabled and state.sym_cfg.bxt_ltf:
                        try:
                            if tf_to_minutes(state.sym_cfg.bxt_ltf) < tf_to_minutes(state.sym_cfg.tf):
                                ltf_bars = self.exchange.fetch_candles(
                                    symbol, state.sym_cfg.bxt_ltf, limit=1500
                                )
                        except Exception:
                            ltf_bars = None
                    state.indicators = compute_all(state.bars, sp, ltf_bars=ltf_bars)

                    had_position = state.open_position is not None
                    before_count = len(state.closed_trades)

                    open_n, total_n = self._open_count_and_notional()
                    if had_position:
                        allow_entry, block_reason = True, ""
                    else:
                        allow_entry, block_reason = can_open_new(
                            self.cfg,
                            open_positions=open_n,
                            total_notional=total_n,
                            new_notional=state.sym_cfg.notional,
                        )

                    step(state, len(state.bars) - 1)

                    # New closed trades this bar (partial and/or full)
                    for trade in state.closed_trades[before_count:]:
                        action = "PARTIAL" if trade.reason == ExitReason.PARTIAL_TP and state.open_position else "CLOSE"
                        oid = self._execute_close(
                            symbol, trade.direction, trade.qty, trade.exit_price
                        )
                        self._realized_pnl += trade.pnl_net
                        self._on_position_update(symbol, action, {
                            "direction": trade.direction.value,
                            "side": trade.direction.value,
                            "entry_price": trade.entry_price,
                            "entry": trade.entry_price,
                            "exit_price": trade.exit_price,
                            "exit": trade.exit_price,
                            "pnl_net": trade.pnl_net,
                            "pnl": trade.pnl_net,
                            "qty": trade.qty,
                            "size": trade.qty,
                            "reason": trade.reason.value,
                            "entry_reason": trade.entry_reason,
                            "bars_held": trade.bars_held,
                            "fees": trade.fees,
                            "slippage": trade.slippage,
                            "funding": trade.funding,
                            "order_id": oid,
                        })
                        self._persist_positions()

                    # New open this bar
                    if state.open_position is not None and not had_position:
                        if not allow_entry:
                            print(f"[{symbol}] Entry blocked: {block_reason}")
                            state.open_position = None
                            continue
                        oid = self._execute_open(symbol, state.open_position)
                        if oid is None and self.mode != Mode.PAPER:
                            state.open_position = None
                            continue
                        self._on_position_update(symbol, "OPEN", {
                            "direction": state.open_position.direction.value,
                            "side": state.open_position.direction.value,
                            "entry_price": state.open_position.entry_price,
                            "entry": state.open_position.entry_price,
                            "size": state.open_position.size,
                            "qty": state.open_position.size,
                            "sl": state.open_position.current_sl,
                            "tp": state.open_position.tp,
                            "leverage": state.open_position.leverage,
                            "entry_reason": state.open_position.entry_reason,
                            "order_id": oid,
                        })
                        self._persist_positions()

                time.sleep(5)
            except Exception as e:
                print(f"Error: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(10)

        self._persist_positions()

    def get_state_snapshot(self) -> dict:
        positions = []
        upnl = 0.0
        for sym, state in self.states.items():
            pos = state.open_position
            if pos is None:
                continue
            mark = state.bars[-1].close if state.bars else pos.entry_price
            if pos.direction == Direction.LONG:
                u = (mark - pos.entry_price) * pos.size
            else:
                u = (pos.entry_price - mark) * pos.size
            upnl += u
            positions.append({
                "symbol": sym,
                "side": pos.direction.value,
                "qty": pos.size,
                "size": pos.size,
                "entry_price": pos.entry_price,
                "entry": pos.entry_price,
                "mark_price": mark,
                "mark": mark,
                "unrealized_pnl": u,
                "upnl": u,
                "leverage": pos.leverage,
                "stop_loss": pos.current_sl,
                "take_profit": pos.tp,
            })
        equity = self._equity_now()
        return {
            "mode": self.mode.value,
            "kill_switch": self.kill_switch,
            "running": self.running and not self.kill_switch,
            "equity": equity,
            "available": self.starting_equity + self._realized_pnl,
            "upnl": upnl,
            "total_pnl": self._realized_pnl + upnl,
            "daily_pnl": equity - self._day_start_equity,
            "positions": positions,
            "symbols": {
                sym: {
                    "open_position": state.open_position is not None,
                    "trades": len(state.closed_trades),
                    "equity": sum(state.equity_curve) if state.equity_curve else 0.0,
                }
                for sym, state in self.states.items()
            },
        }


if __name__ == "__main__":
    config = os.environ.get("PERP_V8_CONFIG", "config.yaml")
    runner = LiveRunner(config)
    runner.run()
