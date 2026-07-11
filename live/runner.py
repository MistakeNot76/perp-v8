"""
Live trading runner. Main loop: fetch → simulate → execute.
Uses the SAME engine as backtest. Parity guaranteed.
"""
import os
import time
import signal
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config_loader import load_config, get_symbols, get_mode, get_symbol_config, get_strategy_params, get_fee_config
from core.indicators import compute_all
from core.engine import EngineState, step, run_bars
from core.models import Mode, Direction, Bar, Trade, ExitReason
from core.timeframes import tf_to_minutes
from live.exchange import get_exchange
from live.state import append_signal_log, load_signal_log


class LiveRunner:
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = config_path
        self.cfg = load_config(config_path)
        self.mode = get_mode(self.cfg)
        self.symbols = get_symbols(self.cfg)
        self.fees = get_fee_config(self.cfg)
        self.exchange = get_exchange(self.mode)
        self.states: dict = {}
        self.running = True
        self.kill_switch = self.cfg["risk"]["kill_switch"]

        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

    def _shutdown(self, signum, frame):
        print("\nShutdown requested...")
        self.running = False

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

    def _on_position_update(self, symbol: str, action: str, record: dict):
        append_signal_log(symbol, {"symbol": symbol, "action": action, **record})
        print(f"[{symbol}] {action}: {record}")

    def run(self):
        print(f"=== LiveRunner mode={self.mode.value} symbols={self.symbols} ===")
        while self.running:
            if self.kill_switch:
                print("Kill switch ON. Halting.")
                break
            try:
                for symbol in self.symbols:
                    if symbol not in self.states:
                        self.states[symbol] = self._init_symbol_state(symbol)
                    state = self.states[symbol]
                    new_bars = self.exchange.fetch_candles(symbol, state.sym_cfg.tf, limit=1)
                    if new_bars and new_bars[-1].ts > state.bars[-1].ts:
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
                        step(state, len(state.bars) - 1)
                        # Only log OPEN on flat → open transition (not every bar while open)
                        if state.open_position is not None and not had_position:
                            self._on_position_update(symbol, "OPEN", {
                                "direction": state.open_position.direction.value,
                                "entry_price": state.open_position.entry_price,
                                "size": state.open_position.size,
                                "sl": state.open_position.current_sl,
                                "tp": state.open_position.tp,
                                "entry_reason": state.open_position.entry_reason,
                            })
                        if len(state.closed_trades) > before_count:
                            trade = state.closed_trades[-1]
                            self._on_position_update(symbol, "CLOSE", {
                                "direction": trade.direction.value,
                                "entry": trade.entry_price,
                                "exit": trade.exit_price,
                                "pnl_net": trade.pnl_net,
                                "reason": trade.reason.value,
                                "entry_reason": trade.entry_reason,
                                "bars_held": trade.bars_held,
                            })
                time.sleep(5)
            except Exception as e:
                print(f"Error: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(10)

    def get_state_snapshot(self) -> dict:
        snapshot = {
            "mode": self.mode.value,
            "kill_switch": self.kill_switch,
            "symbols": {},
        }
        for sym, state in self.states.items():
            snapshot["symbols"][sym] = {
                "open_position": state.open_position is not None,
                "trades": len(state.closed_trades),
                "equity": sum(state.equity_curve),
            }
        return snapshot


if __name__ == "__main__":
    config = os.environ.get("PERP_V8_CONFIG", "config.yaml")
    runner = LiveRunner(config)
    runner.run()
