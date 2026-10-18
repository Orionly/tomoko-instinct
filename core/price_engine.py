# Decoupled PriceEngine - Autonomous Market Data & Finnhub WebSocket
#
# Real-Time Spot / Ticks: Finnhub WebSocket (OANDA:EUR_USD, etc.)
# Historical Candle Bars: yfinance (H4, H1, M15, D1, W1) with background cache
# Completely decoupled from MetaTrader 5 (MT5).

import os
import time
import json
import math
import asyncio
import logging
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional
import pickle
import pandas as pd
import numpy as np
import websockets
import yfinance as yf

import config

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANDLES_CACHE_FILE = os.path.join(ROOT, "logs", "candles_cache.pkl")

FINNHUB_WS_URL = "wss://ws.finnhub.io"

SYMBOLS_MAP = {
    "EURUSD": "OANDA:EUR_USD",
    "GBPUSD": "OANDA:GBP_USD",
    "USDJPY": "OANDA:USD_JPY",
    "GBPJPY": "OANDA:GBP_JPY",
    "EURJPY": "OANDA:EUR_JPY",
    "EURGBP": "OANDA:EUR_GBP",
    "XAUUSD": "OANDA:XAU_USD",
}

REVERSE_MAP = {v: k for k, v in SYMBOLS_MAP.items()}

YF_MAP = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "GBPJPY": "GBPJPY=X",
    "EURJPY": "EURJPY=X",
    "EURGBP": "EURGBP=X",
    "XAUUSD": "PAXG-USD",  # Physical 1:1 Spot Gold bullion
}

POINTS_MAP = {
    "EURUSD": 0.00001,
    "GBPUSD": 0.00001,
    "USDJPY": 0.001,
    "GBPJPY": 0.001,
    "EURJPY": 0.001,
    "EURGBP": 0.00001,
    "XAUUSD": 0.01,
}

DEFAULT_SPREADS = {
    "EURUSD": 12,
    "GBPUSD": 15,
    "USDJPY": 14,
    "GBPJPY": 22,
    "EURJPY": 18,
    "EURGBP": 16,
    "XAUUSD": 25,
}

CURRENCY_MAP = {
    "EURUSD": ["EUR", "USD"],
    "GBPUSD": ["GBP", "USD"],
    "USDJPY": ["USD", "JPY"],
    "GBPJPY": ["GBP", "JPY"],
    "EURJPY": ["EUR", "JPY"],
    "EURGBP": ["EUR", "GBP"],
    "XAUUSD": ["XAU", "USD"],
}


