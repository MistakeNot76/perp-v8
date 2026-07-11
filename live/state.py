"""Position state persistence for live engine."""
from pathlib import Path
from typing import Dict, List, Optional
import json
from datetime import datetime

from core.models import Position, Direction


POSITIONS_PATH = Path("data/logs/open_positions.json")
SIGNAL_LOG_PATH = Path("data/logs/signal_log.jsonl")


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
        "partial_tp_qty": pos.partial_tp_qty,
        "entry_reason": pos.entry_reason,
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
        partial_tp_qty=data.get("partial_tp_qty", 0.0),
        entry_reason=data.get("entry_reason", ""),
    )


def save_open_positions(positions: Dict[str, Optional[Position]], path: Path = POSITIONS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now().isoformat(),
        "positions": {
            sym: serialize_position(pos) for sym, pos in positions.items() if pos is not None
        },
    }
    path.write_text(json.dumps(payload, indent=2))


def load_open_positions(path: Path = POSITIONS_PATH) -> Dict[str, Position]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    out: Dict[str, Position] = {}
    for sym, raw in (data.get("positions") or {}).items():
        pos = deserialize_position(raw)
        if pos is not None:
            out[sym] = pos
    return out


def append_signal_log(symbol: str, record: dict, path: str = str(SIGNAL_LOG_PATH)) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    record = dict(record)
    record.setdefault("symbol", symbol)
    record["ts"] = datetime.now().isoformat()
    # Canonical fields for dashboard
    direction = record.get("direction") or record.get("side")
    if direction:
        record["direction"] = str(direction).lower()
        record["side"] = record["direction"]
    if "pnl_net" in record and "pnl" not in record:
        record["pnl"] = record["pnl_net"]
    if "entry_price" in record and "entry" not in record:
        record["entry"] = record["entry_price"]
    if "exit" in record and "exit_price" not in record:
        record["exit_price"] = record["exit"]
    if "exit_price" in record and "exit" not in record:
        record["exit"] = record["exit_price"]
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def load_signal_log(path: str = str(SIGNAL_LOG_PATH)) -> List[dict]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out
