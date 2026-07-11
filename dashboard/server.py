"""
FastAPI backend for perp-v8 dashboard.
Serves the React SPA and provides REST + WebSocket API.
"""
import sys
import os
import json
import yaml
import subprocess
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import asyncio

from core.config_loader import apply_overrides, load_config, save_config, merge_symbol_params_into_config
from backtest.runner import run_backtest, parse_symbols
from optimize.runner import optimize_symbols, persist_best, DEFAULT_GRID
from live.state import load_signal_log

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
LOGS_DIR = PROJECT_ROOT / "data" / "logs"
HISTORY_DIR = PROJECT_ROOT / "data" / "history"
DIST_DIR = Path(__file__).parent / "dist"
PARAMS_DIR = PROJECT_ROOT / "data" / "params"
POSITIONS_PATH = LOGS_DIR / "open_positions.json"

app = FastAPI(title="perp-v8")

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Helpers ──

def _read_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _write_config(cfg: dict):
    with open(CONFIG_PATH, "w") as f:
        yaml.safe_dump(cfg, f, default_flow_style=False, sort_keys=False)


def _tail_lines(path: Path, lines: int = 200) -> List[str]:
    if not path.exists():
        return []
    with open(path) as f:
        all_lines = f.readlines()
    return [ln.rstrip("\n") for ln in all_lines[-lines:]]


def _tail(path: Path, lines: int = 200) -> str:
    return "\n".join(_tail_lines(path, lines))


def _normalize_record(t: dict) -> dict:
    """Canonical trade/signal fields for the dashboard UI."""
    out = dict(t)
    action = str(out.get("action") or out.get("outcome") or "").upper()
    direction = str(out.get("direction") or out.get("side") or "").lower()
    if direction in ("buy", "long"):
        side = "long"
    elif direction in ("sell", "short"):
        side = "short"
    else:
        side = direction
    pnl = out.get("pnl_net", out.get("pnl", out.get("pnl_usd", 0)))
    try:
        pnl = float(pnl or 0)
    except (TypeError, ValueError):
        pnl = 0.0
    entry = out.get("entry_price", out.get("entry"))
    exit_p = out.get("exit_price", out.get("exit"))
    qty = out.get("qty", out.get("size"))
    out.update({
        "action": action,
        "outcome": action,
        "side": side,
        "direction": side,
        "pnl": pnl,
        "pnl_net": pnl,
        "entry_price": entry,
        "entry": entry,
        "exit_price": exit_p,
        "exit": exit_p,
        "qty": qty,
        "size": qty,
        "closed_at": out.get("closed_at") or out.get("ts"),
        "reason": out.get("reason"),
    })
    return out


