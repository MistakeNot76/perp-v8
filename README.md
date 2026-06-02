# perp-v8

Production-ready perpetual futures trading system.

**What it is**: A clean rewrite of perp-v7.5 with a single config, real-time validation, no fabricated data, and an inbuilt backtester page.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run all tests
python3 -m pytest tests/ -v

# Run a backtest (requires data in data/history/)
python3 -m backtest.runner --symbols SOLUSDT,BTCUSDT --tf 5m --days 90

# Start the dashboard
python3 run_dashboard.py
# Open http://localhost:9125

# Run live trading (paper mode)
# Edit config.yaml: system.mode = "paper"
python3 run_live.py
```

## Structure

```
perp-v8/
├── config.yaml           # SINGLE source of truth
├── core/                 # Shared logic (live + backtest + dashboard)
│   ├── models.py         # Dataclasses
│   ├── indicators.py     # FVB, BX, ADX, Hurst, RSI, ATR, Bollinger
│   ├── entry_rules.py    # Entry signal evaluation
│   ├── exit_rules.py     # SL/TP/BE/trail (giveback-from-peak)
│   ├── engine.py         # Bar-by-bar simulator
│   ├── validator.py      # Hard invariants (no phantom prices)
│   ├── data_loader.py    # Candle loading + resampling
│   └── config_loader.py  # YAML config parser
├── live/                 # Live/Demo/Paper execution
├── backtest/             # CLI backtester
├── dashboard/            # React SPA (FastAPI + Vite)
└── tests/                # Unit + integration tests
```

## Design Principles

- **No fabricated data**: Validator enforces exit_price within bar's [low, high]
- **Single config**: config.yaml controls everything
- **Shared core**: Live and backtest use the same engine
- **Modular**: core/ has no I/O, fully unit-testable

## Migration from perp-v7.5

See [MIGRATION.md](MIGRATION.md).
