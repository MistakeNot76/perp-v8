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

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import asyncio

from core.config_loader import apply_overrides, load_config, save_config, merge_symbol_params_into_config
from backtest.runner import run_backtest, parse_symbols
from optimize.runner import optimize_symbols, persist_best, DEFAULT_GRID

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
LOGS_DIR = PROJECT_ROOT / "data" / "logs"
DIST_DIR = Path(__file__).parent / "dist"
PARAMS_DIR = PROJECT_ROOT / "data" / "params"

app = FastAPI(title="perp-v8")

# ── CORS middleware ──
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Helpers ──

def _read_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)

def _write_config(cfg: dict):
    with open(CONFIG_PATH, "w") as f:
        yaml.safe_dump(cfg, f, default_flow_style=False, sort_keys=False)

def _tail(path: Path, lines: int = 200) -> str:
    if not path.exists():
        return ""
    with open(path) as f:
        all_lines = f.readlines()
    return "".join(all_lines[-lines:])

def _read_jsonl(path: str) -> list:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


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
    """Collect nested + flat override fields from a request body dict."""
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

# ── API Endpoints ──

@app.get("/api/state")
def api_state():
    trades = _read_jsonl(str(LOGS_DIR / "signal_log.jsonl"))
    symbols = {}
    for t in trades:
        sym = t.get("symbol", "?")
        if sym not in symbols:
            symbols[sym] = {"trades": 0, "equity": 0.0}
        symbols[sym]["trades"] += 1
        symbols[sym]["equity"] += float(t.get("pnl_net", 0) or t.get("pnl_usd", 0))

    # Count open positions (outcome == "OPEN")
    open_by_sym = {}
    for t in trades:
        if t.get("outcome") == "OPEN":
            sym = t.get("symbol", "?")
            open_by_sym[sym] = {"open_position": True, "direction": t.get("direction"), "entry_price": t.get("entry_price")}

    cfg = _read_config()
    return {
        "mode": cfg["system"].get("mode", "paper"),
        "kill_switch": cfg.get("risk", {}).get("kill_switch", False),
        "symbols": symbols,
        "open_positions": open_by_sym,
        "total_trades": len(trades),
    }

@app.get("/api/trades")
def api_trades(symbol: Optional[str] = None, limit: int = 500):
    trades = _read_jsonl(str(LOGS_DIR / "signal_log.jsonl"))
    if symbol:
        trades = [t for t in trades if t.get("symbol") == symbol.upper()]
    return trades[-limit:]

@app.get("/api/logs")
def api_logs(file: str = "system.log", lines: int = 200):
    p = LOGS_DIR / file
    if not p.exists():
        return {"error": f"File not found: {file}", "content": ""}
    return {"file": file, "content": _tail(p, lines)}

@app.get("/api/config")
def api_get_config():
    return _read_config()

class ConfigUpdate(BaseModel):
    data: dict

@app.post("/api/config")
def api_set_config(body: ConfigUpdate):
    cfg = _read_config()
    cfg.update(body.data)
    _write_config(cfg)
    return {"status": "ok"}

@app.post("/api/killswitch")
def api_killswitch():
    cfg = _read_config()
    cfg["risk"]["kill_switch"] = not cfg["risk"].get("kill_switch", False)
    _write_config(cfg)
    return {"kill_switch": cfg["risk"]["kill_switch"]}


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
    # Ensure data_dir is absolute relative to project
    if not Path(cfg["system"]["data_dir"]).is_absolute():
        cfg["system"]["data_dir"] = str(PROJECT_ROOT / cfg["system"]["data_dir"])

    ov = _build_overrides(body.model_dump())
    t0 = time.time()
    try:
        payload = run_backtest(symbols, cfg, tf=body.tf, days=body.days, overrides=ov)
    except Exception as e:
        raise HTTPException(500, f"Backtest failed: {e}")
    payload["duration_s"] = time.time() - t0
    # Sanitize inf for JSON
    def _clean(obj):
        if isinstance(obj, float) and (obj == float("inf") or obj == float("-inf")):
            return None
        if isinstance(obj, dict):
            return {k: _clean(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_clean(v) for v in obj]
        return obj
    return _clean(payload)


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
        # Reload fresh cfg for merge (avoid absolute data_dir pollution in yaml)
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

    def _clean(obj):
        if isinstance(obj, float) and (obj == float("inf") or obj == float("-inf")):
            return None
        if isinstance(obj, dict):
            return {k: _clean(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_clean(v) for v in obj]
        return obj
    return _clean(payload)


@app.get("/api/params/{symbol}")
def api_get_params(symbol: str):
    p = PARAMS_DIR / f"{symbol.upper()}.json"
    if not p.exists():
        raise HTTPException(404, f"No params for {symbol}")
    with open(p) as f:
        return json.load(f)


@app.get("/api/validator")
def api_validator(lines: int = 200):
    p = LOGS_DIR / "validator_failures.log"
    content = _tail(p, lines)
    count = content.count("\n") if content else 0
    return {"failures": count, "content": content}

@app.get("/api/cron")
def api_cron():
    result = subprocess.run(["pgrep", "-f", "cron_engine"], capture_output=True, text=True)
    running = result.returncode == 0
    pids = result.stdout.strip()
    return {"running": running, "pids": pids}

# ── WebSocket ──

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            state = api_state()
            await websocket.send_json(state)
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
    return FileResponse(DIST_DIR.parent / "index.html")

if __name__ == "__main__":
    import uvicorn
    cfg = _read_config()
    port = cfg.get("dashboard", {}).get("port", 9125)
    uvicorn.run(app, host="0.0.0.0", port=port)
