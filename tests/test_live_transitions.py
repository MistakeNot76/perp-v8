"""Tests for live runner transition detection (OPEN spam fix)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.models import Direction


def test_open_logged_only_on_flat_to_open_transition():
    """Mirrors live/runner.py transition logic without needing exchange I/O."""
    class FakePos:
        direction = Direction.LONG
        entry_price = 100.0
        size = 1.0
        current_sl = 94.0
        tp = 110.0

    class State:
        def __init__(self):
            self.open_position = None
            self.closed_trades = []

    state = State()
    opens = []
    closes = []

    def on_update(action, record):
        if action == "OPEN":
            opens.append(record)
        else:
            closes.append(record)

    # Bar 1: flat → open
    had_position = state.open_position is not None
    before_count = len(state.closed_trades)
    state.open_position = FakePos()  # simulate step opening
    if state.open_position is not None and not had_position:
        on_update("OPEN", {"entry_price": state.open_position.entry_price})
    if len(state.closed_trades) > before_count:
        on_update("CLOSE", {})

    # Bar 2: still open — must NOT log OPEN again
    had_position = state.open_position is not None
    before_count = len(state.closed_trades)
    # step leaves position open
    if state.open_position is not None and not had_position:
        on_update("OPEN", {"entry_price": state.open_position.entry_price})
    if len(state.closed_trades) > before_count:
        on_update("CLOSE", {})

    # Bar 3: close
    had_position = state.open_position is not None
    before_count = len(state.closed_trades)
    state.closed_trades.append({"pnl": 1})
    state.open_position = None
    if state.open_position is not None and not had_position:
        on_update("OPEN", {})
    if len(state.closed_trades) > before_count:
        on_update("CLOSE", {"pnl_net": 1})

    assert len(opens) == 1
    assert len(closes) == 1
