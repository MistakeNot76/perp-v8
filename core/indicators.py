"""Pure indicator math. No I/O, no external state. Fully unit-testable."""
from typing import List, Optional
import math

from core.models import Bar, Indicators


def _rma(values: List[float], period: int) -> List[Optional[float]]:
    if period <= 0:
        raise ValueError("period must be > 0")
    out: List[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    alpha = 1.0 / period
    for i in range(period, len(values)):
        out[i] = out[i - 1] * (1 - alpha) + values[i] * alpha
    return out


def _sma(values: List[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return out
    window_sum = sum(values[:period])
    out[period - 1] = window_sum / period
    for i in range(period, len(values)):
        window_sum += values[i] - values[i - period]
        out[i] = window_sum / period
    return out


def atr(bars: List[Bar], period: int = 14) -> List[Optional[float]]:
    if not bars:
        return []
    trs: List[float] = [0.0]
    for i in range(1, len(bars)):
        h, l, pc = bars[i].high, bars[i].low, bars[i - 1].close
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return _rma(trs, period)


def adx(bars: List[Bar], period: int = 14) -> List[Optional[float]]:
    if len(bars) < period * 2:
        return [None] * len(bars)
    plus_dm: List[float] = [0.0]
    minus_dm: List[float] = [0.0]
    tr_list: List[float] = [0.0]
    for i in range(1, len(bars)):
        up = bars[i].high - bars[i - 1].high
        dn = bars[i - 1].low - bars[i].low
        plus_dm.append(max(up, 0) if up > dn else 0.0)
        minus_dm.append(max(dn, 0) if dn > up else 0.0)
        h, l, pc = bars[i].high, bars[i].low, bars[i - 1].close
        tr_list.append(max(h - l, abs(h - pc), abs(l - pc)))

    atr_s = _rma(tr_list, period)
    plus_di: List[Optional[float]] = [None] * len(bars)
    minus_di: List[Optional[float]] = [None] * len(bars)
    dx: List[Optional[float]] = [None] * len(bars)
    for i in range(period, len(bars)):
        if atr_s[i] and atr_s[i] > 0:
            sp = _rma(plus_dm, period)[i] or 0.0
            sm = _rma(minus_dm, period)[i] or 0.0
            plus_di[i] = 100.0 * sp / atr_s[i]
            minus_di[i] = 100.0 * sm / atr_s[i]
            if plus_di[i] + minus_di[i] > 0:
                dx[i] = 100.0 * abs(plus_di[i] - minus_di[i]) / (plus_di[i] + minus_di[i])
    return _rma([v if v is not None else 0.0 for v in dx], period)


def rsi(closes: List[float], period: int = 14) -> List[Optional[float]]:
    if len(closes) < period + 1:
        return [None] * len(closes)
    gains: List[float] = [0.0]
    losses: List[float] = [0.0]
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_g = _rma(gains, period)
    avg_l = _rma(losses, period)
    out: List[Optional[float]] = [None] * len(closes)
    for i in range(period, len(closes)):
        if avg_l[i] and avg_l[i] > 0:
            rs = (avg_g[i] or 0) / avg_l[i]
            out[i] = 100.0 - (100.0 / (1.0 + rs))
        else:
            out[i] = 100.0
    return out


def fvb(bars: List[Bar], length: int = 8) -> List[Optional[float]]:
    """Fair Value Bubble: RMA of (high-low) range. FVB band = fvb * mult."""
    if not bars:
        return []
    ranges = [b.high - b.low for b in bars]
    return _rma(ranges, length)


def fvb_bands(fvb_vals: List[Optional[float]], closes: List[float], mult: float = 1.0):
    """Compute upper/lower bands around price using FVB."""
    n = len(closes)
    lower1: List[Optional[float]] = [None] * n
    lower2: List[Optional[float]] = [None] * n
    upper1: List[Optional[float]] = [None] * n
    upper2: List[Optional[float]] = [None] * n
    for i in range(n):
        if fvb_vals[i] is not None:
            offset = fvb_vals[i] * mult
            lower1[i] = closes[i] - offset
            lower2[i] = closes[i] - offset * 2
            upper1[i] = closes[i] + offset
            upper2[i] = closes[i] + offset * 2
    return lower1, lower2, upper1, upper2


def bxt(bars: List[Bar], l1: int = 5, l2: int = 30, l3: int = 5) -> tuple:
    """Bar Strength Index Trend: SMA diff of two MAs of typical price."""
    if not bars:
        return [], []
    tp = [(b.high + b.low + b.close) / 3 for b in bars]
    fast = _sma(tp, l1)
    slow = _sma(tp, l2)
    long_line: List[Optional[float]] = [None] * len(bars)
    short_line: List[Optional[float]] = [None] * len(bars)
    for i in range(len(bars)):
        if fast[i] is not None and slow[i] is not None:
            diff = fast[i] - slow[i]
            long_line[i] = diff
            short_line[i] = -diff
    return long_line, short_line


def hurst(bars: List[Bar], window: int = 100) -> List[Optional[float]]:
    """Simplified Hurst exponent estimator. Returns 0.5 = random walk baseline."""
    if len(bars) < window:
        return [None] * len(bars)
    closes = [b.close for b in bars]
    out: List[Optional[float]] = [None] * len(bars)
    for i in range(window - 1, len(bars)):
        segment = closes[i - window + 1: i + 1]
        mean_s = sum(segment) / window
        deviations = [p - mean_s for p in segment]
        cum_dev = []
        running = 0.0
        for d in deviations:
            running += d
            cum_dev.append(running)
        r = max(cum_dev) - min(cum_dev)
        s = math.sqrt(sum((p - mean_s) ** 2 for p in segment) / window)
        if s > 0:
            rs = r / s
            out[i] = max(0.0, min(1.0, math.log(rs) / math.log(window) * 0.5 + 0.5))
    return out


def bollinger(closes: List[float], period: int = 20, mult: float = 2.0):
    n = len(closes)
    middle = _sma(closes, period)
    upper: List[Optional[float]] = [None] * n
    lower: List[Optional[float]] = [None] * n
    for i in range(period - 1, n):
        seg = closes[i - period + 1: i + 1]
        mean = middle[i]
        var = sum((p - mean) ** 2 for p in seg) / period
        sd = math.sqrt(var)
        upper[i] = mean + mult * sd
        lower[i] = mean - mult * sd
    return upper, middle, lower


def compute_all(bars: List[Bar], params) -> Indicators:
    """Compute all indicators. Pure function."""
    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    volumes = [b.volume for b in bars]

    atr_vals = atr(bars, params.atr_period)
    adx_vals = adx(bars, params.adx_period)
    rsi14 = rsi(closes, 14)
    rsi2 = rsi(closes, 2)
    fvb_vals = fvb(bars, params.fvb_length)
    l1, l2, u1, u2 = fvb_bands(fvb_vals, closes, params.fvb_band_mult)
    bx_long, bx_short = bxt(bars, params.bxt_l1, params.bxt_l2, params.bxt_l3)
    hurst_vals = hurst(bars, params.hurst_window)
    bb_u, bb_m, bb_l = bollinger(closes)

    return Indicators(
        n=len(bars),
        closes=closes,
        highs=highs,
        lows=lows,
        volumes=volumes,
        fvb=fvb_vals,
        fvb_lower1=l1,
        fvb_lower2=l2,
        fvb_upper1=u1,
        fvb_upper2=u2,
        atr=atr_vals,
        adx=adx_vals,
        rsi2=rsi2,
        rsi14=rsi14,
        bxt_long=bx_long,
        bxt_short=bx_short,
        hurst=hurst_vals,
        mfi=[None] * len(bars),
        bb_upper=bb_u,
        bb_middle=bb_m,
        bb_lower=bb_l,
    )
