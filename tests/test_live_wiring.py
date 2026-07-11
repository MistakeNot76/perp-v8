"""Tests for P0 live wiring: risk, exchange fills, state persistence."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from core.models import Direction, FeeConfig, Mode, Position
from core.config_loader import get_risk_config
from live.exchange import PaperExchange
from live.state import (
    save_positions,
    load_positions,
    clear_positions,
)
from live.runner import LiveRunner


def _minimal_cfg(**risk_overrides):
    return {
        "system": {"mode": "paper", "data_dir": "data/history"},
        "execution": {
            "leverage": 15,
            "notional_per_trade": 100,
            "max_total_notional": 4000,
            "max_open_positions": 2,
            "partial_tp": {"enabled": False, "pct": 0.5, "r_multiple": 1.0},
        },
        "risk": {
            "max_daily_loss_pct": 5.0,
            "max_drawdown_pct": 15.0,
            "kill_switch": False,
            **risk_overrides,
        },
        "strategy": {
            "tf": "15m",
            "fvb_length": 20,
            "fvb_band_mult": 1.5,
            "bxt_l1": 5, "bxt_l2": 30, "bxt_l3": 5, "bxt_ll1": 30, "bxt_ll2": 8,
            "hurst_window": 100, "adx_period": 14, "adx_max": 30, "adx_trend_max": 35,
            "rsi2_oversold": 10, "rsi2_overbought": 90, "hurst_max": 0.85,
            "confirmation_bars": 6,
        },
        "exits": {
            "tp_atr_mult": 2.0, "sl_atr_mult": 1.5,
            "min_tp_pct": 2.0, "min_sl_pct": 1.0,
            "breakeven_bars": 8, "trail_after_be": 1.0, "max_bars": 200,
        },
        "fees": {
            "maker_pct": 0.02, "taker_pct": 0.06,
            "slippage_pct": 0.05, "funding_pct_per_8h": 0.01,
        },
        "symbols": ["SOLUSDT"],
        "dashboard": {"port": 9125},
    }


def _bare_runner(monkeypatch, cfg) -> LiveRunner:
    class FakeEx:
        def get_balance(self):
            return 10000.0

        def fetch_candles(self, *a, **k):
            return []

    monkeypatch.setattr("live.runner.get_exchange", lambda mode: FakeEx())
    monkeypatch.setattr("live.runner.load_config", lambda path: cfg)
    monkeypatch.setattr("live.runner.clear_positions", lambda: None)
    monkeypatch.setattr("live.runner.ensure_state_dir", lambda: None)

    runner = LiveRunner.__new__(LiveRunner)
    runner.cfg = cfg
    runner.mode = Mode.PAPER
    runner.symbols = cfg["symbols"]
    runner.fees = FeeConfig()
    runner.risk = get_risk_config(cfg)
    runner.exchange = FakeEx()
    runner.states = {}
    runner.running = True
    runner.kill_switch = runner.risk.kill_switch
    runner.fresh = True
    runner.start_equity = 10000.0
    runner.peak_equity = 10000.0
    runner.daily_pnl = 0.0
    runner._day_key = runner._utc_day_key()
    return runner


def test_get_risk_config_reads_execution_caps():
    cfg = _minimal_cfg()
    risk = get_risk_config(cfg)
    assert risk.max_open_positions == 2
    assert risk.max_total_notional == 4000
    assert risk.max_daily_loss_pct == 5.0
    assert risk.kill_switch is False


def test_paper_exchange_tracks_position_and_fill():
    ex = PaperExchange()
    fill = ex.place_order("SOLUSDT", Direction.LONG, qty=1.5, price=100.0)
    assert fill["fill_price"] == 100.0
    assert fill["id"].startswith("paper-")
    assert ex.get_open_position("SOLUSDT")["qty"] == 1.5
    close = ex.close_position("SOLUSDT", Direction.LONG, qty=1.5, price=105.0)
    assert close["fill_price"] == 105.0
    assert ex.get_open_position("SOLUSDT") is None


def test_position_persistence_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("live.state.STATE_DIR", tmp_path)
    monkeypatch.setattr("live.state.POSITIONS_PATH", tmp_path / "positions.json")
    clear_positions(tmp_path / "positions.json")

    pos = Position(
        symbol="SOLUSDT", direction=Direction.LONG, entry_price=100,
        entry_bar_idx=1, entry_ts=1, notional=100, leverage=15, size=1.0,
        initial_sl=94, current_sl=94, tp=110, high_water=100, low_water=100,
    )
    save_positions({"SOLUSDT": pos}, tmp_path / "positions.json")
    loaded = load_positions(tmp_path / "positions.json")
    assert "SOLUSDT" in loaded
    assert loaded["SOLUSDT"].entry_price == 100
    assert loaded["SOLUSDT"].direction == Direction.LONG


def test_risk_blocks_when_max_positions_reached(monkeypatch):
    cfg = _minimal_cfg()
    runner = _bare_runner(monkeypatch, cfg)

    class FakePos:
        notional = 100

    class FakeState:
        def __init__(self):
            self.open_position = FakePos()

    # 3 open while max_open_positions=2 → block
    runner.states = {"A": FakeState(), "B": FakeState(), "C": FakeState()}
    ok, reason = runner.check_risk_allows_entry(100)
    assert ok is False
    assert "max_open_positions" in reason


def test_risk_blocks_kill_switch(monkeypatch):
    cfg = _minimal_cfg(kill_switch=True)
    runner = _bare_runner(monkeypatch, cfg)
    ok, reason = runner.check_risk_allows_entry(100)
    assert ok is False
    assert reason == "kill_switch"


def test_risk_blocks_daily_loss(monkeypatch):
    cfg = _minimal_cfg()
    runner = _bare_runner(monkeypatch, cfg)
    runner.daily_pnl = -600  # 6% of 10k, limit is 5%
    ok, reason = runner.check_risk_allows_entry(100)
    assert ok is False
    assert "max_daily_loss_pct" in reason


def test_risk_allows_when_under_limits(monkeypatch):
    cfg = _minimal_cfg()
    runner = _bare_runner(monkeypatch, cfg)

    class FakePos:
        notional = 100

    class FakeState:
        def __init__(self, open_pos=False):
            self.open_position = FakePos() if open_pos else None

    runner.states = {"A": FakeState(True), "B": FakeState(False)}
    ok, reason = runner.check_risk_allows_entry(100)
    assert ok is True
    assert reason == ""