def _live_runner_pids() -> List[str]:
    result = subprocess.run(
        ["pgrep", "-f", "run_live.py|live.runner|live/runner.py"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    return [p for p in result.stdout.strip().split("\n") if p]


def _derive_open_positions(trades: List[dict], persisted: Dict[str, Any]) -> List[dict]:
    """Build open positions from persisted file, falling back to unpaired OPENs in the log."""
    positions: List[dict] = []
    if persisted:
        for sym, raw in persisted.items():
            if not raw:
                continue
            side = str(raw.get("direction") or raw.get("side") or "").lower()
            qty = float(raw.get("size") or raw.get("qty") or 0)
            entry = float(raw.get("entry_price") or raw.get("entry") or 0)
            positions.append({
                "symbol": sym,
                "side": side,
                "qty": qty,
                "size": qty,
                "entry_price": entry,
                "entry": entry,
                "mark_price": entry,
                "mark": entry,
                "unrealized_pnl": 0.0,
                "upnl": 0.0,
                "leverage": raw.get("leverage"),
                "stop_loss": raw.get("current_sl") or raw.get("initial_sl"),
                "take_profit": raw.get("tp"),
                "opened_at": raw.get("entry_ts"),
            })
        return positions

    # Fallback: unpaired OPEN actions
    open_by_sym: Dict[str, dict] = {}
    for t in trades:
        n = _normalize_record(t)
        sym = n.get("symbol") or "?"
        action = n.get("action")
        if action == "OPEN":
            open_by_sym[sym] = n
        elif action in ("CLOSE",):
            open_by_sym.pop(sym, None)
        elif action == "PARTIAL":
            pass
    for sym, n in open_by_sym.items():
        qty = float(n.get("qty") or n.get("size") or 0)
        entry = float(n.get("entry_price") or n.get("entry") or 0)
        positions.append({
            "symbol": sym,
            "side": n.get("side") or "long",
            "qty": qty,
            "size": qty,
            "entry_price": entry,
            "entry": entry,
            "mark_price": entry,
            "mark": entry,
            "unrealized_pnl": 0.0,
            "upnl": 0.0,
            "leverage": n.get("leverage"),
            "stop_loss": n.get("sl"),
            "take_profit": n.get("tp"),
            "opened_at": n.get("ts"),
        })
    return positions


def _build_state() -> dict:
    raw_trades = load_signal_log(str(LOGS_DIR / "signal_log.jsonl"))
    trades = [_normalize_record(t) for t in raw_trades]
    closed = [t for t in trades if t.get("action") in ("CLOSE", "PARTIAL")]
    total_pnl = sum(float(t.get("pnl") or 0) for t in closed)

    persisted_raw: Dict[str, Any] = {}
    if POSITIONS_PATH.exists():
        try:
            data = json.loads(POSITIONS_PATH.read_text())
            persisted_raw = data.get("positions") or {}
        except Exception:
            persisted_raw = {}

    positions = _derive_open_positions(raw_trades, persisted_raw)
    upnl = sum(float(p.get("unrealized_pnl") or 0) for p in positions)

    cfg = _read_config()
    pids = _live_runner_pids()
    running = len(pids) > 0
    kill_switch = bool(cfg.get("risk", {}).get("kill_switch", False))
    starting = 10000.0  # paper default; live runner owns real equity
    equity = starting + total_pnl + upnl

    # Daily pnl: closes today
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    daily = 0.0
    for t in closed:
        ts = str(t.get("ts") or t.get("closed_at") or "")
        if ts.startswith(today):
            daily += float(t.get("pnl") or 0)

    symbols_summary: Dict[str, Any] = {}
    for t in trades:
        sym = t.get("symbol") or "?"
        symbols_summary.setdefault(sym, {"trades": 0, "equity": 0.0})
        if t.get("action") in ("CLOSE", "PARTIAL"):
            symbols_summary[sym]["trades"] += 1
            symbols_summary[sym]["equity"] += float(t.get("pnl") or 0)

    return {
        "mode": cfg.get("system", {}).get("mode", "paper"),
        "kill_switch": kill_switch,
        "killswitch": kill_switch,
        "running": running and not kill_switch,
        "pids": pids,
        "equity": equity,
        "available": starting + total_pnl,
        "margin": sum(float(p.get("entry_price") or 0) * float(p.get("qty") or 0) / max(float(p.get("leverage") or 1), 1) for p in positions),
        "upnl": upnl,
        "daily_pnl": daily,
        "total_pnl": total_pnl + upnl,
        "positions": positions,
        "open_positions": {p["symbol"]: p for p in positions},
        "symbols": symbols_summary,
        "total_trades": len(closed),
        "active_symbols": cfg.get("symbols") or [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _normalize_symbols(symbols: Union[str, List[str]]) -> List[str]:
    if isinstance(symbols, list):
        raw = ",".join(str(s) for s in symbols)
    else:
        raw = str(symbols)
    try:
        return parse_symbols(raw)
    except ValueError as e:
        raise HTTPException(400, str(e))


def _build_overrides(body: dict) -> dict:
    ov: Dict[str, Any] = {}
    if isinstance(body.get("overrides"), dict):
        ov.update(body["overrides"])
    for section in ("strategy", "exits", "fees", "execution"):
        if isinstance(body.get(section), dict):
            ov[section] = {**(ov.get(section) or {}), **body[section]}
    if body.get("leverage") is not None:
        ov.setdefault("execution", {})["leverage"] = body["leverage"]
    if body.get("notional") is not None:
        ov.setdefault("execution", {})["notional_per_trade"] = body["notional"]
    return ov


def _clean_json(obj):
    if isinstance(obj, float) and (obj == float("inf") or obj == float("-inf")):
        return None
    if isinstance(obj, dict):
        return {k: _clean_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_json(v) for v in obj]
    return obj


# ── API Endpoints ──

@app.get("/api/state")
def api_state():
    return _build_state()


@app.get("/api/trades")
def api_trades(symbol: Optional[str] = None, limit: int = 500):
    trades = [_normalize_record(t) for t in load_signal_log(str(LOGS_DIR / "signal_log.jsonl"))]
    # Prefer closed/partial for the trades table; include all normalized
    closed = [t for t in trades if t.get("action") in ("CLOSE", "PARTIAL")]
    if symbol:
        closed = [t for t in closed if t.get("symbol") == symbol.upper()]
    return closed[-limit:]


@app.get("/api/logs")
def api_logs(
    file: Optional[str] = None,
    lines: int = 200,
    list_files: bool = Query(False, alias="list"),
):
    """List log files when ?list=1 or no file; otherwise tail a file."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    if list_files or not file:
        files = []
        for p in sorted(LOGS_DIR.iterdir()):
            if p.is_file():
                st = p.stat()
                files.append({
                    "name": p.name,
                    "size": st.st_size,
                    "modified": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                })
        return files

    p = LOGS_DIR / Path(file).name  # prevent path traversal
    if not p.exists():
        raise HTTPException(404, f"File not found: {file}")
    all_lines = p.read_text().splitlines() if p.stat().st_size else []
    tail = all_lines[-lines:]
    return {
        "file": p.name,
        "lines": tail,
        "total_lines": len(all_lines),
        "content": "\n".join(tail),
    }


@app.get("/api/logs/files")
def api_log_files():
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    files = []
    for p in sorted(LOGS_DIR.iterdir()):
        if p.is_file():
            st = p.stat()
            files.append({
                "name": p.name,
                "size": st.st_size,
                "modified": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
            })
    return files


@app.get("/api/config")
def api_get_config():
    return _read_config()


class ConfigUpdate(BaseModel):
    data: Optional[dict] = None


@app.post("/api/config")
async def api_set_config(request: Request):
    """Accept either raw config JSON or { data: {...} }."""
    body = await request.json()
    if isinstance(body, dict) and "data" in body and isinstance(body["data"], dict):
        # Merge patch into existing
        cfg = _read_config()
        _deep_merge(cfg, body["data"])
        _write_config(cfg)
    elif isinstance(body, dict):
        # Full replace if it looks like a config (has system/risk/etc)
        if any(k in body for k in ("system", "risk", "strategy", "execution", "symbols")):
            _write_config(body)
        else:
            cfg = _read_config()
            _deep_merge(cfg, body)
            _write_config(cfg)
    else:
        raise HTTPException(400, "Expected JSON object")
    return {"status": "ok", "ok": True}


def _deep_merge(dst: dict, src: dict) -> dict:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


@app.post("/api/killswitch")
async def api_killswitch(request: Request):
    cfg = _read_config()
    desired: Optional[bool] = None
    try:
        body = await request.json()
    except Exception:
        body = None

    if isinstance(body, str):
        desired = body.lower() in ("on", "true", "1")
        if body.lower() in ("off", "false", "0"):
            desired = False
    elif isinstance(body, dict):
        if "on" in body:
            desired = bool(body["on"])
        elif "kill_switch" in body:
            desired = bool(body["kill_switch"])
        elif body.get("action") in ("on", "off"):
            desired = body["action"] == "on"

    if desired is None:
        desired = not bool(cfg.get("risk", {}).get("kill_switch", False))

    cfg.setdefault("risk", {})["kill_switch"] = desired
    _write_config(cfg)
    return {"ok": True, "kill_switch": desired, "killswitch": desired}


class BacktestRequest(BaseModel):
    symbols: Union[str, List[str]]
    tf: str = "15m"
    days: int = 90
    strategy: Optional[dict] = None
    exits: Optional[dict] = None
    fees: Optional[dict] = None
    execution: Optional[dict] = None
    leverage: Optional[float] = None
    notional: Optional[float] = None
    overrides: dict = Field(default_factory=dict)


@app.post("/api/backtest")
def api_backtest(body: BacktestRequest):
    symbols = _normalize_symbols(body.symbols)
    cfg = _read_config()
    if not Path(cfg["system"]["data_dir"]).is_absolute():
        cfg["system"]["data_dir"] = str(PROJECT_ROOT / cfg["system"]["data_dir"])

    ov = _build_overrides(body.model_dump())
    t0 = time.time()
    try:
        payload = run_backtest(symbols, cfg, tf=body.tf, days=body.days, overrides=ov)
    except Exception as e:
        raise HTTPException(500, f"Backtest failed: {e}")
    payload["duration_s"] = time.time() - t0
    return _clean_json(payload)


class OptimizeRequest(BaseModel):
    symbols: Union[str, List[str]]
    tf: Optional[str] = None
    days: Optional[int] = 90
    train_frac: float = 0.7
    min_trades: int = 5
    max_dd: float = 500.0
    top_n: int = 10
    grid: Optional[dict] = None
    apply_config: bool = False
    write_params: bool = True


@app.post("/api/optimize")
def api_optimize(body: OptimizeRequest):
    symbols = _normalize_symbols(body.symbols)
    cfg = _read_config()
    if not Path(cfg["system"]["data_dir"]).is_absolute():
        cfg["system"]["data_dir"] = str(PROJECT_ROOT / cfg["system"]["data_dir"])

    grid = body.grid or DEFAULT_GRID
    t0 = time.time()
    try:
        payload = optimize_symbols(
            symbols,
            cfg,
            tf=body.tf,
            days=body.days,
            grid=grid,
            train_frac=body.train_frac,
            min_trades=body.min_trades,
            max_dd=body.max_dd,
            top_n=body.top_n,
        )
    except Exception as e:
        raise HTTPException(500, f"Optimize failed: {e}")

    if body.write_params or body.apply_config:
        cfg_disk = _read_config()
        persist = persist_best(
            payload,
            cfg_disk,
            params_dir=str(PARAMS_DIR),
            apply_config=body.apply_config,
            config_path=str(CONFIG_PATH),
        )
        payload["persisted"] = persist

    payload["duration_s"] = time.time() - t0
    return _clean_json(payload)


@app.get("/api/params/{symbol}")
def api_get_params(symbol: str):
    p = PARAMS_DIR / f"{symbol.upper()}.json"
    if not p.exists():
        raise HTTPException(404, f"No params for {symbol}")
    with open(p) as f:
        return json.load(f)


@app.post("/api/params/{symbol}/apply")
def api_apply_params(symbol: str):
    """Merge data/params/{SYMBOL}.json into config.yaml symbol_params."""
    p = PARAMS_DIR / f"{symbol.upper()}.json"
    if not p.exists():
        raise HTTPException(404, f"No params for {symbol}")
    with open(p) as f:
        params = json.load(f)
    cfg = _read_config()
    cfg.setdefault("symbol_params", {})[symbol.upper()] = params.get("params") or params
    _write_config(cfg)
    return {"ok": True, "symbol": symbol.upper(), "params": cfg["symbol_params"][symbol.upper()]}


@app.get("/api/validator")
def api_validator(lines: int = 200):
    p = LOGS_DIR / "validator_failures.log"
    content_lines = _tail_lines(p, lines)
    failures = []
    for ln in content_lines:
        if not ln.strip() or ln.startswith("  "):
            continue
        # Format: {ts} [{symbol}] {msg}
        severity = "error"
        symbol = None
        rule = "validator"
        msg = ln
        try:
            if "] " in ln:
                left, msg = ln.split("] ", 1)
                if "[" in left:
                    symbol = left.split("[")[-1]
                if "PHANTOM" in msg:
                    rule = "phantom_exit"
                elif "PNL" in msg:
                    rule = "pnl_math"
            failures.append({
                "rule": rule,
                "severity": severity,
                "message": msg,
                "symbol": symbol,
                "detected_at": ln.split(" ")[0] if ln else datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            failures.append({
                "rule": "validator",
                "severity": "error",
                "message": ln,
                "detected_at": datetime.now(timezone.utc).isoformat(),
            })

    ok = len(failures) == 0
    checks = [
        {
            "name": "exit_price_in_bar_range",
            "ok": not any(f["rule"] == "phantom_exit" for f in failures),
            "detail": "Exit prices must lie within bar [low, high]",
        },
        {
            "name": "pnl_math",
            "ok": not any(f["rule"] == "pnl_math" for f in failures),
            "detail": "pnl_net == gross - fees - slippage - funding (notional PnL; leverage sets margin only)",
        },
        {
            "name": "failure_log",
            "ok": ok,
            "detail": f"{len(failures)} recent failure line(s)",
        },
    ]
    return {
        "ok": ok,
        "failures": failures,
        "checks": checks,
        "last_run": failures[-1]["detected_at"] if failures else None,
        "content": "\n".join(content_lines),
    }


@app.get("/api/cron")
@app.get("/api/process")
def api_process():
    """Live runner / process status (replaces legacy cron_engine probe)."""
    cfg = _read_config()
    pids = _live_runner_pids()
    running = len(pids) > 0
    kill = bool(cfg.get("risk", {}).get("kill_switch", False))
    mode = cfg.get("system", {}).get("mode", "paper")
    jobs = [
        {
            "id": "live_runner",
            "name": "Live runner",
            "schedule": "continuous (run_live.py)",
            "last_run": datetime.now(timezone.utc).isoformat() if running else None,
            "next_run": None,
            "last_status": "running" if running and not kill else ("error" if kill else "never"),
            "last_message": (
                f"mode={mode} pids={','.join(pids)}" if running
                else ("kill switch armed" if kill else "not running — start with python3 run_live.py")
            ),
        }
    ]
    return {
        "running": running,
        "pids": ",".join(pids),
        "enabled": not kill,
        "mode": mode,
        "kill_switch": kill,
        "jobs": jobs,
    }


@app.get("/api/data/health")
def api_data_health():
    """Candle history freshness for dashboard hints."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    files = []
    for p in sorted(HISTORY_DIR.glob("*.json")):
        st = p.stat()
        age_h = (time.time() - st.st_mtime) / 3600
        files.append({
            "name": p.name,
            "size": st.st_size,
            "age_hours": round(age_h, 1),
            "stale": age_h > 48,
        })
    return {
        "dir": str(HISTORY_DIR),
        "files": files,
        "missing": len(files) == 0,
        "hint": "python3 -m tools.fetch_candles --symbols SOLUSDT,BTCUSDT --tf 15m --days 90",
    }


# ── WebSocket ──

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            state = _build_state()
            await websocket.send_json({"type": "state", "data": state})
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        pass


# ── Static files ──

@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    p = DIST_DIR / full_path
    if p.exists() and p.is_file():
        return FileResponse(p)
    index = DIST_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    # Dev fallback: serve a tiny placeholder
    root_index = DIST_DIR.parent / "index.html"
    if root_index.exists():
        return FileResponse(root_index)
    return {"message": "Dashboard UI not built. Run: cd dashboard && npm run build"}


if __name__ == "__main__":
    import uvicorn
    cfg = _read_config()
    port = cfg.get("dashboard", {}).get("port", 9125)
    uvicorn.run(app, host="0.0.0.0", port=port)
