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


def _ema(values: List[float], period: int) -> List[Optional[float]]:
    """Exponential moving average. Seed with SMA, then iterate."""
    out: List[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return out
    out[period - 1] = sum(values[:period]) / period
    alpha = 2.0 / (period + 1)
    for i in range(period, len(values)):
        out[i] = values[i] * alpha + (out[i - 1] or 0.0) * (1 - alpha)
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

    # Compute RMAs ONCE — O(n), not O(n²) inside the loop
    atr_s = _rma(tr_list, period)
    plus_dm_rma = _rma(plus_dm, period)
    minus_dm_rma = _rma(minus_dm, period)

    plus_di: List[Optional[float]] = [None] * len(bars)
    minus_di: List[Optional[float]] = [None] * len(bars)
    dx: List[Optional[float]] = [None] * len(bars)
    for i in range(period, len(bars)):
        if atr_s[i] and atr_s[i] > 0:
            sp = plus_dm_rma[i] or 0.0
            sm = minus_dm_rma[i] or 0.0
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


def _vwap(bars: List[Bar]) -> List[Optional[float]]:
    """VWAP with daily reset (UTC midnight). Ported from V7.5."""
    if not bars:
        return []
    vwap_series: List[Optional[float]] = []
    cum_vp = 0.0
    cum_v = 0.0
    last_day = None
    for b in bars:
        typ = (b.high + b.low + b.close) / 3.0
        vol = b.volume
        day = b.ts // 86400000
        if day != last_day:
            cum_vp = 0.0
            cum_v = 0.0
            last_day = day
        cum_vp += typ * vol
        cum_v += vol
        vwap_series.append(cum_vp / cum_v if cum_v > 0 else typ)
    return vwap_series


def vwap(bars: List[Bar]) -> List[Optional[float]]:
    """Daily-reset VWAP. Used as the FVB center line."""
    return _vwap(bars)


def fvb(bars: List[Bar], length: int = 20) -> List[Optional[float]]:
    """Fair Value Bubble center line = daily-reset VWAP.

    ``length`` is unused here — band width uses ``fvb_length`` via
    ``fvb_bands(..., period=)``. Kept for call-site compatibility.
    """
    _ = length  # band period is applied in fvb_bands, not the center line
    return vwap(bars)


def fvb_bands(
    fvb_vals: List[Optional[float]],
    closes: List[float],
    mult: float = 1.5,
    bars: Optional[List[Bar]] = None,
    period: int = 20,
    smoothing: str = "SMA",
) -> tuple:
    """VWAP ± σ multiplicative bands. Ported from V7.5 calc_vwap_with_bands.

    lower1 = smoothed_vwap * (1 - mult * rolling_std)
    upper1 = smoothed_vwap * (1 + mult * rolling_std)
    lower2/upper2 use 2*mult.

    rolling_std = std of (close - vwap) / vwap over `period` bars.
    """
    n = len(closes)
    lower1: List[Optional[float]] = [None] * n
    lower2: List[Optional[float]] = [None] * n
    upper1: List[Optional[float]] = [None] * n
    upper2: List[Optional[float]] = [None] * n

    if n < period or not fvb_vals:
        return lower1, lower2, upper1, upper2

    # Deviations from VWAP (as fraction of VWAP)
    devs: List[float] = [0.0] * n
    for i in range(n):
        v = fvb_vals[i]
        if v is not None and v > 0:
            devs[i] = (closes[i] - v) / v
        else:
            devs[i] = 0.0

    # Rolling standard deviation of deviations
    rolling_std: List[Optional[float]] = [None] * n
    for i in range(period - 1, n):
        window = devs[i - period + 1: i + 1]
        mean_d = sum(window) / period
        var_d = sum((d - mean_d) ** 2 for d in window) / period
        rolling_std[i] = math.sqrt(var_d)

    # Smooth the VWAP series
    vwap_clean = [v if v is not None else 0.0 for v in fvb_vals]
    if smoothing == "EMA":
        smoothed = _ema(vwap_clean, period)
    elif smoothing == "RMA":
        smoothed = _rma(vwap_clean, period)
    else:  # SMA (default, matches V7.5)
        smoothed = _sma(vwap_clean, period)

    for i in range(n):
        sv = smoothed[i] if smoothed[i] is not None else fvb_vals[i]
        if sv is not None and sv > 0 and rolling_std[i] is not None:
            rs = rolling_std[i]
            lower1[i] = sv * (1 - mult * rs)
            lower2[i] = sv * (1 - 2 * mult * rs)
            upper1[i] = sv * (1 + mult * rs)
            upper2[i] = sv * (1 + 2 * mult * rs)

    return lower1, lower2, upper1, upper2


def bxt(bars: List[Bar], l1: int = 5, l2: int = 30, l3: int = 5) -> tuple:
    """Bar Strength Index Trend: SMA diff of two MAs of typical price.

    ``bxt_long`` = SMA(tp, l1) - SMA(tp, l2). Positive = bullish (fast above slow).
    ``bxt_short`` = -bxt_long (mirror series; kept for compatibility).

    ``l3`` is unused — retained for call-site / config compatibility only.
    """
    _ = l3
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
    l1, l2, u1, u2 = fvb_bands(fvb_vals, closes, params.fvb_band_mult, period=params.fvb_length, smoothing="SMA")
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
