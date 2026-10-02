# MT5 Bridge - Real data for Tomoko Brain + EA DUMB PIPE
# Local only, no VPS - Option A file bridge
# Fixes: AttributeError get_calendar_from_ea

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
import os
import json
import math
import time
import socket
import sys
import threading
import argparse

# Global dictionary for calendar events keyed by event_id
ea_calendar_dict = {}

MT5_PATHS = {
    "VT": r"C:\Program Files\VT Markets (Pty) MT5 Terminal\terminal64.exe",
    "HFM": r"C:\Program Files\HFM Metatrader 5\terminal64.exe",
}
ACTIVE_BROKER = "VT"
MT5_TERMINAL_PATH = MT5_PATHS[ACTIVE_BROKER]

ACCOUNT_CENT_MODE = False  # VT Standard = 100k contract size, not Cent

LIGHT_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "EURJPY", "EURGBP", "XAUUSD"]

ea_quotes = {}
ea_calendar = []
ea_connected = False
_bridge_port = 18001

def _socket_server(port):
    global _bridge_port
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(('127.0.0.1', port))
    except PermissionError as e:
        print(f"[SOCKET] Port {port} blocked (WinError 10013 - Hyper-V/Firewall reservation)")
        print(f"[SOCKET] Run: netsh int ipv4 show excludedportrange protocol=tcp")
        print(f"[SOCKET] Or try: netsh int ipv4 add excludedportrange protocol=tcp startport={port} numberofports=1")
        print(f"[SOCKET] Or run this script as Administrator")
        raise
    except OSError as e:
        print(f"[SOCKET] Port {port} unavailable: {e}")
        raise
    srv.listen(5)
    _bridge_port = port
    print(f"[SOCKET] Listening on 127.0.0.1:{port}")
    if port != 18001:
        print(f"!!! CRITICAL: Bound to {port} but EA targets 18001 - UPDATE EA INPUT TO {port}!!!")
    while True:
        try:
            conn, addr = srv.accept()
            t = threading.Thread(target=_socket_handle_client, args=(conn, addr), daemon=True)
            t.start()
        except Exception as e:
            print(e)
            continue

def _parse_event_time(time_str):
    """Parse event time string, trying multiple formats. Returns timezone-aware UTC datetime or None."""
    if not time_str:
        return None
    formats = ["%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y-%m-%dT%H:%M:%S"]
    for fmt in formats:
        try:
            return datetime.strptime(time_str, fmt).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def _normalize_importance(importance):
    """Map importance string to numeric: HIGH->2, MEDIUM->1, LOW->0."""
    if importance is None:
        return 0
    if isinstance(importance, (int, float)):
        try:
            return int(importance)
        except (ValueError, TypeError):
            return 0
    if isinstance(importance, str):
        upper = importance.upper().strip()
        if upper == "HIGH":
            return 2
        elif upper == "MEDIUM":
            return 1
        elif upper == "LOW":
            return 0
    return 0


def _normalize_event(ev):
    """Normalize a calendar event: ensure time, event, currency, importance fields."""
    if not isinstance(ev, dict):
        return None
    time_val = ev.get("time") or ev.get("release_time")
    event_name = ev.get("event") or ev.get("name")
    importance = ev.get("importance") or ev.get("priority")
    normalized = dict(ev)
    normalized["time"] = time_val
    normalized["event"] = event_name
    normalized["importance"] = importance
    return normalized


CURRENCY_MAP = {
    "EURUSD": ["EUR", "USD"], "GBPUSD": ["GBP", "USD"],
    "USDJPY": ["USD", "JPY"], "GBPJPY": ["GBP", "JPY"],
    "EURJPY": ["EUR", "JPY"], "EURGBP": ["EUR", "GBP"],
    "XAUUSD": ["XAU", "USD"],
}


def safe_float(v, default=0.0):
    if v is None:
        return default
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def sanitize_for_json(obj):
    import math
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return 0.0
        return obj
    elif isinstance(obj, dict):
        clean = {}
        for k, v in obj.items():
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                v = 0.0
            clean[k] = sanitize_for_json(v) if isinstance(v, (dict, list)) else v
        return clean
    elif isinstance(obj, list):
        return [sanitize_for_json(x) for x in obj]
    return obj


