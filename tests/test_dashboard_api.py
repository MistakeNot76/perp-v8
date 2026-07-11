"""Tests for dashboard API state/trade normalization helpers."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import json
from dashboard.server import _normalize_record, _derive_open_positions, _build_state


def test_normalize_record_maps_live_fields():
    n = _normalize_record({
        "symbol": "SOLUSDT",
        "action": "CLOSE",
        "direction": "long",
        "entry_price": 100,
        "exit": 110,
        "pnl_net": 9.5,
        "size": 1.0,
        "ts": "2026-01-01T00:00:00",
    })
    assert n["side"] == "long"
    assert n["pnl"] == 9.5
    assert n["exit_price"] == 110
    assert n["entry"] == 100
    assert n["closed_at"] == "2026-01-01T00:00:00"


def test_derive_open_from_unpaired_opens():
    trades = [
        {"symbol": "BTCUSDT", "action": "OPEN", "direction": "short", "entry_price": 50, "size": 2},
        {"symbol": "ETHUSDT", "action": "OPEN", "direction": "long", "entry_price": 10, "size": 1},
        {"symbol": "ETHUSDT", "action": "CLOSE", "direction": "long", "pnl_net": 1},
    ]
    positions = _derive_open_positions(trades, {})
    syms = {p["symbol"] for p in positions}
    assert "BTCUSDT" in syms
    assert "ETHUSDT" not in syms
    btc = next(p for p in positions if p["symbol"] == "BTCUSDT")
    assert btc["side"] == "short"


def test_derive_open_prefers_persisted():
    positions = _derive_open_positions([], {
        "SOLUSDT": {
            "direction": "long",
            "entry_price": 120,
            "size": 0.5,
            "leverage": 15,
            "current_sl": 110,
            "tp": 140,
        }
    })
    assert len(positions) == 1
    assert positions[0]["symbol"] == "SOLUSDT"
    assert positions[0]["entry_price"] == 120
