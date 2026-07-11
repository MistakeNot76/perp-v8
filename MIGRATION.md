# Migration Guide: perp-v7.5 → perp-v8

## Overview
perp-v8 is a clean rewrite. The trading core lives in `core/` and is shared by:
- **Live** trading (paper/demo/live)
- **Backtest** CLI
- **Dashboard** backend (and the backtester page in the dashboard)

There is **no code reuse** from perp-v7.5. Every line was rewritten with invariants enforced at runtime.

## What's Different

### 1. No more fabricated exit prices
- **Old bug** (perp-v7.5 commit `0eab8ff`): trailing stop used a step-based model that forced SL above entry even when price barely moved. 46 live phantom exits were found, inflating PnL by 22%.
- **New code** (perp-v8 `core/exit_rules.py`): giveback-from-peak trail. SL = `high_water * (1 - trail_pct%)`, floored at entry. The `validator` enforces exit price ∈ [bar.low, bar.high] on every position close.

### 2. Single config file
- **Old**: `config_v75.py`, `risk_config.json`, `execution_config.json`, `perp_params.json`, symbol-level params scattered across multiple files.
- **New**: `config.yaml`. One file. All values editable from the dashboard Config tab.

### 3. Shared core/
- `core/` has no I/O, no exchange calls, no file writes (except for the validator's failure log). Every live and backtest trade goes through the same `core/engine.py` and `core/exit_rules.py`. Parity is structurally guaranteed.

### 4. Real-time validation
- `core/validator.py` runs on every position close in live AND backtest.
- Failures log to `data/logs/validator_failures.log`.
- Dashboard Validator tab shows the count and tail.

## Cutover Steps

### Pre-cutover
1. Stop perp-v7.5 cron and dashboard.
2. Archive perp-v7.5: `mv /home/sdjmorris/perp-v7.5 /home/sdjmorris/_archive/perp-v7.5-2026-06-XX`.
3. Verify perp-v8 tests pass: `cd /home/sdjmorris/perp-v8 && python3 -m pytest tests/ -v`.

### Cutover
1. Start perp-v8 dashboard: `cd /home/sdjmorris/perp-v8 && python3 run_dashboard.py`
2. Start perp-v8 live (paper first): `cd /home/sdjmorris/perp-v8 && python3 run_live.py`
3. Verify positions appear in the dashboard Positions tab.
4. After 24h of paper trading, compare signal_log.jsonl against perp-v7.5's archived log. Should be similar patterns.
5. Switch to demo mode in `config.yaml`: `system.mode: demo`.
6. After 24h of demo, switch to `live` (requires real API keys in env vars).

### Rollback
If something goes wrong:
1. Restore perp-v7.5 from archive.
2. Stop perp-v8 processes.
3. Restart perp-v7.5 cron.

## Data
- perp-v8 reads from `data/history/` which is symlinked to `backtest-v75/data/history/` (1205 candle files, 3.8GB).
- New data from perp-v8 saves to the same location.
- Old signal_log.jsonl from perp-v7.5 is NOT imported. Fresh start.

## Configuration Migration
If you had custom values in perp-v7.5's config files, translate them to perp-v8's `config.yaml`:

| perp-v7.5 | perp-v8 config.yaml |
|---|---|
| `config_v75.py:LEVERAGE` | `execution.leverage` |
| `config_v75.py:NOTIONAL_PER_TRADE` | `execution.notional_per_trade` |
| `risk_config.json:max_daily_loss_pct` | `risk.max_daily_loss_pct` |
| `perp_params.json:SYMBOL.min_tp_pct` | `exits.min_tp_pct` (global default) |
| `params/fvb_length` | `strategy.fvb_length` |
| `params/bxt_l1/l2/l3` | `strategy.bxt_l1/l2/l3` |

Symbol-specific overrides: set `symbol_params.SYMBOL` in `config.yaml`, or run the optimizer which writes `data/params/{SYMBOL}.json` (JSON wins). Live and backtest both call `get_strategy_params(cfg, symbol)` / `get_symbol_config(cfg, symbol)`.

## Known Limitations
- RSI(2) oversold/overbought at 10/90 are strict. May produce very few signals on less volatile symbols.
- No multi-timeframe analysis. Single TF per symbol (set in strategy.tf).
- No partial TP yet (config flag exists; exit engine does not implement it).
- Entries use **outer** FVB bands (`lower2`/`upper2`) and require a **bullish** BXT zero-cross for longs / **bearish** for shorts.
- Per-symbol params: use `symbol_params` in config.yaml and/or `data/params/{SYMBOL}.json` from `python -m optimize.runner`.
- Unused compat knobs (loaded, ignored by indicators): `bxt_l3`, `bxt_ll1`, `bxt_ll2`, `adx_trend_max`.

## Support
- Tests: `cd /home/sdjmorris/perp-v8 && python3 -m pytest tests/ -v`
- Live runner: `python3 run_live.py`
- Backtest: `python3 -m backtest.runner --symbols SOLUSDT,BTCUSDT --tf 15m --days 90`
- Optimize: `python3 -m optimize.runner --symbols SOLUSDT,BTCUSDT --tf 15m --days 90 --apply-config`
- Dashboard: `python3 run_dashboard.py` then open http://localhost:9125
