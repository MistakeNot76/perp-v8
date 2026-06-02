"""Core: shared logic for live, backtest, and dashboard."""
from core.models import Bar, Trade, Position, SymbolConfig, Direction, ExitReason, Mode
from core.indicators import compute_all
from core.exit_rules import check_bar_exit, compute_tp_sl, update_sl_on_water
from core.validator import validate_exit_price, validate_trade_math, safe_exit_price
from core.data_loader import load_candles, resample, save_candles
from core.config_loader import load_config, get_symbols, get_mode, get_symbol_config, get_strategy_params, get_fee_config, get_dashboard_port, save_config
