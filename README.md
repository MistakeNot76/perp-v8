# perp-v8

Production-ready perpetual futures trading system.

**What it is**: A clean rewrite of perp-v7.5 with a single config, real-time validation, no fabricated data, an inbuilt backtester, and per-perp FVB/BXT optimization.

## Strategy (entries)

| Side | Location | Timing |
|------|----------|--------|
| **Long** | Close below **outer** FVB lower band (`fvb_lower2`) | `bxt_long` crosses **above** 0 within `confirmation_bars` |
| **Short** | Close above **outer** FVB upper band (`fvb_upper2`) | `bxt_long` crosses **below** 0 within `confirmation_bars` |

Also filtered by ADX, Hurst, and RSI(2).

## Strategy (exits) — all selectable for backtest → live

Priority (first hit wins): hard SL → partial TP @ R → **FVB revert** (`vwap` or `inner`) → **faster same-TF BXT flip** → **lower-TF BXT flip** → fixed ATR/% TP → trail / max bars.

Both FVB targets and both BXT exit styles are available so you can A/B them in the backtester/optimizer, then apply winning `symbol_params` for live.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run all tests
python3 -m pytest tests/ -v

# Run a backtest (requires data in data/history/)
python3 -m backtest.runner --symbols SOLUSDT,BTCUSDT --tf 15m --days 90

# Optimize FVB/BXT per symbol (writes data/params/{SYMBOL}.json)
python3 -m optimize.runner --symbols SOLUSDT,BTCUSDT --tf 15m --days 90
# Also merge winners into config.yaml symbol_params:
python3 -m optimize.runner --symbols SOLUSDT --apply-config

# Start the dashboard
python3 run_dashboard.py
# Open http://localhost:9125  → Backtester / Optimize tabs

# Run live trading (paper mode)
# Edit config.yaml: system.mode = "paper"
python3 run_live.py
```

## Structure

```
perp-v8/
├── config.yaml           # SINGLE source of truth (+ optional symbol_params)
├── core/                 # Shared logic (live + backtest + dashboard)
│   ├── models.py
│   ├── indicators.py     # FVB, BX, ADX, Hurst, RSI, ATR
│   ├── entry_rules.py    # Outer-band + bullish/bearish BXT cross
│   ├── exit_rules.py     # SL/TP/BE/trail (giveback-from-peak)
│   ├── engine.py         # Bar-by-bar simulator
│   ├── validator.py      # Hard invariants (no phantom prices)
│   ├── data_loader.py
│   └── config_loader.py  # YAML + per-symbol JSON merge
├── backtest/             # CLI + programmatic backtester
├── optimize/             # Grid + walk-forward per-perp search
├── live/                 # Live/Demo/Paper execution
├── dashboard/            # React SPA (FastAPI + Vite)
├── data/params/          # Optimizer output per symbol
└── tests/
```

## Per-symbol params

Optimizer writes `data/params/SOLUSDT.json`. Live/backtest merge those over global `config.yaml` strategy keys (JSON wins over `symbol_params` in YAML).

Unused / compat-only knobs (do not change signals): `bxt_l3`, `bxt_ll1`, `bxt_ll2`, `adx_trend_max`.

## Design Principles

- **No fabricated data**: Validator enforces exit_price within bar's [low, high]
- **Single config**: config.yaml controls everything; per-perp overlays optional
- **Shared core**: Live, backtest, and optimizer use the same engine
- **Honest costs**: Fees (maker/taker), slippage, and flat funding estimates are applied on every trade. PnL is on **notional** size; leverage sets margin only (`margin = notional / leverage`).
- **Live execution**: Paper/demo/live place orders through the exchange adapter on OPEN/CLOSE/PARTIAL. Risk caps and kill switch are enforced and hot-reloaded from config.

## Dashboard

`python3 run_dashboard.py` → http://localhost:9125

| Tab | Purpose |
|-----|---------|
| Positions | Equity, uPnL, open positions |
| Trades | Closed trade log with filters |
| Backtester | Backtest + per-symbol optimize |
| Config | Common knobs + raw JSON |
| Logs | Tail `data/logs/` |
| Process | Live runner PID / mode status |
| Validator | Phantom-exit / PnL invariant failures |

Env for demo/live: `BITGET_API_KEY`, `BITGET_API_SECRET`, `BITGET_API_PASSPHRASE`.
- **Honest trades**: `entry_reason` + exit reason/price on every trade

## Migration from perp-v7.5

See [MIGRATION.md](MIGRATION.md).