def _socket_handle_client(conn, addr):
    global ea_quotes, ea_calendar, ea_connected, ea_calendar_dict
    print(f"[SOCKET {_bridge_port}] Client connected from {addr}")
    try:
        buffer = ""
        while True:
            data = conn.recv(4096)
            if not data:
                break
            buffer += data.decode('utf-8')
            while '\n' in buffer:
                line, buffer = buffer.split('\n', 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    msg_type = msg.get("type", "")
                    if msg_type == "hello":
                        ver = msg.get("version", "unknown")
                        print(f"[SOCKET {_bridge_port}] HELLO v{ver}")
                        ea_connected = True
                        conn.sendall(json.dumps({"type": "ack", "message": "connected"}).encode() + b'\n')
                    elif msg_type == "quote":
                        sym = msg.get("symbol", "")
                        bid = msg.get("bid", 0)
                        ask = msg.get("ask", 0)
                        ea_quotes[sym] = {"bid": bid, "ask": ask, "time": msg.get("time", 0)}
                    elif msg_type in ("calendar_event", "calendar_update"):
                        # Bulk replace on calendar_update with {"events": [...]}, single event otherwise
                        events = msg.get("events")
                        if msg_type == "calendar_update" and isinstance(events, list):
                            event_msgs = events
                        else:
                            event_msgs = [msg]
                        for ev in event_msgs:
                            eid = ev.get("event_id") or ev.get("id") or ev.get("time")
                            if eid is None:
                                continue
                            normalized = {
                                "time": ev.get("time") or ev.get("release_time"),
                                "event": ev.get("event") or ev.get("name"),
                                "currency": ev.get("currency"),
                                "importance": ev.get("importance")
                            }
                            ea_calendar_dict[eid] = normalized
                        # Cap dict size to 500 to prevent indefinite growth
                        if len(ea_calendar_dict) > 500:
                            overflow = list(ea_calendar_dict.keys())[:len(ea_calendar_dict) - 500]
                            for k in overflow:
                                del ea_calendar_dict[k]
                        ea_calendar = list(ea_calendar_dict.values())
                        # Also write ea_calendar to Common/Files/tomoko_calendar.json immediately
                        try:
                            base = os.getenv('APPDATA','')
                            write_path = os.path.join(base, 'MetaQuotes','Terminal','Common','Files','tomoko_calendar.json')
                            with open(write_path, 'w', encoding='utf-8') as f:
                                json.dump({"calendar": ea_calendar}, f, ensure_ascii=False)
                        except Exception as e:
                            print(f"[SOCKET {_bridge_port}] Failed to write calendar: {e}")
                    elif msg_type == "calendar_status":
                        print(f"[SOCKET {_bridge_port}] CAL STATUS: {msg.get('status', '')}")
                    elif msg_type == "heartbeat":
                        pass
                except json.JSONDecodeError:
                    pass
    except Exception as e:
        print(f"[SOCKET {_bridge_port}] Client error: {e}")
    finally:
        conn.close()
        ea_connected = False
        print(f"[SOCKET {_bridge_port}] Client disconnected")





class MT5Bridge:
    def __init__(self):
        self.connected = False
        self.symbol_map = {}
        self._ea_calendar_err_logged = False
        base = os.getenv('APPDATA','')
        paths = [
            os.path.join(base, 'MetaQuotes','Terminal','Common','Files','tomoko_calendar.json'),
            os.path.join(base, 'MetaQuotes','Terminal','Common','Files','calendar.json'),
            os.path.join(base, 'MetaQuotes','Terminal','Common','Files','tomoko_calendar_raw.json'),
        ]
        self.ea_calendar_paths = paths
        self.ea_prices_path = os.path.join(base, 'MetaQuotes','Terminal','Common','Files','tomoko_prices_raw.json')
        # Also check current terminal Data Folder\Common\Files fallback
        # Common Files is same for all terminals

    def connect(self):
        if not mt5.initialize(path=MT5_TERMINAL_PATH, timeout=10000):
            print(f"[MT5] Failed to init {ACTIVE_BROKER} at {MT5_TERMINAL_PATH}, error: {mt5.last_error()}")
            if not mt5.initialize():
                print("[MT5] Fallback initialization also failed")
                return False
            else:
                print(f"[MT5] Warning: Using fallback MT5 terminal (not {ACTIVE_BROKER})")
        self.connected = True
        acc = mt5.account_info()
        if acc:
            print(f"[MT5] Connected to {acc.server} Balance {acc.balance} {acc.currency} via {MT5_TERMINAL_PATH}")
        terminal_info = mt5.terminal_info()
        if terminal_info:
            print(f"[MT5] Terminal path: {terminal_info.path} Name: {terminal_info.name}")
        # Discover symbols for your 7 pairs
        try:
            all_syms = mt5.symbols_get()
            if all_syms:
                names = [s.name for s in all_syms]
                for desired in ["EURUSD","GBPUSD","USDJPY","GBPJPY","EURJPY","EURGBP","XAUUSD"]:
                    found = desired
                    if desired not in names:
                        for n in names:
                            if n.startswith(desired) or desired in n:
                                found = n
                                break
                    self.symbol_map[desired] = found
                    if found != desired:
                        print(f"  Symbol mapping: {desired} -> {found}")
                    mt5.symbol_select(found, True)
            print(f"Symbol map: {self.symbol_map}")
        except Exception as e:
            print(f"discover failed: {e}")
        return True

    def get_balance(self):
        info = mt5.account_info()
        return info.balance if info else 0

    def get_price(self, symbol):
        global ea_quotes
        # Priority 1: EA quotes from socket
        if symbol in ea_quotes:
            q = ea_quotes[symbol]
            return {"bid": q.get("bid", 0), "ask": q.get("ask", 0), "time": q.get("time", 0), "symbol": symbol, "source": "ea_socket"}
        # Priority 2: MT5 real tick
        real = self.symbol_map.get(symbol, symbol)
        try:
            tick = mt5.symbol_info_tick(real)
            if tick:
                return {"bid": tick.bid, "ask": tick.ask, "time": tick.time, "symbol": symbol, "real_symbol": real}
        except:
            pass
        # Priority 3: EA file fallback
        ea_prices = self.get_prices_from_ea()
        if symbol in ea_prices:
            return {"bid": ea_prices[symbol].get("bid"), "ask": ea_prices[symbol].get("ask"), "time": 0, "symbol": symbol, "real_symbol": symbol+" (EA)", "source": "ea"}
        return None

    def get_rates(self, symbol, timeframe, count=200):
        real = self.symbol_map.get(symbol, symbol)
        try:
            rates = mt5.copy_rates_from_pos(real, timeframe, 0, count)
        except Exception:
            rates = None
        if rates is None or len(rates) == 0:
            return pd.DataFrame()
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        return self._add_indicators(df)

    @staticmethod
    def _add_indicators(df):
        if df.empty:
            return df
        close = df['close']

        # EMAs
        df['EMA20'] = close.ewm(span=20, adjust=False).mean()
        df['EMA50'] = close.ewm(span=50, adjust=False).mean()
        df['EMA21'] = close.ewm(span=21, adjust=False).mean()

        # ATR (14)
        high, low = df['high'], df['low']
        prev_close = close.shift(1)
        tr = pd.concat([
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        df['ATR'] = tr.ewm(alpha=1 / 14, adjust=False).mean()

        # RSI (14)
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df['RSI'] = (100 - (100 / (1 + rs))).fillna(100)

        # ADX (14)
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        atr14 = tr.ewm(alpha=1 / 14, adjust=False).mean().replace(0, 0.0001)
        plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / 14, adjust=False).mean() / atr14
        minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / 14, adjust=False).mean() / atr14
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        df['ADX'] = dx.ewm(alpha=1 / 14, adjust=False).mean()

        # Distance from EMA21 in ATR units (restored for regime/watchout/strategy consumers)
        df['dist_ema21_atr'] = ((close - df['EMA21']).abs() / df['ATR'].replace(0, np.nan)).round(2)
        df['dist_ema21_atr'] = df['dist_ema21_atr'].fillna(0.0)

        return df

    def get_all_pair_info(self, pairs):
        data = {}
        for p in pairs:
            real = self.symbol_map.get(p, p)
            info = mt5.symbol_info(real)
            tick = mt5.symbol_info_tick(real)
            if info and tick:
                data[p] = {
                    "spread": info.spread,
                    "bid": tick.bid,
                    "ask": tick.ask,
                    "point": info.point,
                }
        return data

    # --- EA DUMB PIPE - Option A ---
    def get_calendar_from_ea(self):
        """Read raw calendar dumped by EA - exactly as MT5 terminal shows"""
        try:
            # Try paths in order: first existing file wins
            data = None
            for path in self.ea_calendar_paths:
                if os.path.exists(path):
                    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                        data = json.load(f)
                        break
            if data is None:
                return {"source": "mt5_calendar_missing", "calendar": [], "last_update": "", "path": self.ea_calendar_paths[0]}

            # Handle both formats: {"calendar": [...]} or raw list
            if isinstance(data, list):
                data = {"calendar": data}
            if not isinstance(data, dict):
                return {"source": "mt5_calendar_missing", "calendar": [], "last_update": "", "path": self.ea_calendar_paths[0]}

            # Normalize each event
            normalized_events = []
            for ev in data.get("calendar", []):
                if not isinstance(ev, dict):
                    continue
                time_val = ev.get("time") or ev.get("release_time")
                event_name = ev.get("event") or ev.get("name")
                importance = ev.get("importance") or ev.get("priority")
                normalized = dict(ev)
                normalized["time"] = time_val
                normalized["event"] = event_name
                normalized["importance"] = importance
                normalized_events.append(normalized)

            if "source" not in data:
                data["source"] = "ea_file"
            data["calendar"] = normalized_events
            return data
        except Exception as e:
            if not self._ea_calendar_err_logged:
                self._ea_calendar_err_logged = True
                print(f"EA calendar read failed: {e} path={self.ea_calendar_paths[0]}")
            return {"source": "mt5_calendar_missing", "calendar": [], "error": str(e)}

    def get_prices_from_ea(self):
        """Fallback prices from EA"""
        try:
            if not os.path.exists(self.ea_prices_path):
                return {}
            with open(self.ea_prices_path,'r',encoding='utf-8',errors='ignore') as f:
                data = json.load(f)
            raw = data.get("prices",{})
            # Normalize keys: strip broker suffixes, cent marker, uppercase
            reverse_map = {v: k for k, v in self.symbol_map.items()}
            normalized_prices = {}
            for k, v in raw.items():
                clean = str(k).replace('.crp','').replace('-STD','').replace('-std','')
                if clean.endswith('c') and not clean.endswith(('.mc','.kc','.bc')):
                    clean = clean[:-1]  # cent list: EURUSDc -> EURUSD
                clean = clean.upper()
                # map via symbol_map if needed (broker-specific name -> desired name)
                if clean in reverse_map:
                    clean = reverse_map[clean]
                normalized_prices[clean] = v
            return normalized_prices
        except Exception as e:
            print(f"EA prices read failed: {e}")
            return {}

    def get_currencies(self, symbol):
        """Return the currencies that affect a symbol (for news filtering)."""
        return CURRENCY_MAP.get(symbol, ["USD"])

    def get_upcoming_events(self, symbol, hours_ahead=168):
        """Return upcoming events for symbol's currencies within the display window.

        Window runs from now UTC to now + hours_ahead (default 168h = 7 days).
        Returns up to 20 events sorted soonest-first, each annotated with _rel_min.
        """
        cal = self.get_calendar_from_ea()
        source = cal.get("source", "mt5_calendar_missing")
        events = cal.get("calendar", [])
        now = datetime.now(timezone.utc)

        if source == "mt5_calendar_missing" or not events:
            return []

        # FIX: use hours_ahead param, not hardcoded 60m
        window_start = now
        window_end = now + timedelta(hours=hours_ahead)

        # For "ALL" symbol, don't filter by currency
        wanted = self.get_currencies(symbol) if symbol != "ALL" else None
        filtered = []
        for ev in events:
            # Check currency: either the singular 'currency' field or the plural 'currencies' field
            ev_currency = ev.get("currency")
            ev_currencies = ev.get("currencies")
            match = False
            if wanted is None:
                match = True
            elif ev_currency and ev_currency in wanted:
                match = True
            elif ev_currencies:
                if any(c in wanted for c in ev_currencies):
                    match = True
            if not match:
                continue
            try:
                ev_time = _parse_event_time(ev.get("time", ""))
                if ev_time is None:
                    continue
            except Exception:
                continue
            if ev_time < window_start or ev_time > window_end:
                continue
            rel_min = int((ev_time - now).total_seconds() / 60)
            filtered.append({**ev, "_rel_min": rel_min})

        filtered.sort(key=lambda e: e.get("time", ""))
        total = len(filtered)
        nearest = filtered[0] if filtered else None
        nearest_str = ""
        if nearest:
            d = nearest["_rel_min"]
            days, rem = divmod(abs(d), 1440)
            hrs, mins = divmod(rem, 60)
            if days:
                nearest_str = f"{nearest['event']} in {days}d {hrs}h"
            elif hrs:
                nearest_str = f"{nearest['event']} in {hrs}h {mins}m"
            else:
                nearest_str = f"{nearest['event']} in {mins}m"
        print(f"[NEWS_ENGINE] Loaded {len(events)} events, Window {window_start.strftime('%a %H:%M')} -> {window_end.strftime('%a %H:%M')}+{hours_ahead//24}d ({hours_ahead}h), {symbol} -> {total} events")
        return filtered[:20]  # return more for global calendar

    def is_news_clean(self, symbol, minutes_before=60, minutes_after=30):
        """Check HIGH news (priority>=2) for symbol's currencies within proximity window.

        Trading gate: blocks only when a HIGH event is imminent (<=minutes_before away).
        """
        cal = self.get_calendar_from_ea()
        if cal.get("source") == "mt5_calendar_missing":
            return True, []
        wanted = self.get_currencies(symbol)
        now = datetime.now(timezone.utc)
        blocking = []
        for ev in cal.get("calendar", []):
            if _normalize_importance(ev.get("importance")) < 2:  # only HIGH = 2 (red)
                continue
            if ev.get("currency") not in wanted:
                continue
            try:
                ev_time = _parse_event_time(ev.get("time", ""))
                if ev_time is None:
                    continue
                diff = (ev_time - now).total_seconds() / 60
                if -minutes_after <= diff <= minutes_before:
                    blocking.append(ev)
            except Exception:
                continue
        return len(blocking) == 0, blocking

    def get_global_calendar(self, hours_ahead: int = 168):
        """ALL currencies, not filtered by symbol. From now UTC to now+7days. Up to 15 events.

        Returns a sanitized list (each event annotated with _rel_min in minutes) so the
        frontend GLOBAL CALENDAR block shows the whole market even when the symbol-specific
        right-side filter is empty (e.g. Sunday with no USD/XAU/GOLD events upcoming).
        """
        cal = self.get_calendar_from_ea()
        source = cal.get("source", "mt5_calendar_missing")
        events = cal.get("calendar", [])
        now = datetime.now(timezone.utc)

        if source == "mt5_calendar_missing" or not events:
            return []

        start = now
        end = start + timedelta(hours=hours_ahead)

        filtered = []
        for ev in events:
            try:
                ev_time = _parse_event_time(ev.get("time", ""))
                if ev_time is None:
                    continue
            except Exception:
                continue
            if ev_time < start or ev_time > end:
                continue
            rel_min = (ev_time - now).total_seconds() / 60
            e = dict(ev)
            e["_rel_min"] = safe_float(rel_min)
            filtered.append(e)

        filtered = sorted(filtered, key=lambda x: x.get("time", ""))[:15]
        return sanitize_for_json(filtered)

    @staticmethod
    def _safe_num(v):
        try:
            f = float(v)
            return 0.0 if math.isnan(f) or math.isinf(f) else f
        except (TypeError, ValueError):
            return 0.0

    def get_market_heatmap(self, scan_results: dict):
        """Build H4/H1/M15 alignment heatmap from scan results.

        Returns one row per pair with trend direction per timeframe, an alignment
        classification (3/3 BULL/BEAR, 2/3, MIXED), the brain score and action.
        Sorted strongest-alignment first, then by score desc. Sanitized for JSON.
        """
        heatmap = []
        for symbol, data in scan_results.items():
            score = self._safe_num(data.get("score", 0))
            action = data.get("action", "WAIT")

            # H4 trend: data['h4']['direction'] -> UP / DOWN / FLAT
            h4_dir = ""
            h4_block = data.get("h4")
            if isinstance(h4_block, dict):
                h4_dir = str(h4_block.get("direction", "")).upper()
            h4_str = "BULL" if h4_dir == "UP" else "BEAR" if h4_dir == "DOWN" else "FLAT"

            # H1 trend: data['h1_bias'] -> Bullish / Bearish / Neutral
            h1_raw = str(data.get("h1_bias", "")).upper()
            if "BULL" in h1_raw:
                h1_str = "BULL"
            elif "BEAR" in h1_raw:
                h1_str = "BEAR"
            else:
                h1_str = "FLAT"

            # M15 trend: data['m15_bias'] -> Bullish / Bearish / Neutral
            m15_raw = str(data.get("m15_bias", data.get("M15_bias", ""))).upper()
            if "BULL" in m15_raw:
                m15_str = "BULL"
            elif "BEAR" in m15_raw:
                m15_str = "BEAR"
            else:
                m15_str = "FLAT"

            trends = [h4_str, h1_str, m15_str]
            bull = sum(1 for t in trends if "BULL" in t)
            bear = sum(1 for t in trends if "BEAR" in t)

            if bull == 3:
                align, color = "3/3 BULL", "green"
            elif bear == 3:
                align, color = "3/3 BEAR", "red"
            elif bull == 2:
                align, color = "2/3 BULL", "yellow-green"
            elif bear == 2:
                align, color = "2/3 BEAR", "orange"
            else:
                align, color = "MIXED", "gray"

            # distance from EMA21 in ATR units
            dist = self._safe_num(
                data.get("h4", {}).get("dist_ema21_atr", 0)
                if isinstance(data.get("h4"), dict)
                else data.get("dist_ema21_atr", 0)
            )

            heatmap.append({
                "pair": symbol,
                "score": float(score),
                "h4": h4_str,
                "h1": h1_str,
                "m15": m15_str,
                "alignment": align,
                "dist": float(dist),
                "action": action,
                "color": color,
                "bull_count": bull,
                "bear_count": bear,
            })

        heatmap.sort(
            key=lambda x: (
                3 if x["alignment"] in ("3/3 BULL", "3/3 BEAR") else
                2 if x["alignment"] in ("2/3 BULL", "2/3 BEAR") else 0,
                x["score"],
            ),
            reverse=True,
        )
        return sanitize_for_json(heatmap)

    


def calculate_currency_strength(scan_results: dict) -> dict:
    import math
    currency_pairs = {
        "USD": [("EURUSD", False), ("GBPUSD", False), ("USDJPY", True), ("XAUUSD", False)],
        "EUR": [("EURUSD", True), ("EURJPY", True), ("EURGBP", True)],
        "GBP": [("GBPUSD", True), ("GBPJPY", True), ("EURGBP", False)],
        "JPY": [("USDJPY", False), ("GBPJPY", False), ("EURJPY", False)],
        "XAU": [("XAUUSD", True)],
    }
    result = {}
    print(f"[MARKET_PULSE] scan_results keys: {list(scan_results.keys())}")
    for currency, pairs in currency_pairs.items():
        score = 0.0
        bull_count = 0
        total = len(pairs)
        has_data = False
        for pair_name, direct in pairs:
            scan = scan_results.get(pair_name)
            if not scan:
                print(f" {currency} {pair_name} MISSING scan")
                continue
            h4 = scan.get("h4", {})
            if not h4:
                print(f" {currency} {pair_name} MISSING h4")
                continue
            direction = h4.get("direction", "NEUTRAL")
            dist = safe_float(h4.get("dist_ema21_atr"), 0.0)
            print(f" {currency} {pair_name} dir={direction} dist={dist} direct={direct}")
            # count any valid h4 as data
            has_data = True
            if direction in ("UP", "BULLISH", "BULL"):
                contrib = 1.0 + min(dist, 3.0) * 0.15 if direct else -1.0 - min(dist, 3.0) * 0.15
                score += contrib
                if direct:
                    bull_count += 1
            elif direction in ("DOWN", "BEARISH", "BEAR"):
                contrib = -1.0 - min(dist, 3.0) * 0.15 if direct else 1.0 + min(dist, 3.0) * 0.15
                score += contrib
                if not direct:
                    bull_count += 1
            else:
                # NEUTRAL still counts
                score += 0.0

        if not has_data:
            print(f"[MARKET_PULSE] {currency} NO DATA skip")
            continue

        # Force score to be valid float
        score = safe_float(round(score, 2), 0.0)
        print(f"[MARKET_PULSE] {currency} final score={score} bull={bull_count}/{total}")

        label = "Strong" if abs(score) > 1.0 else "Neutral" if abs(score) < 0.5 else ("Mixed" if score > 0 else "Weak")
        direction = "up" if score > 0.5 else "down" if score < -0.5 else "neutral"
        color = "green" if score > 0.5 else "red" if score < -0.5 else "yellow"

        result[currency] = {
            "strength": score,
            "direction": direction,
            "label": label,
            "bull_ratio": f"{bull_count}/{total}",
            "color": color
        }

    print(f"[MARKET_PULSE] Calculated raw: { {k: v['strength'] for k, v in result.items()} }")
    cleaned = sanitize_for_json(result)
    print(f"[MARKET_PULSE] Calculated cleaned: {cleaned}")
    return cleaned


def _find_available_port(start_port=18001, max_attempts=5):
    """Try to bind consecutive ports, return the first available one."""
    import errno
    for port in range(start_port, start_port + max_attempts):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(('127.0.0.1', port))
            srv.close()
            return port
        except OSError as e:
            srv.close()
            if e.errno == errno.EACCES:
                print(f"[SOCKET] Port {port} permission denied (need admin)")
            elif e.errno == errno.WSAEACCES or (hasattr(errno, 'WSAEACCES') and e.errno == errno.WSAEACCES):
                print(f"[SOCKET] Port {port} permission denied (need admin)")
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tomoko MT5 Bridge")
    parser.add_argument("--port", type=int, default=18001, help="EA socket server port (default: 18001)")
    args = parser.parse_args()

    # Try user-specified port first, then fallbacks
    requested_port = args.port
    port = requested_port
    try:
        srv_test = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv_test.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv_test.bind(('127.0.0.1', port))
        srv_test.close()
        print(f"Tomoko MT5 Bridge starting... (requested port {port})")
    except OSError:
        print(f"Requested port {port} unavailable, trying fallbacks...")
        port = _find_available_port(18001)
        if port is None:
            print("ERROR: No available ports in 18001-18005 range!")
            sys.exit(1)
        print(f"Using port {port} instead of {requested_port}")

    print(f"Broker: {ACTIVE_BROKER} | Path: {MT5_TERMINAL_PATH}")
    print(f"Cent mode: {ACCOUNT_CENT_MODE} | Symbols: {LIGHT_SYMBOLS}")

    # Start socket server thread first
    socket_thread = threading.Thread(target=_socket_server, args=(port,), daemon=True)
    socket_thread.start()
    time.sleep(1)

    bridge = MT5Bridge()
    bridge.connect()
    print(f"MT5 Bridge running - EA:{port} | TAB to continue... (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nMT5 Bridge stopped.")