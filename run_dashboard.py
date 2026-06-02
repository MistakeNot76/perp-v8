#!/usr/bin/env python3
"""Entry point: run dashboard."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from dashboard.server import app
import uvicorn

from core.config_loader import load_config, get_dashboard_port

if __name__ == "__main__":
    cfg = load_config()
    port = get_dashboard_port(cfg)
    uvicorn.run(app, host="0.0.0.0", port=port)
