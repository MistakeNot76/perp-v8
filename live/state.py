"""Position state persistence for live engine."""
from pathlib import Path
from typing import List, Optional
import json
from datetime import datetime

from core.models import Position, Trade, Direction, ExitReason


def serialize_position(pos: Optional[Position]) -> Optional[dict]:
    if pos is None:
        return None
    return {
        "symbol": pos.symbol,
        "direction": pos.direction.value,
        "entry_price": pos.entry_price,
        "entry_bar_idx": pos.entry_bar_idx,
        "entry_ts": pos.entry_ts,
        "notional": pos.notional,
        "leverage": pos.leverage,
        "size": pos.size,
        "initial_sl": pos.initial_sl,
        "current_sl": pos.current_sl,
        "tp": pos.tp,
        "high_water": pos.high_water,
        "low_water": pos.low_water,
        "bars_held": pos.bars_held,
        "partial_tp_hit": pos.partial_tp_hit,
    }


def deserialize_position(data: Optional[dict]) -> Optional[Position]:
    if data is None:
        return None
    return Position(
        symbol=data["symbol"],
        direction=Direction(data["direction"]),
        entry_price=data["entry_price"],
        entry_bar_idx=data["entry_bar_idx"],
        entry_ts=data["entry_ts"],
        notional=data["notional"],
        leverage=data["leverage"],
        size=data["size"],
        initial_sl=data["initial_sl"],
        current_sl=data["current_sl"],
        tp=data["tp"],
        high_water=data["high_water"],
        low_water=data["low_water"],
        bars_held=data["bars_held"],
        partial_tp_hit=data.get("partial_tp_hit", False),
    )


def append_signal_log(symbol: str, record: dict, path: str = "data/logs/signal_log.jsonl") -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    record["ts"] = datetime.now().isoformat()
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def load_signal_log(path: str = "data/logs/signal_log.jsonl") -> List[dict]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out
