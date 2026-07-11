"""Tests for live risk limits, persistence, and fee maker/taker."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import pytest

from live.risk import check_risk_limits, can_open_new, set_kill_switch
from live.state import (
    serialize_position,
    deserialize_position,
    save_open_positions,
    load_open_positions,
    append_signal_log,
    load_signal_log,
)
from live.exchange import PaperExchange, get_exchange
from core.models import Direction, Mode, Position, FeeConfig


def test_risk_daily_loss_breach():
    cfg = {
        "risk": {"max_daily_loss_pct": 5.0, "max_drawdown_pct": 50.0, "kill_switch": False},
        "execution": {"max_open_positions": 20, "max_total_notional": 4000},
    }
    snap = check_risk_limits(
        cfg,
        equity=9400,
        peak_equity=10000,
        daily_pnl=-600,
        open_positions=0,
        total_notional=0,
        starting_equity=10000,
    )
    assert snap.breach is not None
    assert "max_daily_loss" in snap.breach
    assert snap.kill_switch is True


def test_risk_drawdown_breach():
    cfg = {
        "risk": {"max_daily_loss_pct": 50.0, "max_drawdown_pct": 15.0, "kill_switch": False},
        "execution": {"max_open_positions": 20, "max_total_notional": 4000},
    }
    snap = check_risk_limits(
        cfg,
        equity=8000,
        peak_equity=10000,
        daily_pnl=-100,
        open_positions=0,
        total_notional=0,
        starting_equity=10000,
    )
    assert snap.breach is not None
    assert "max_drawdown" in snap.breach


def test_can_open_new_caps():
    cfg = {"execution": {"max_open_positions": 2, "max_total_notional": 500}}
    ok, _ = can_open_new(cfg, open_positions=1, total_notional=100, new_notional=100)
    assert ok
    ok, reason = can_open_new(cfg, open_positions=2, total_notional=100, new_notional=100)
    assert not ok
    assert "max_open_positions" in reason
    ok, reason = can_open_new(cfg, open_positions=0, total_notional=450, new_notional=100)
    assert not ok
    assert "max_total_notional" in reason


def test_set_kill_switch():
    cfg = {"risk": {"kill_switch": False}}
    out = set_kill_switch(cfg, True)
    assert out["risk"]["kill_switch"] is True
    assert cfg["risk"]["kill_switch"] is False  # original untouched


def test_position_roundtrip(tmp_path, monkeypatch):
    pos = Position(
        symbol="SOLUSDT",
        direction=Direction.LONG,
        entry_price=100.0,
        entry_bar_idx=10,
        entry_ts=1,
        notional=100.0,
        leverage=15,
        size=1.0,
        initial_sl=94.0,
        current_sl=94.0,
        tp=110.0,
        high_water=101.0,
        low_water=99.0,
        bars_held=3,
        partial_tp_hit=True,
        partial_tp_qty=0.5,
        entry_reason="test",
    )
    path = tmp_path / "open_positions.json"
    save_open_positions({"SOLUSDT": pos}, path=path)
    loaded = load_open_positions(path=path)
    assert "SOLUSDT" in loaded
    assert loaded["SOLUSDT"].entry_price == 100.0
    assert loaded["SOLUSDT"].partial_tp_hit is True
    assert loaded["SOLUSDT"].direction == Direction.LONG


def test_signal_log_canonical_fields(tmp_path):
    path = tmp_path / "signal_log.jsonl"
    append_signal_log("BTCUSDT", {
        "action": "CLOSE",
        "direction": "long",
        "entry_price": 50,
        "exit": 55,
        "pnl_net": 4.5,
        "size": 0.1,
    }, path=str(path))
    rows = load_signal_log(str(path))
    assert len(rows) == 1
    r = rows[0]
    assert r["side"] == "long"
    assert r["pnl"] == 4.5
    assert r["exit_price"] == 55
    assert r["entry"] == 50


def test_paper_exchange_orders():
    ex = PaperExchange()
    oid = ex.place_order("SOLUSDT", Direction.LONG, 1.0, 100.0)
    assert oid.startswith("paper-")
    assert len(ex.get_positions()) == 1
    ex.close_position("SOLUSDT", Direction.LONG, 1.0, 105.0)
    assert len(ex.get_positions()) == 0


def test_get_exchange_paper():
    ex = get_exchange(Mode.PAPER)
    assert isinstance(ex, PaperExchange)


def test_maker_vs_taker_fees():
    fees = FeeConfig(maker_pct=0.02, taker_pct=0.06)
    assert fees.entry_cost(1000) == pytest.approx(0.6)  # taker default
    assert fees.entry_cost(1000, is_maker=True) == pytest.approx(0.2)
    assert fees.exit_cost(1000, is_maker=True) == pytest.approx(0.2)
    assert fees.exit_cost(1000, is_maker=False) == pytest.approx(0.6)


def test_serialize_deserialize_position():
    pos = Position(
        symbol="ETHUSDT",
        direction=Direction.SHORT,
        entry_price=2000.0,
        entry_bar_idx=5,
        entry_ts=99,
        notional=200.0,
        leverage=10,
        size=0.1,
        initial_sl=2100.0,
        current_sl=2050.0,
        tp=1800.0,
        high_water=1990.0,
        low_water=2010.0,
    )
    raw = serialize_position(pos)
    back = deserialize_position(raw)
    assert back is not None
    assert back.symbol == "ETHUSDT"
    assert back.direction == Direction.SHORT
    assert back.current_sl == 2050.0
