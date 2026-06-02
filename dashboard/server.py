"""
FastAPI backend for perp-v8 dashboard.
Serves the React SPA and provides REST + WebSocket API.
"""
import sys
import os
import json
import yaml
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import asyncio

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
LOGS_DIR = PROJECT_ROOT / "data" / "logs"
DIST_DIR = Path(__file__).parent / "dist"

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
    symbols: str
    tf: str = "5m"
    days: int = 90
    overrides: dict = {}

@app.post("/api/backtest")
def api_backtest(body: BacktestRequest):
    symbols = body.symbols
    if len(symbols.split(",")) > 10:
        raise HTTPException(400, "Max 10 symbols")

    cfg = _read_config()
    if body.overrides:
        cfg_strategy = dict(cfg["strategy"])
        cfg_exits = dict(cfg["exits"])
        cfg_fees = dict(cfg["fees"])
        cfg_exec = dict(cfg["execution"])
        for k, v in body.overrides.items():
            if k in cfg_strategy:
                cfg_strategy[k] = v
            elif k in cfg_exits:
                cfg_exits[k] = v
            elif k in cfg_fees:
                cfg_fees[k] = v
            elif k in cfg_exec:
                cfg_exec[k] = v
        bt_cfg = dict(cfg)
        bt_cfg["strategy"] = cfg_strategy
        bt_cfg["exits"] = cfg_exits
        bt_cfg["fees"] = cfg_fees
        bt_cfg["execution"] = cfg_exec
    else:
        bt_cfg = cfg

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(bt_cfg, f, default_flow_style=False, sort_keys=False)
        tmp_config = f.name

    try:
        result = subprocess.run(
            [sys.executable, "-m", "backtest.runner", "--symbols", symbols,
             "--tf", body.tf, "--days", str(body.days), "--config", tmp_config],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT), timeout=300,
        )
        output = result.stdout + "\n" + result.stderr
        if result.returncode != 0:
            return {"error": f"Backtest failed (exit {result.returncode})", "output": output}
        return {"output": output}
    except subprocess.TimeoutExpired:
        return {"error": "Backtest timed out (300s)"}
    finally:
        Path(tmp_config).unlink(missing_ok=True)

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
