"""Entry-rule bug-killers: outer FVB bands + correct BXT direction."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.entry_rules import check_entry, _crossover_above, _crossover_below
from core.models import Indicators, Direction, SymbolConfig


def _sym(**kwargs) -> SymbolConfig:
    defaults = dict(
        tf="15m",
        leverage=15,
        notional=100,
        min_tp_pct=2.0,
        min_sl_pct=1.0,
        tp_atr_mult=2.0,
        sl_atr_mult=1.5,
        confirmation_bars=3,
        breakeven_bars=8,
        trail_after_be=1.0,
        max_bars=200,
        adx_max=50.0,
        adx_trend_max=60.0,
        rsi2_oversold=20.0,
        rsi2_overbought=80.0,
        hurst_max=0.95,
    )
    defaults.update(kwargs)
    return SymbolConfig(**defaults)


def _series(n: int, fill):
    return [fill] * n


def _make_indicators(
    n: int,
    *,
    closes,
    lower1=None,
    lower2=None,
    upper1=None,
    upper2=None,
    adx=None,
    hurst=None,
    rsi2=None,
    bxt_long=None,
    bxt_short=None,
) -> Indicators:
    lower1 = lower1 or _series(n, 95.0)
    lower2 = lower2 or _series(n, 90.0)
    upper1 = upper1 or _series(n, 105.0)
    upper2 = upper2 or _series(n, 110.0)
    adx = adx or _series(n, 20.0)
    hurst = hurst or _series(n, 0.5)
    rsi2 = rsi2 or _series(n, 50.0)
    bxt_long = bxt_long or _series(n, 0.0)
    bxt_short = bxt_short or [-x if x is not None else None for x in bxt_long]
    return Indicators(
        n=n,
        closes=closes,
        highs=[c + 1 for c in closes],
        lows=[c - 1 for c in closes],
        volumes=_series(n, 10.0),
        fvb=_series(n, 100.0),
        fvb_lower1=lower1,
        fvb_lower2=lower2,
        fvb_upper1=upper1,
        fvb_upper2=upper2,
        atr=_series(n, 1.0),
        adx=adx,
        rsi2=rsi2,
        rsi14=_series(n, 50.0),
        bxt_long=bxt_long,
        bxt_short=bxt_short,
        hurst=hurst,
        mfi=_series(n, 50.0),
        bb_upper=upper1,
        bb_middle=_series(n, 100.0),
        bb_lower=lower1,
    )


def test_crossover_above_detects_zero_cross():
    assert _crossover_above([-1.0, 0.5], 2) is True
    assert _crossover_above([-1.0, -0.5], 2) is False
    assert _crossover_above([1.0, 2.0], 2) is False


def test_crossover_below_detects_zero_cross():
    assert _crossover_below([1.0, -0.5], 2) is True
    assert _crossover_below([1.0, 0.5], 2) is False


def test_long_requires_outer_band_and_bullish_bxt():
    """Long: close < lower2 AND bxt_long crosses above 0."""
    n = 8
    # Price under outer lower; RSI oversold; bullish BXT cross on last bar
    closes = [100.0] * (n - 1) + [85.0]
    bxt_long = [1.0] * (n - 2) + [-0.5, 0.8]
    rsi2 = [50.0] * (n - 1) + [5.0]
    ind = _make_indicators(n, closes=closes, bxt_long=bxt_long, rsi2=rsi2)
    sig = check_entry(ind, n - 1, _sym())
    assert sig is not None
    assert sig.direction == Direction.LONG
    assert "lower2" in sig.reason
    assert "BX_long_cross_up" in sig.reason


def test_long_does_not_fire_on_bearish_bxt():
    """Old bug: long used bxt_short cross-up (= bearish). Must not enter."""
    n = 8
    closes = [100.0] * (n - 1) + [85.0]
    # Bearish: bxt_long crosses below 0
    bxt_long = [1.0] * (n - 2) + [0.5, -0.8]
    rsi2 = [50.0] * (n - 1) + [5.0]
    ind = _make_indicators(n, closes=closes, bxt_long=bxt_long, rsi2=rsi2)
    assert check_entry(ind, n - 1, _sym()) is None


def test_long_inner_band_alone_does_not_enter():
    """Between lower1 and lower2 is not enough — need outer band."""
    n = 8
    # 92 is below lower1 (95) but above lower2 (90)
    closes = [100.0] * (n - 1) + [92.0]
    bxt_long = [-0.5] * (n - 1) + [0.8]
    rsi2 = [50.0] * (n - 1) + [5.0]
    ind = _make_indicators(n, closes=closes, bxt_long=bxt_long, rsi2=rsi2)
    assert check_entry(ind, n - 1, _sym()) is None


def test_short_requires_outer_band_and_bearish_bxt():
    n = 8
    closes = [100.0] * (n - 1) + [115.0]
    bxt_long = [-1.0] * (n - 2) + [0.5, -0.8]
    rsi2 = [50.0] * (n - 1) + [95.0]
    ind = _make_indicators(n, closes=closes, bxt_long=bxt_long, rsi2=rsi2)
    sig = check_entry(ind, n - 1, _sym())
    assert sig is not None
    assert sig.direction == Direction.SHORT
    assert "upper2" in sig.reason
    assert "BX_long_cross_dn" in sig.reason


def test_short_does_not_fire_on_bullish_bxt():
    n = 8
    closes = [100.0] * (n - 1) + [115.0]
    bxt_long = [-0.5] * (n - 2) + [-0.2, 0.8]
    rsi2 = [50.0] * (n - 1) + [95.0]
    ind = _make_indicators(n, closes=closes, bxt_long=bxt_long, rsi2=rsi2)
    assert check_entry(ind, n - 1, _sym()) is None


def test_no_signal_when_bxt_cross_outside_lookback():
    n = 10
    closes = [100.0] * (n - 1) + [85.0]
    # Bullish cross happened early; recent bars stay positive (no new cross)
    bxt_long = [-1.0, 0.5] + [1.0] * (n - 2)
    rsi2 = [50.0] * (n - 1) + [5.0]
    ind = _make_indicators(n, closes=closes, bxt_long=bxt_long, rsi2=rsi2)
    # confirmation_bars=3 — cross at idx 1 is outside window ending at idx 9
    assert check_entry(ind, n - 1, _sym(confirmation_bars=3)) is None


def test_adx_filter_blocks_entry():
    n = 8
    closes = [100.0] * (n - 1) + [85.0]
    bxt_long = [-0.5] * (n - 1) + [0.8]
    rsi2 = [50.0] * (n - 1) + [5.0]
    adx = [20.0] * (n - 1) + [55.0]
    ind = _make_indicators(n, closes=closes, bxt_long=bxt_long, rsi2=rsi2, adx=adx)
    assert check_entry(ind, n - 1, _sym(adx_max=50)) is None
