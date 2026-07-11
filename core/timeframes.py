"""Timeframe parsing helpers shared by backtest, live, and tools."""


def tf_to_minutes(tf: str) -> int:
    """Parse timeframe string to minutes.

    Supports: ``5m``, ``15m``, ``30m``, ``1h``, ``4h``, ``1d``.
    Raises ``ValueError`` for unknown formats.
    """
    tf = (tf or "").strip().lower()
    if not tf:
        raise ValueError("Unknown timeframe: empty string")
    if tf.endswith("m"):
        return int(tf[:-1])
    if tf.endswith("h"):
        return int(tf[:-1]) * 60
    if tf.endswith("d"):
        return int(tf[:-1]) * 1440
    raise ValueError(f"Unknown timeframe: {tf}")


def bars_per_day(tf: str) -> int:
    """Number of bars in one UTC day for the given timeframe."""
    minutes = tf_to_minutes(tf)
    if 1440 % minutes != 0:
        raise ValueError(f"Timeframe {tf} does not divide evenly into a day")
    return 1440 // minutes
