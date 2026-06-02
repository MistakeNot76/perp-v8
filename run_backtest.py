#!/usr/bin/env python3
"""Entry point: run backtest."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from backtest.runner import main

if __name__ == "__main__":
    main()
