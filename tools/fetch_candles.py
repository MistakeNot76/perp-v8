"""
Bitget candle fetcher. Pulls historical OHLCV data from Bitget public API.

No API key needed for public OHLCV. Paginates to cover full date range.

Usage:
    python -m tools.fetch_candles --symbols SOLUSDT,BTCUSDT,ETHUSDT --tf 5m --days 90
    python -m tools.fetch_candles --symbols SOLUSDT --tf 1m --days 30
"""
import argparse
import sys
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

import ccxt
from core.data_loader import _parse_candle, save_candles
from core.timeframes import bars_per_day


# ── Symbol translation ──────────────────────────────────────────────
# Config uses SOLUSDT, ccxt needs SOL/USDT:USDT for Bitget perps.
def to_ccxt_symbol(symbol: str) -> str:
    """SOLUSDT -> SOL/USDT:USDT"""
    symbol = symbol.upper().replace("USDT", "")
    return f"{symbol}/USDT:USDT"


def from_ccxt_symbol(symbol: str) -> str:
    """SOL/USDT:USDT -> SOLUSDT"""
    return symbol.split("/")[0] + "USDT"


# ── Pagination ──────────────────────────────────────────────────────
def fetch_range(exchange: ccxt.Exchange, symbol_ccxt: str, tf: str,
                since_ms: int, until_ms: int, batch_size: int = 200) -> list:
    """Fetch all candles between since_ms and until_ms with pagination.

    Bitget caps fetch_ohlcv at 200 candles per call when using `since`.
    We paginate by advancing cursor to last candle ts + 1ms.
    Break when: empty batch, cursor doesn't advance (infinite loop guard),
    or cursor passes until_ms.
    """
    all_candles = []
    cursor = since_ms
    while cursor < until_ms:
        try:
            batch = exchange.fetch_ohlcv(symbol_ccxt, timeframe=tf, since=cursor, limit=batch_size)
        except ccxt.RateLimitExceeded:
            print(f"    Rate limited, sleeping 2s...", flush=True)
            time.sleep(2)
            continue
        except Exception as e:
            print(f"    ERROR fetching: {e}", flush=True)
            break
        if not batch:
            break
        all_candles.extend(batch)
        new_cursor = batch[-1][0] + 1  # ms after last candle
        if new_cursor <= cursor:
            # Cursor didn't advance — infinite loop guard
            print(f"    WARNING: cursor stalled at {cursor}, stopping", flush=True)
            break
        cursor = new_cursor
        # ccxt rate limiter handles spacing, but add tiny safety
        time.sleep(0.1)
    return all_candles


# ── Dedup ────────────────────────────────────────────────────────────
def deduplicate(candles: list) -> list:
    """Remove duplicate timestamps, keep last occurrence."""
    seen = {}
    for c in candles:
        ts = c[0]
        seen[ts] = c
    return sorted(seen.values(), key=lambda c: c[0])


# ── Main ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Fetch Bitget candle data")
    parser.add_argument("--symbols", required=True,
                        help="Comma-separated symbols (e.g. SOLUSDT,BTCUSDT)")
    parser.add_argument("--tf", default="5m", help="Timeframe (default: 5m)")
    parser.add_argument("--days", type=int, default=90, help="Days of history (default: 90)")
    parser.add_argument("--data-dir", default="data/history", help="Output directory")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        print("ERROR: No symbols provided")
        sys.exit(1)

    exchange = ccxt.bitget({"options": {"defaultType": "swap"}})
    exchange.enableRateLimit = True

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    since_ms = int((datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp() * 1000)

    print(f"=== Fetching {args.days} days of {args.tf} candles from Bitget ===")
    print(f"    Symbols: {', '.join(symbols)}")
    print(f"    Range: {datetime.fromtimestamp(since_ms/1000, tz=timezone.utc).isoformat()}"
          f" → {datetime.fromtimestamp(now_ms/1000, tz=timezone.utc).isoformat()}")
    print()

    for sym in symbols:
        sym_ccxt = to_ccxt_symbol(sym)
        print(f"  {sym} ({sym_ccxt})...", end=" ", flush=True)

        try:
            raw = fetch_range(exchange, sym_ccxt, args.tf, since_ms, now_ms)
        except Exception as e:
            print(f"FAILED: {e}")
            continue

        if not raw:
            print("NO DATA")
            continue

        raw = deduplicate(raw)
        bars = [_parse_candle(c) for c in raw]

        # Verify timestamp range
        first_ts = bars[0].ts
        last_ts = bars[-1].ts
        expected_min = args.days * bars_per_day(args.tf)

        save_candles(sym, args.tf, bars, data_dir=args.data_dir)
        pct = len(bars) / expected_min * 100 if expected_min > 0 else 0
        print(f"{len(bars)} candles ({pct:.0f}% of expected {expected_min})")
        print(f"    First: {datetime.fromtimestamp(first_ts/1000, tz=timezone.utc).isoformat()}")
        print(f"    Last:  {datetime.fromtimestamp(last_ts/1000, tz=timezone.utc).isoformat()}")
        print(f"    Saved: {args.data_dir}/{sym}_{args.tf}.json")

    print("\n=== Done ===")


if __name__ == "__main__":
    main()