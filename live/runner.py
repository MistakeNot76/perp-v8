"""
Live trading runner. Main loop: fetch → simulate → execute.
Uses the SAME engine as backtest. Parity guaranteed.
"""
import argparse
import os
import time
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config_loader import (
    load_config,
    get_symbols,
    get_mode,
    get_symbol_config,
    get_strategy_params,
    get_fee_config,
    get_risk_config,
)
from core.indicators import compute_all
from core.engine import EngineState, step
from core.models import Mode, RiskConfig
from core.timeframes import tf_to_minutes
from live.exchange import get_exchange
from live.state import (
    append_signal_log,
    append_trade,
    clear_positions,
    load_positions,
    save_positions,
    ensure_state_dir,
)


class LiveRunner:
    def __init__(
        self,
        config_path: str = "config.yaml",
        fresh: bool = False,
        kill_switch: Optional[bool] = None,
    ):
        self.config_path = config_path
        self.cfg = load_config(config_path)
        self.mode = get_mode(self.cfg)
        self.symbols = get_symbols(self.cfg)
        self.fees = get_fee_config(self.cfg)
        self.risk: RiskConfig = get_risk_config(self.cfg)
        if kill_switch is not None:
            self.risk.kill_switch = kill_switch
        self.exchange = get_exchange(self.mode)
        self.states: dict = {}
        self.running = True
        self.kill_switch = self.risk.kill_switch
        self.fresh = fresh

        # Equity tracking for daily loss / drawdown
        self.start_equity = float(self.exchange.get_balance())
        self.peak_equity = self.start_equity
        self.daily_pnl = 0.0
        self._day_key = self._utc_day_key()

        ensure_state_dir()
        if fresh:
            clear_positions()

        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

    @staticmethod
    def _utc_day_key() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _rollover_day_if_needed(self) -> None:
        key = self._utc_day_key()
        if key != self._day_key:
            self._day_key = key
            self.daily_pnl = 0.0
            self.start_equity = float(self.exchange.get_balance())
            self.peak_equity = max(self.peak_equity, self.start_equity)

    def _shutdown(self, signum, frame):
        print("\nShutdown requested...")
        self.running = False

    def _init_symbol_state(self, symbol: str) -> EngineState:
        sym_cfg = get_symbol_config(self.cfg, symbol)
        strategy_params = get_strategy_params(self.cfg)
        tf = sym_cfg.tf
        bars = self.exchange.fetch_candles(symbol, tf, limit=500)
        indicators = compute_all(bars, strategy_params)
        return EngineState(
            symbol=symbol,
            bars=bars,
            indicators=indicators,
            sym_cfg=sym_cfg,
            fees=self.fees,
            bar_minutes=tf_to_minutes(tf),
        )

    def _restore_positions(self) -> None:
        if self.fresh:
            return
        saved = load_positions()
        for symbol, pos in saved.items():
            if symbol not in self.states:
                self.states[symbol] = self._init_symbol_state(symbol)
            self.states[symbol].open_position = pos
            print(f"[{symbol}] Restored open position from data/state/positions.json")

    def _persist_positions(self) -> None:
        payload = {
            sym: state.open_position
            for sym, state in self.states.items()
        }
        save_positions(payload)

    def _open_count(self) -> int:
        return sum(1 for s in self.states.values() if s.open_position is not None)

    def _open_notional(self) -> float:
        return sum(
            s.open_position.notional
            for s in self.states.values()
            if s.open_position is not None
        )

    def _current_equity(self) -> float:
        return self.start_equity + self.daily_pnl

    def check_risk_allows_entry(self, new_notional: float) -> Tuple[bool, str]:
        """Return (ok, reason). Call before accepting a new engine open."""
        self._rollover_day_if_needed()
        if self.risk.kill_switch or self.kill_switch:
            return False, "kill_switch"
        # New position already counted in engine state when called post-step
        if self._open_count() > self.risk.max_open_positions:
            return False, f"max_open_positions ({self.risk.max_open_positions})"
        if self._open_notional() > self.risk.max_total_notional:
            return False, f"max_total_notional ({self.risk.max_total_notional})"
        loss_limit = -self.risk.max_daily_loss_pct / 100.0 * self.start_equity
        if self.daily_pnl <= loss_limit:
            return False, f"max_daily_loss_pct ({self.risk.max_daily_loss_pct})"
        equity = self._current_equity()
        self.peak_equity = max(self.peak_equity, equity)
        if self.peak_equity > 0:
            dd_pct = (self.peak_equity - equity) / self.peak_equity * 100.0
            if dd_pct >= self.risk.max_drawdown_pct:
                return False, f"max_drawdown_pct ({self.risk.max_drawdown_pct})"
        return True, ""

    def _on_position_update(self, symbol: str, action: str, record: dict):
        append_signal_log(symbol, {"symbol": symbol, "action": action, **record})
        print(f"[{symbol}] {action}: {record}")

    def _apply_entry_fill(self, state: EngineState, fill: dict) -> None:
        pos = state.open_position
        if pos is None:
            return
        fill_price = float(fill["fill_price"])
        if fill_price > 0 and fill_price != pos.entry_price:
            # Preserve TP/SL distances; rebase to fill
            tp_dist = abs(pos.tp - pos.entry_price)
            sl_dist = abs(pos.entry_price - pos.initial_sl)
            pos.entry_price = fill_price
            pos.size = pos.notional / fill_price
            if pos.direction.value == "long":
                pos.tp = fill_price + tp_dist
                pos.initial_sl = fill_price - sl_dist
                pos.current_sl = fill_price - sl_dist
                pos.high_water = fill_price
                pos.low_water = fill_price
            else:
                pos.tp = fill_price - tp_dist
                pos.initial_sl = fill_price + sl_dist
                pos.current_sl = fill_price + sl_dist
                pos.high_water = fill_price
                pos.low_water = fill_price

    def _handle_new_open(self, symbol: str, state: EngineState) -> None:
        pos = state.open_position
        assert pos is not None
        ok, reason = self.check_risk_allows_entry(pos.notional)
        if not ok:
            print(f"[{symbol}] RISK BLOCK entry: {reason}")
            state.open_position = None
            self._persist_positions()
            return

        fill = self.exchange.place_order(
            symbol, pos.direction, pos.size, pos.entry_price
        )
        self._apply_entry_fill(state, fill)
        self._on_position_update(symbol, "OPEN", {
            "direction": pos.direction.value,
            "entry_price": pos.entry_price,
            "size": pos.size,
            "sl": pos.current_sl,
            "tp": pos.tp,
            "order_id": fill.get("id"),
            "fill_price": fill.get("fill_price"),
        })
        self._persist_positions()

    def _handle_close(self, symbol: str, state: EngineState) -> None:
        trade = state.closed_trades[-1]
        fill = self.exchange.close_position(
            symbol, trade.direction, trade.qty, trade.exit_price
        )
        # Prefer exchange fill for accounting when it differs
        fill_price = float(fill.get("fill_price", trade.exit_price))
        if abs(fill_price - trade.exit_price) > 1e-12:
            # Adjust net PnL for fill difference (simple rebase of raw)
            if trade.direction.value == "long":
                delta = (fill_price - trade.exit_price) * trade.qty
            else:
                delta = (trade.exit_price - fill_price) * trade.qty
            trade.exit_price = fill_price
            trade.pnl_raw += delta
            trade.pnl_net += delta

        self.daily_pnl += trade.pnl_net
        equity = self._current_equity()
        self.peak_equity = max(self.peak_equity, equity)

        self._on_position_update(symbol, "CLOSE", {
            "direction": trade.direction.value,
            "entry": trade.entry_price,
            "exit": trade.exit_price,
            "pnl_net": trade.pnl_net,
            "reason": trade.reason.value,
            "bars_held": trade.bars_held,
            "order_id": fill.get("id"),
            "fill_price": fill.get("fill_price"),
        })
        append_trade(trade)
        self._persist_positions()

    def run(self):
        print(
            f"=== LiveRunner mode={self.mode.value} symbols={self.symbols} "
            f"kill={self.kill_switch} fresh={self.fresh} ==="
        )
        # Init all symbols then restore positions
        for symbol in self.symbols:
            if symbol not in self.states:
                self.states[symbol] = self._init_symbol_state(symbol)
        self._restore_positions()

        while self.running:
            if self.kill_switch or self.risk.kill_switch:
                print("Kill switch ON. Halting.")
                break
            try:
                self._rollover_day_if_needed()
                for symbol in self.symbols:
                    if symbol not in self.states:
                        self.states[symbol] = self._init_symbol_state(symbol)
                    state = self.states[symbol]
                    new_bars = self.exchange.fetch_candles(symbol, state.sym_cfg.tf, limit=1)
                    if new_bars and new_bars[-1].ts > state.bars[-1].ts:
                        state.bars.append(new_bars[-1])
                        state.indicators = compute_all(state.bars, get_strategy_params(self.cfg))
                        had_position = state.open_position is not None
                        before_count = len(state.closed_trades)
                        step(state, len(state.bars) - 1)
                        # flat → open
                        if state.open_position is not None and not had_position:
                            self._handle_new_open(symbol, state)
                        # open → closed
                        if len(state.closed_trades) > before_count:
                            self._handle_close(symbol, state)
                time.sleep(5)
            except Exception as e:
                print(f"Error: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(10)

    def get_state_snapshot(self) -> dict:
        snapshot = {
            "mode": self.mode.value,
            "kill_switch": self.kill_switch or self.risk.kill_switch,
            "daily_pnl": self.daily_pnl,
            "equity": self._current_equity(),
            "peak_equity": self.peak_equity,
            "open_positions": self._open_count(),
            "open_notional": self._open_notional(),
            "symbols": {},
        }
        for sym, state in self.states.items():
            snapshot["symbols"][sym] = {
                "open_position": state.open_position is not None,
                "trades": len(state.closed_trades),
                "equity": sum(state.equity_curve),
            }
        return snapshot


def main(argv=None):
    parser = argparse.ArgumentParser(description="perp-v8 live runner")
    parser.add_argument("--config", default=os.environ.get("PERP_V8_CONFIG", "config.yaml"))
    parser.add_argument("--fresh", action="store_true", help="Ignore saved state; start flat")
    parser.add_argument("--kill", action="store_true", help="Force kill switch ON and halt")
    args = parser.parse_args(argv)
    runner = LiveRunner(config_path=args.config, fresh=args.fresh, kill_switch=True if args.kill else None)
    runner.run()


if __name__ == "__main__":
    main()
