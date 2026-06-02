"""Candle loading and resampling. Pure data layer."""
from pathlib import Path
from typing import List, Optional
import json

from core.models import Bar


def _parse_candle(raw) -> Bar:
    if isinstance(raw, dict):
        return Bar(
            ts=int(raw["ts"]),
            open=float(raw["open"]),
            high=float(raw["high"]),
            low=float(raw["low"]),
            close=float(raw["close"]),
            volume=float(raw.get("volume", 0)),
        )
    if isinstance(raw, list) and len(raw) >= 6:
        return Bar(
            ts=int(raw[0]),
            open=float(raw[1]),
            high=float(raw[2]),
            low=float(raw[3]),
            close=float(raw[4]),
            volume=float(raw[5]),
        )
    raise ValueError(f"Unknown candle format: {raw}")


def load_candles(symbol: str, tf: str, data_dir: str = "data/history") -> List[Bar]:
    """Load OHLCV candles from JSON file."""
    p = Path(data_dir) / f"{symbol.upper()}_{tf}.json"
    if not p.exists():
        raise FileNotFoundError(f"No data for {symbol} {tf} at {p}")
    with open(p) as f:
        raw = json.load(f)
    if isinstance(raw, dict) and "candles" in raw:
        raw = raw["candles"]
    return [_parse_candle(c) for c in raw]


def resample(bars_1m: List[Bar], target_minutes: int) -> List[Bar]:
    """Aggregate 1-minute bars into higher timeframe bars."""
    if target_minutes <= 0 or target_minutes == 1:
        return bars_1m
    ms_target = target_minutes * 60 * 1000
    if not bars_1m:
        return []
    out: List[Bar] = []
    bucket: List[Bar] = []
    bucket_start = (bars_1m[0].ts // ms_target) * ms_target
    for b in bars_1m:
        b_start = (b.ts // ms_target) * ms_target
        if b_start != bucket_start:
            if bucket:
                out.append(_aggregate(bucket, bucket_start))
            bucket = [b]
            bucket_start = b_start
        else:
            bucket.append(b)
    if bucket:
        out.append(_aggregate(bucket, bucket_start))
    return out


def _aggregate(bucket: List[Bar], ts: int) -> Bar:
    return Bar(
        ts=ts,
        open=bucket[0].open,
        high=max(b.high for b in bucket),
        low=min(b.low for b in bucket),
        close=bucket[-1].close,
        volume=sum(b.volume for b in bucket),
    )


def save_candles(symbol: str, tf: str, bars: List[Bar], data_dir: str = "data/history") -> None:
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    p = Path(data_dir) / f"{symbol.upper()}_{tf}.json"
    out = [
        {"ts": b.ts, "open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume}
        for b in bars
    ]
    with open(p, "w") as f:
        json.dump(out, f)
