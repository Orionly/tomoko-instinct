"""
core/mt5_bridge.py - Autonomous Compatibility Shim (MT5 Retired)

MetaTrader 5 (MT5) and the EA Socket Bridge have been fully decoupled and retired.
This shim provides 100% backwards-compatible aliases mapping to the autonomous PriceEngine
(Finnhub WebSocket + yfinance) and Decoupled NewsEngine.
"""

from typing import Dict, List, Optional
import pandas as pd

from core.price_engine import (
    PriceEngine,
    safe_float,
    sanitize_for_json,
    calculate_currency_strength,
)
from core.news_engine import _parse_event_time

parse_event_time = _parse_event_time
ea_calendar = []
ea_quotes = {}
ea_connected = False
ACCOUNT_CENT_MODE = False


class MT5Bridge(PriceEngine):
    """Backwards-compatibility alias for PriceEngine."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.connected = True
        self.symbol_map = {}

    def connect(self) -> bool:
        self.connected = True
        return True

    def get_balance(self) -> float:
        return 0.0

    def get_calendar_from_ea(self) -> Dict:
        return {"source": "decoupled_macro_calendar", "calendar": [], "last_update": ""}


def _socket_server(port: int):
    """Inert stub: socket server retired."""
    pass