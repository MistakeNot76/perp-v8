"""
Exchange wrapper. Abstracted behind Mode selector.
Paper: simulated fills
Demo: real Bitget demo (when available)
Live: real Bitget API
"""
from typing import Any, Dict, Optional
from core.models import Direction, Mode, Bar


class ExchangeBase:
    def fetch_candles(self, symbol: str, tf: str, limit: int = 200) -> list:
        raise NotImplementedError

    def place_order(self, symbol: str, direction: Direction, qty: float, price: float) -> Dict[str, Any]:
        """Place an order. Returns fill dict with id, fill_price, qty."""
        raise NotImplementedError

    def close_position(self, symbol: str, direction: Direction, qty: float, price: float) -> Dict[str, Any]:
        """Close a position. Returns fill dict with id, fill_price, qty."""
        raise NotImplementedError

    def get_balance(self) -> float:
        raise NotImplementedError


class PaperExchange(ExchangeBase):
    """Simulated exchange. Fills at the requested price and tracks positions."""

    def __init__(self, data_dir: str = "data/history"):
        self.data_dir = data_dir
        self._orders: list = []
        self._positions: Dict[str, dict] = {}

    def fetch_candles(self, symbol: str, tf: str, limit: int = 200) -> list:
        from core.data_loader import load_candles
        return load_candles(symbol, tf, self.data_dir)[-limit:]

    def place_order(self, symbol, direction, qty, price) -> Dict[str, Any]:
        oid = f"paper-{len(self._orders)}"
        fill = {
            "id": oid,
            "symbol": symbol,
            "side": direction.value,
            "qty": qty,
            "fill_price": price,
        }
        self._orders.append(fill)
        self._positions[symbol] = {
            "direction": direction,
            "qty": qty,
            "entry_price": price,
            "order_id": oid,
        }
        return fill

    def close_position(self, symbol, direction, qty, price) -> Dict[str, Any]:
        # Closing a long = sell; closing a short = buy
        close_dir = Direction.SHORT if direction == Direction.LONG else Direction.LONG
        oid = f"paper-{len(self._orders)}"
        fill = {
            "id": oid,
            "symbol": symbol,
            "side": close_dir.value,
            "qty": qty,
            "fill_price": price,
        }
        self._orders.append(fill)
        self._positions.pop(symbol, None)
        return fill

    def get_balance(self) -> float:
        return 10000.0

    def get_open_position(self, symbol: str) -> Optional[dict]:
        return self._positions.get(symbol)


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

    def place_order(self, symbol, direction, qty, price) -> Dict[str, Any]:
        client = self._get_client()
        side = "buy" if direction == Direction.LONG else "sell"
        order = client.create_order(symbol, "market", side, qty)
        fill_price = float(order.get("average") or order.get("price") or price)
        return {
            "id": order["id"],
            "symbol": symbol,
            "side": direction.value,
            "qty": qty,
            "fill_price": fill_price,
        }

    def close_position(self, symbol, direction, qty, price) -> Dict[str, Any]:
        client = self._get_client()
        # Opposite side to flatten
        side = "sell" if direction == Direction.LONG else "buy"
        order = client.create_order(symbol, "market", side, qty, params={"reduceOnly": True})
        fill_price = float(order.get("average") or order.get("price") or price)
        return {
            "id": order["id"],
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "fill_price": fill_price,
        }

    def get_balance(self) -> float:
        client = self._get_client()
        bal = client.fetch_balance()
        return float(bal.get("USDT", {}).get("free", 0))


def get_exchange(mode: Mode, api_key: str = "", api_secret: str = "", api_passphrase: str = "") -> ExchangeBase:
    if mode == Mode.PAPER:
        return PaperExchange()
    return BitgetExchange(mode, api_key, api_secret, api_passphrase)