def _fetch_gold_spot() -> Optional[Dict]:
    """
    Fetches live Physical Spot Gold (PAXG/USD) from Kraken with Coinbase & Yahoo fallback.
    Tracks spot gold down to the cent without requiring an API key or monthly limits.
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    # 1. Primary: Kraken public ticker
    try:
        url = "https://api.kraken.com/0/public/Ticker?pair=PAXGUSD"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            d = json.loads(resp.read().decode())
            res = d.get("result", {}).get("PAXGUSD", {})
            if res:
                bid = float(res["b"][0])
                ask = float(res["a"][0])
                mid = round((bid + ask) / 2.0, 2)
                spread_pts = max(1, int(round((ask - bid) / 0.01)))
                return {
                    "price": mid,
                    "bid": bid,
                    "ask": ask,
                    "spread": spread_pts,
                    "point": 0.01,
                    "source": "spot_oracle",
                }
    except Exception as e:
        log.debug("[PriceEngine] Kraken gold fetch note: %s", e)

    # 2. Secondary: Coinbase public ticker
    try:
        url = "https://api.exchange.coinbase.com/products/PAXG-USD/ticker"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            d = json.loads(resp.read().decode())
            bid = float(d["bid"])
            ask = float(d["ask"])
            mid = round((bid + ask) / 2.0, 2)
            spread_pts = max(1, int(round((ask - bid) / 0.01)))
            return {
                "price": mid,
                "bid": bid,
                "ask": ask,
                "spread": spread_pts,
                "point": 0.01,
                "source": "spot_oracle",
            }
    except Exception as e:
        log.debug("[PriceEngine] Coinbase gold fetch note: %s", e)

    # 3. Tertiary: Yahoo Finance PAXG-USD chart
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/PAXG-USD?interval=1m&range=1d"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            d = json.loads(resp.read().decode())
            meta = d["chart"]["result"][0]["meta"]
            price = float(meta["regularMarketPrice"])
            return {
                "price": price,
                "bid": round(price - 0.25, 2),
                "ask": round(price + 0.25, 2),
                "spread": 25,
                "point": 0.01,
                "source": "spot_oracle",
            }
    except Exception as e:
        log.debug("[PriceEngine] Yahoo PAXG-USD fetch note: %s", e)

    return None


def _fetch_forex_spot(pair: str, yf_sym: str) -> Optional[Dict]:
    """Fetches live interbank Forex spot price from Yahoo Finance v8 chart."""
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_sym}?interval=1m&range=1d"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            d = json.loads(resp.read().decode())
            meta = d["chart"]["result"][0]["meta"]
            price = float(meta["regularMarketPrice"])
            point = POINTS_MAP.get(pair, 0.00001)
            spread_pts = DEFAULT_SPREADS.get(pair, 15)
            half_spread = (spread_pts * point) / 2.0
            digits = 3 if "JPY" in pair else 5
            bid = round(price - half_spread, digits)
            ask = round(price + half_spread, digits)
            return {
                "price": price,
                "bid": bid,
                "ask": ask,
                "spread": spread_pts,
                "point": point,
                "source": "spot_oracle",
            }
    except Exception as e:
        log.debug("[PriceEngine] Forex spot fetch %s note: %s", pair, e)
        return None


class PriceEngine:
    """
    Autonomous Price & Rates Engine.
    Uses Multi-Source Spot Oracle for live ticks and yfinance for historical OHLCV bars.
    """

    def __init__(self, api_key: str = None):
        self.api_key = api_key or getattr(config, "FINNHUB_API_KEY", "")
        self.latest_prices: Dict[str, Dict] = {}
        self.candles_cache: Dict[str, Dict[str, pd.DataFrame]] = {}
        self.last_candle_update: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self.connected = False

        # Pre-seed initial prices and historical bars
        self._seed_initial_data()

        # Start background threads:
        # 1. Multi-Source Spot Oracle loop (runs every 5s, quota-free)
        self._spot_thread = threading.Thread(target=self._spot_oracle_loop, daemon=True, name="spot-oracle")
        self._spot_thread.start()

        # 2. Finnhub WebSocket stream (optional supplementary)
        if self.api_key:
            self._ws_thread = threading.Thread(target=self._start_ws_loop, daemon=True, name="finnhub-ws")
            self._ws_thread.start()

        # 3. Periodic candle refresher (every 90s)
        self._candle_thread = threading.Thread(target=self._candle_refresh_loop, daemon=True, name="candle-refresher")
        self._candle_thread.start()

    def poll_spot_oracle_all(self):
        """Queries all 7 symbols concurrently in <600ms and updates latest_prices."""
        now_sec = time.time()
        updates = {}

        with ThreadPoolExecutor(max_workers=7) as executor:
            futures = {}
            for pair, yf_sym in YF_MAP.items():
                if pair == "XAUUSD":
                    futures[executor.submit(_fetch_gold_spot)] = pair
                else:
                    futures[executor.submit(_fetch_forex_spot, pair, yf_sym)] = pair

            for fut in as_completed(futures):
                pair = futures[fut]
                try:
                    res = fut.result()
                    if res:
                        res["time"] = now_sec
                        res["symbol"] = pair
                        updates[pair] = res
                except Exception as ex:
                    log.debug("[PriceEngine] Spot oracle future error for %s: %s", pair, ex)

        if updates:
            with self._lock:
                for p, data in updates.items():
                    self.latest_prices[p] = data
                    # Also dynamically update latest candle bar close in memory
                    for tf in ("M15", "H1"):
                        cached = self.candles_cache.get(p, {}).get(tf)
                        if cached is not None and not cached.empty:
                            live_p = data["price"]
                            cached.iloc[-1, cached.columns.get_loc("close")] = live_p
                            if live_p > cached.iloc[-1]["high"]:
                                cached.iloc[-1, cached.columns.get_loc("high")] = live_p
                            if live_p < cached.iloc[-1]["low"]:
                                cached.iloc[-1, cached.columns.get_loc("low")] = live_p
                self.connected = True

    def _spot_oracle_loop(self):
        """Refreshes spot prices every 5 seconds continuously."""
        # Run immediately on thread start
        try:
            self.poll_spot_oracle_all()
        except Exception as e:
            log.debug("[PriceEngine] Initial spot poll error: %s", e)

        while not self._stop_event.is_set():
            time.sleep(5)
            try:
                self.poll_spot_oracle_all()
            except Exception as e:
                log.debug("[PriceEngine] Spot oracle poll error: %s", e)

    def _load_disk_cache(self) -> bool:
        """Load candles and prices from disk cache for instant startup (<10ms)."""
        try:
            if os.path.isfile(CANDLES_CACHE_FILE):
                with open(CANDLES_CACHE_FILE, "rb") as f:
                    data = pickle.load(f)
                    self.candles_cache = data.get("candles", {})
                    self.latest_prices = data.get("prices", {})
                    if self.candles_cache:
                        self.connected = True
                        log.info("[PriceEngine] Instantly loaded %d pairs from disk cache.", len(self.candles_cache))
                        return True
        except Exception as e:
            log.debug("[PriceEngine] Disk cache read note: %s", e)
        return False

    def _save_disk_cache(self):
        """Save current candles to disk cache."""
        try:
            os.makedirs(os.path.dirname(CANDLES_CACHE_FILE), exist_ok=True)
            with open(CANDLES_CACHE_FILE, "wb") as f:
                with self._lock:
                    pickle.dump({
                        "candles": self.candles_cache,
                        "prices": self.latest_prices,
                        "timestamp": time.time(),
                    }, f)
        except Exception as e:
            log.debug("[PriceEngine] Disk cache write note: %s", e)

    def _seed_initial_data(self):
        """Load disk cache immediately for instant startup, then refresh in background."""
        has_cache = self._load_disk_cache()
        self.connected = True
        if not has_cache:
            try:
                log.info("[PriceEngine] No disk cache found. Fetching initial candles...")
                self.refresh_all_candles()
            except Exception as e:
                log.warning("[PriceEngine] Initial fetch warning: %s", e)
        else:
            # Refresh in background so startup is 100% instant
            threading.Thread(target=self.refresh_all_candles, daemon=True, name="bg-initial-refresh").start()

    def _start_ws_loop(self):
        """Runs the asyncio websocket event loop with automatic reconnect."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while not self._stop_event.is_set():
            try:
                loop.run_until_complete(self._finnhub_ws_client())
            except Exception as e:
                log.debug("[PriceEngine] WS connection dropped: %s. Reconnecting in 5s...", e)
                time.sleep(5)

    async def _finnhub_ws_client(self):
        ws_url = f"{FINNHUB_WS_URL}?token={self.api_key}"
        async with websockets.connect(ws_url, ping_interval=20, ping_timeout=10) as ws:
            self.connected = True
            log.info("[PriceEngine] Finnhub WebSocket connected successfully.")

            # Subscribe to all 7 pairs
            for sym in SYMBOLS_MAP.values():
                await ws.send(json.dumps({"type": "subscribe", "symbol": sym}))

            while not self._stop_event.is_set():
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=25.0)
                    data = json.loads(msg)
                    msg_type = data.get("type")

                    if msg_type == "trade":
                        trades = data.get("data", [])
                        now_sec = time.time()
                        for t in trades:
                            fh_sym = t.get("s")
                            pair = REVERSE_MAP.get(fh_sym)
                            price = t.get("p")
                            if pair and price:
                                point = POINTS_MAP.get(pair, 0.00001)
                                spread_pts = DEFAULT_SPREADS.get(pair, 15)
                                half_spread = (spread_pts * point) / 2.0
                                bid = round(price - half_spread, 5 if "JPY" not in pair and pair != "XAUUSD" else (3 if pair == "XAUUSD" else 3))
                                ask = round(price + half_spread, 5 if "JPY" not in pair and pair != "XAUUSD" else (3 if pair == "XAUUSD" else 3))

                                with self._lock:
                                    self.latest_prices[pair] = {
                                        "price": price,
                                        "bid": bid,
                                        "ask": ask,
                                        "spread": spread_pts,
                                        "point": point,
                                        "time": now_sec,
                                        "symbol": pair,
                                        "source": "finnhub_ws",
                                    }

                    elif msg_type == "ping":
                        await ws.send(json.dumps({"type": "pong"}))

                except asyncio.TimeoutError:
                    # Keepalive ping
                    await ws.ping()
                except Exception as e:
                    log.debug("[PriceEngine] WS message error: %s", e)
                    break

    # -------------------------------------------------------------
    # Candle Bar Management (yfinance Layer)
    # -------------------------------------------------------------

    def _candle_refresh_loop(self):
        """Refreshes H4, H1, M15 candles in the background every 90 seconds."""
        while not self._stop_event.is_set():
            time.sleep(90)
            try:
                self.refresh_all_candles()
            except Exception as e:
                log.debug("[PriceEngine] Candle refresh error: %s", e)

    def refresh_all_candles(self):
        """Batch downloads latest bars for H1, M15, D1."""
        tickers = list(YF_MAP.values())

        # 1. Download H1 (used for H1 and resampled to H4)
        try:
            df_h1_raw = yf.download(tickers, period="10d", interval="1h", progress=False)
            now_ts = time.time()
            for pair, yf_sym in YF_MAP.items():
                try:
                    if len(tickers) == 1:
                        sub = df_h1_raw.copy()
                    else:
                        sub = pd.DataFrame({
                            "open": df_h1_raw["Open"][yf_sym],
                            "high": df_h1_raw["High"][yf_sym],
                            "low": df_h1_raw["Low"][yf_sym],
                            "close": df_h1_raw["Close"][yf_sym],
                            "volume": df_h1_raw["Volume"][yf_sym] if "Volume" in df_h1_raw else 0,
                        }).dropna()

                    if not sub.empty:
                        sub.index = pd.to_datetime(sub.index)
                        if sub.index.tz is None:
                            sub.index = sub.index.tz_localize("UTC").tz_convert("Asia/Kuala_Lumpur")
                        else:
                            sub.index = sub.index.tz_convert("Asia/Kuala_Lumpur")
                        sub["time"] = sub.index

                        # Compute H1 with indicators
                        h1_ind = self._add_indicators(sub.copy())

                        # Compute H4 by 4h resampling anchored to 6:00 AM MYT session cycle (06:00, 10:00, 14:00, 18:00, 22:00, 02:00)
                        h4_res = sub.resample("4h", offset="2h").agg({
                            "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum", "time": "first"
                        }).dropna()
                        h4_ind = self._add_indicators(h4_res)

                        # Compute D1 by 1d resampling strictly anchored to 06:00 AM Malaysia Time (Forex NY 5pm rollover)
                        d1_res = sub.resample("1D", offset="6h").agg({
                            "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum", "time": "first"
                        }).dropna()
                        d1_ind = self._add_indicators(d1_res)

                        # Compute W1 by 1W resampling anchored to Monday 06:00 AM Malaysia Time
                        w1_res = sub.resample("W-MON", offset="6h").agg({
                            "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum", "time": "first"
                        }).dropna()
                        w1_ind = self._add_indicators(w1_res)

                        with self._lock:
                            if pair not in self.candles_cache:
                                self.candles_cache[pair] = {}
                            self.candles_cache[pair]["H1"] = h1_ind
                            self.candles_cache[pair]["H4"] = h4_ind
                            self.candles_cache[pair]["D1"] = d1_ind
                            self.candles_cache[pair]["W1"] = w1_ind
                            self.last_candle_update[pair] = now_ts

                            # If no live tick yet, seed latest_prices with latest close
                            if pair not in self.latest_prices:
                                last_c = float(sub["close"].iloc[-1])
                                pt = POINTS_MAP.get(pair, 0.00001)
                                sp = DEFAULT_SPREADS.get(pair, 15)
                                self.latest_prices[pair] = {
                                    "price": last_c,
                                    "bid": round(last_c - (sp * pt) / 2, 5),
                                    "ask": round(last_c + (sp * pt) / 2, 5),
                                    "spread": sp,
                                    "point": pt,
                                    "time": now_ts,
                                    "symbol": pair,
                                    "source": "yf_seed",
                                }
                except Exception as ex:
                    log.debug("[PriceEngine] Pair %s candle processing error: %s", pair, ex)

        except Exception as e:
            log.warning("[PriceEngine] Batch yfinance download warning: %s", e)

        # 2. Download M15 for M15 bias
        try:
            df_m15_raw = yf.download(tickers, period="3d", interval="15m", progress=False)
            for pair, yf_sym in YF_MAP.items():
                try:
                    if len(tickers) == 1:
                        sub = df_m15_raw.copy()
                    else:
                        sub = pd.DataFrame({
                            "open": df_m15_raw["Open"][yf_sym],
                            "high": df_m15_raw["High"][yf_sym],
                            "low": df_m15_raw["Low"][yf_sym],
                            "close": df_m15_raw["Close"][yf_sym],
                            "volume": df_m15_raw["Volume"][yf_sym] if "Volume" in df_m15_raw else 0,
                        }).dropna()
                    if not sub.empty:
                        sub.index = pd.to_datetime(sub.index)
                        if sub.index.tz is None:
                            sub.index = sub.index.tz_localize("UTC").tz_convert("Asia/Kuala_Lumpur")
                        else:
                            sub.index = sub.index.tz_convert("Asia/Kuala_Lumpur")
                        sub["time"] = sub.index
                        m15_ind = self._add_indicators(sub)
                        with self._lock:
                            if pair not in self.candles_cache:
                                self.candles_cache[pair] = {}
                            self.candles_cache[pair]["M15"] = m15_ind
                except Exception as ex:
                    log.debug("[PriceEngine] M15 pair %s error: %s", pair, ex)
        except Exception as e:
            log.warning("[PriceEngine] M15 yfinance download warning: %s", e)

        # Save to disk cache for instantaneous reloads
        self._save_disk_cache()

    @staticmethod
    def _add_indicators(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or len(df) < 14:
            return df
        close = df["close"]
        high = df["high"]
        low = df["low"]

        # EMAs
        df["EMA20"] = close.ewm(span=20, adjust=False).mean()
        df["EMA50"] = close.ewm(span=50, adjust=False).mean()
        df["EMA21"] = close.ewm(span=21, adjust=False).mean()

        # ATR (14)
        tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        df["ATR"] = tr.rolling(14).mean().bfill().replace(0, 0.0001)

        # RSI (14)
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
        avg_gain = gain.rolling(14).mean()
        rs = avg_gain / loss.replace(0, np.nan)
        df["RSI"] = (100 - (100 / (1 + rs))).fillna(50)

        # ADX (14)
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        atr14 = tr.ewm(alpha=1 / 14, adjust=False).mean().replace(0, 0.0001)
        plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / 14, adjust=False).mean() / atr14
        minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / 14, adjust=False).mean() / atr14
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        df["ADX"] = dx.ewm(alpha=1 / 14, adjust=False).mean().fillna(20)

        # Distance from EMA21 in ATR units
        df["dist_ema21_atr"] = ((close - df["EMA21"]).abs() / df["ATR"].replace(0, np.nan)).round(2)
        df["dist_ema21_atr"] = df["dist_ema21_atr"].fillna(0.0)

        return df

    # -------------------------------------------------------------
    # Public API Compatible with MT5Bridge
    # -------------------------------------------------------------

    def get_price(self, symbol: str) -> Optional[Dict]:
        with self._lock:
            if symbol in self.latest_prices:
                return dict(self.latest_prices[symbol])
        return None

    def get_rates(self, symbol: str, timeframe, count: int = 200) -> pd.DataFrame:
        """
        Returns indicator-augmented DataFrame for symbol & timeframe.
        Accepts MT5 timeframe constants (16388=H4, 16385=H1, 15=M15, 16408=D1, 32769=W1) or strings.
        """
        # Map timeframe to key
        tf_key = "H1"
        tf_str = str(timeframe)
        if tf_str in ("16388", "H4", "TIMEFRAME_H4"):
            tf_key = "H4"
        elif tf_str in ("16385", "H1", "TIMEFRAME_H1"):
            tf_key = "H1"
        elif tf_str in ("15", "M15", "TIMEFRAME_M15"):
            tf_key = "M15"
        elif tf_str in ("16408", "D1", "TIMEFRAME_D1", "1440"):
            tf_key = "D1"
        elif tf_str in ("32769", "W1", "TIMEFRAME_W1", "10080"):
            tf_key = "W1"

        with self._lock:
            cached = self.candles_cache.get(symbol, {}).get(tf_key)
            if cached is None or cached.empty:
                # Resilient fallback: use H1 if M15 missing, or D1 if W1 missing
                if tf_key == "M15":
                    cached = self.candles_cache.get(symbol, {}).get("H1")
                elif tf_key == "W1":
                    cached = self.candles_cache.get(symbol, {}).get("D1")
                elif tf_key == "D1":
                    cached = self.candles_cache.get(symbol, {}).get("H4")
            if cached is not None and not cached.empty:
                df = cached.copy()
                # Update latest bar's close with real-time Finnhub tick if available
                if symbol in self.latest_prices:
                    live_p = self.latest_prices[symbol]["price"]
                    df.iloc[-1, df.columns.get_loc("close")] = live_p
                    if live_p > df.iloc[-1]["high"]:
                        df.iloc[-1, df.columns.get_loc("high")] = live_p
                    if live_p < df.iloc[-1]["low"]:
                        df.iloc[-1, df.columns.get_loc("low")] = live_p
                return df.tail(count)

        return pd.DataFrame()

    def get_all_pair_info(self, pairs: List[str]) -> Dict[str, Dict]:
        data = {}
        for p in pairs:
            q = self.get_price(p)
            if q:
                data[p] = {
                    "spread": q.get("spread", 15),
                    "bid": q.get("bid", 0.0),
                    "ask": q.get("ask", 0.0),
                    "point": q.get("point", 0.00001),
                }
        return data

    def get_currencies(self, symbol: str) -> List[str]:
        return CURRENCY_MAP.get(symbol, ["USD"])

    def get_prices_from_ea(self) -> Dict:
        """Compatibility method: returns latest live prices."""
        with self._lock:
            return {k: dict(v) for k, v in self.latest_prices.items()}


    @staticmethod
    def _safe_num(v):
        try:
            f = float(v)
            return 0.0 if math.isnan(f) or math.isinf(f) else f
        except (TypeError, ValueError):
            return 0.0

    def get_market_heatmap(self, scan_results: Dict) -> List[Dict]:
        """Build momentum & alignment heatmap from scan results.
        Calculates pips change, percent change, and daily range from D1 open.
        """
        heatmap = []
        for symbol, data in scan_results.items():
            score = self._safe_num(data.get("score", 0))
            action = data.get("action", "WAIT")
            price = self._safe_num(data.get("price", 0))
            daily_open = self._safe_num(data.get("daily_open", data.get("levels", {}).get("daily_open", price)))
            if daily_open <= 0:
                daily_open = price

            # Pip and % change
            pct_change = 0.0
            pips_change = 0.0
            is_gold = bool("XAU" in symbol.upper() or "GOLD" in symbol.upper())
            if daily_open > 0 and price > 0:
                pct_change = round(((price - daily_open) / daily_open) * 100.0, 2)
                diff = price - daily_open
                if "JPY" in symbol.upper():
                    pips_change = round(diff / 0.01, 1)
                elif is_gold:
                    pips_change = round(diff, 2)
                else:
                    pips_change = round(diff / 0.0001, 1)

            lv = data.get("levels", {})
            today_high = self._safe_num(lv.get("today_high", lv.get("daily_high", price)))
            today_low = self._safe_num(lv.get("today_low", lv.get("daily_low", price)))
            atr = self._safe_num(lv.get("atr", data.get("atr", 0)))

            # H4 trend
            h4_dir = ""
            h4_block = data.get("h4")
            if isinstance(h4_block, dict):
                h4_dir = str(h4_block.get("direction", "")).upper()
            h4_str = "BULL" if h4_dir == "UP" else "BEAR" if h4_dir == "DOWN" else "FLAT"

            # H1 trend
            h1_raw = str(data.get("h1_bias", "")).upper()
            if "BULL" in h1_raw:
                h1_str = "BULL"
            elif "BEAR" in h1_raw:
                h1_str = "BEAR"
            else:
                h1_str = "FLAT"

            # M15 trend
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

            dist = self._safe_num(
                data.get("h4", {}).get("dist_ema21_atr", 0)
                if isinstance(data.get("h4"), dict)
                else data.get("dist_ema21_atr", 0)
            )

            heatmap.append({
                "pair": symbol,
                "price": float(price),
                "daily_open": float(daily_open),
                "pct_change": float(pct_change),
                "pips_change": float(pips_change),
                "today_high": float(today_high),
                "today_low": float(today_low),
                "atr": float(atr),
                "is_gold": is_gold,
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


def safe_float(v, default=0.0) -> float:
    """Safely convert value to float, filtering out None, NaN, and Inf."""
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
    """Recursively sanitize data structures for JSON serialization (clean NaN/Inf)."""
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


def calculate_currency_strength(scan_results: dict) -> dict:
    """Calculate Market Pulse currency strength meters (USD, EUR, GBP, JPY, XAU) from pair scan results."""
    currency_pairs = {
        "USD": [("EURUSD", False), ("GBPUSD", False), ("USDJPY", True), ("XAUUSD", False)],
        "EUR": [("EURUSD", True), ("EURJPY", True), ("EURGBP", True)],
        "GBP": [("GBPUSD", True), ("GBPJPY", True), ("EURGBP", False)],
        "JPY": [("USDJPY", False), ("GBPJPY", False), ("EURJPY", False)],
        "XAU": [("XAUUSD", True)],
    }
    result = {}
    for currency, pairs in currency_pairs.items():
        score = 0.0
        bull_count = 0
        total = len(pairs)
        has_data = False
        for pair_name, direct in pairs:
            scan = scan_results.get(pair_name)
            if not scan:
                continue
            h4 = scan.get("h4", {})
            if not h4:
                continue
            direction = h4.get("direction", "NEUTRAL")
            dist = safe_float(h4.get("dist_ema21_atr"), 0.0)
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
                score += 0.0

        if not has_data:
            continue

        score = safe_float(round(score, 2), 0.0)
        label = "Strong" if score > 1.0 else "Weak" if score < -1.0 else "Neutral" if abs(score) < 0.5 else ("Mixed" if score > 0 else "Weak")
        direction = "up" if score > 0.5 else "down" if score < -0.5 else "neutral"
        color = "green" if score > 0.5 else "red" if score < -0.5 else "yellow"

        result[currency] = {
            "strength": score,
            "direction": direction,
            "label": label,
            "bull_ratio": f"{bull_count}/{total}",
            "color": color
        }

    # Calculate top gainer and top loser among fiat currencies
    fiat_items = [(c, d["strength"]) for c, d in result.items() if c != "XAU" and isinstance(d, dict) and "strength" in d]
    if fiat_items:
        fiat_items.sort(key=lambda x: x[1], reverse=True)
        top_g = fiat_items[0]
        top_l = fiat_items[-1]
        result["_top_gainer"] = {"currency": top_g[0], "strength": top_g[1]}
        result["_top_loser"] = {"currency": top_l[0], "strength": top_l[1]}

    return sanitize_for_json(result)

