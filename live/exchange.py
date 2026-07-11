"""
Exchange wrapper. Abstracted behind Mode selector.
Paper: simulated fills
Demo: real Bitget demo (when available)
Live: real Bitget API

API keys are loaded from environment (never hardcoded):
  BITGET_API_KEY, BITGET_API_SECRET, BITGET_API_PASSPHRASE
"""
from __future__ import annotations

import os
from typing import List, Optional

from core.models import Direction, Mode, Bar


class ExchangeBase:
    def fetch_candles(self, symbol: str, tf: str, limit: int = 200) -> list:
        raise NotImplementedError

    def place_order(self, symbol: str, direction: Direction, qty: float, price: float) -> str:
        raise NotImplementedError

    def close_position(self, symbol: str, direction: Direction, qty: float, price: float) -> str:
        raise NotImplementedError

    def get_balance(self) -> float:
        raise NotImplementedError

    def get_positions(self) -> List[dict]:
        """Return open exchange positions: [{symbol, side, qty, entry_price, ...}]."""
        return []

    def get_mark_price(self, symbol: str) -> Optional[float]:
        return None


class PaperExchange(ExchangeBase):
    """Simulated exchange. Candles from local history; fills are recorded in-memory."""

    def __init__(self, data_dir: str = "data/history", starting_balance: float = 10000.0):
        self.data_dir = data_dir
        self._orders: list = []
        self._balance = starting_balance
        self._positions: dict = {}  # symbol -> {side, qty, entry_price}

    def fetch_candles(self, symbol: str, tf: str, limit: int = 200) -> list:
        from core.data_loader import load_candles
        return load_candles(symbol, tf, self.data_dir)[-limit:]

    def place_order(self, symbol, direction, qty, price) -> str:
        oid = f"paper-{len(self._orders)}"
        self._orders.append({
            "id": oid, "symbol": symbol, "side": direction.value,
            "qty": qty, "price": price, "action": "open",
        })
        self._positions[symbol] = {
            "symbol": symbol,
            "side": direction.value,
            "qty": qty,
            "entry_price": price,
        }
        return oid

    def close_position(self, symbol, direction, qty, price) -> str:
        oid = f"paper-{len(self._orders)}"
        self._orders.append({
            "id": oid, "symbol": symbol, "side": direction.value,
            "qty": qty, "price": price, "action": "close",
        })
        cur = self._positions.get(symbol)
        if cur:
            remaining = float(cur["qty"]) - float(qty)
            if remaining <= 1e-12:
                self._positions.pop(symbol, None)
            else:
                cur["qty"] = remaining
        return oid

    def get_balance(self) -> float:
        return self._balance

    def get_positions(self) -> List[dict]:
        return list(self._positions.values())

    def get_mark_price(self, symbol: str) -> Optional[float]:
        try:
            bars = self.fetch_candles(symbol, "15m", limit=1)
            return bars[-1].close if bars else None
        except Exception:
            return None


class BitgetExchange(ExchangeBase):
    """Real Bitget API. Used for demo and live modes."""

    def __init__(self, mode: Mode, api_key: str = "", api_secret: str = "", api_passphrase: str = ""):
        self.mode = mode
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import ccxt
            except ImportError:
                raise ImportError("ccxt required for demo/live modes: pip install ccxt")
            if not self.api_key or not self.api_secret:
                raise ValueError(
                    "Bitget API keys required. Set BITGET_API_KEY, BITGET_API_SECRET, "
                    "BITGET_API_PASSPHRASE environment variables."
                )
            self._client = ccxt.bitget({
                "apiKey": self.api_key,
                "secret": self.api_secret,
                "password": self.api_passphrase,
                "options": {"defaultType": "swap", "sandboxMode": self.mode == Mode.DEMO},
            })
        return self._client

    def fetch_candles(self, symbol: str, tf: str, limit: int = 200) -> list:
        client = self._get_client()
        raw = client.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
        from core.data_loader import _parse_candle
        return [_parse_candle(c) for c in raw]

    def place_order(self, symbol, direction, qty, price) -> str:
        client = self._get_client()
        side = "buy" if direction == Direction.LONG else "sell"
        order = client.create_order(symbol, "market", side, qty)
        return order["id"]

    def close_position(self, symbol, direction, qty, price) -> str:
        # Opposite side to flatten
        client = self._get_client()
        side = "sell" if direction == Direction.LONG else "buy"
        order = client.create_order(symbol, "market", side, qty, params={"reduceOnly": True})
        return order["id"]

    def get_balance(self) -> float:
        client = self._get_client()
        bal = client.fetch_balance()
        return float(bal.get("USDT", {}).get("free", 0) or 0)

    def get_positions(self) -> List[dict]:
        client = self._get_client()
        raw = client.fetch_positions()
        out = []
        for p in raw or []:
            contracts = float(p.get("contracts") or p.get("contractSize") or 0)
            if abs(contracts) < 1e-12:
                continue
            side = (p.get("side") or "").lower()
            if side not in ("long", "short"):
                side = "long" if contracts > 0 else "short"
            out.append({
                "symbol": p.get("symbol") or p.get("info", {}).get("symbol"),
                "side": side,
                "qty": abs(contracts),
                "entry_price": float(p.get("entryPrice") or 0),
                "unrealized_pnl": float(p.get("unrealizedPnl") or 0),
                "leverage": float(p.get("leverage") or 0),
                "mark_price": float(p.get("markPrice") or 0) if p.get("markPrice") else None,
            })
        return out

    def get_mark_price(self, symbol: str) -> Optional[float]:
        try:
            client = self._get_client()
            t = client.fetch_ticker(symbol)
            return float(t.get("last") or t.get("close") or 0) or None
        except Exception:
            return None


def _env_keys() -> tuple:
    return (
        os.environ.get("BITGET_API_KEY", ""),
        os.environ.get("BITGET_API_SECRET", ""),
        os.environ.get("BITGET_API_PASSPHRASE", ""),
    )


def get_exchange(
    mode: Mode,
    api_key: str = "",
    api_secret: str = "",
    api_passphrase: str = "",
    data_dir: str = "data/history",
) -> ExchangeBase:
    if mode == Mode.PAPER:
        return PaperExchange(data_dir=data_dir)
    key, secret, passphrase = api_key, api_secret, api_passphrase
    if not key:
        key, secret, passphrase = _env_keys()
    return BitgetExchange(mode, key, secret, passphrase)
