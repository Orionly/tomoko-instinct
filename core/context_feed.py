import time
import math
import yfinance as yf
import MetaTrader5 as mt5
from datetime import datetime, timezone

_YF_TTL = 300  # seconds
_yf_cache = {}  # ticker -> {"price","change_pct","trend","ts"}
_yf_rate_limited_logged = False

_YF_FALLBACKS = {
    "DXY": {"price": 103.2, "change_pct": 0.0, "trend": "FLAT"},
    "US10Y": {"price": 4.30, "change_pct": 0.0, "trend": "FLAT"},
    "VIX": {"price": 18.0, "change_pct": 0.0, "trend": "FLAT"},
    "SPX": {"price": 5600.0, "change_pct": 0.0, "trend": "FLAT"},
    "OIL": {"price": 78.0, "change_pct": 0.0, "trend": "FLAT"},
    "GOLD": {"price": 2350.0, "change_pct": 0.0, "trend": "FLAT"},
}


def _safe_num(v, default=0.0):
    """Convert to float, replacing NaN/Inf with default."""
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def _safe_change(last, prev):
    """Percent change with NaN/zero protection."""
    p = _safe_num(prev)
    if p == 0.0:
        return 0.0
    chg = (_safe_num(last) - p) / p * 100
    return _safe_num(chg)


def _yf_fetch(ticker):
    """Fetch yfinance 2d history with 300s TTL cache. Rate-limit logs once."""
    global _yf_rate_limited_logged
    now = time.time()
    cached = _yf_cache.get(ticker)
    if cached and (now - cached["ts"]) < _YF_TTL:
        return {k: v for k, v in cached.items() if k != "ts"}
    try:
        data = yf.Ticker(ticker).history(period="2d")
        if len(data) >= 2:
            last = _safe_num(data['Close'].iloc[-1])
            prev = _safe_num(data['Close'].iloc[-2])
            change = _safe_change(last, prev)
            res = {
                "price": _safe_num(last),
                "change_pct": round(change, 2),
                "trend": "UP" if change > 0 else "DOWN" if change < 0 else "FLAT",
                "ts": now,
            }
            _yf_cache[ticker] = res
            _yf_rate_limited_logged = False
            return {k: v for k, v in res.items() if k != "ts"}
    except Exception as e:
        if "Too Many Requests" in str(e) or "Rate limited" in str(e) or "429" in str(e):
            if not _yf_rate_limited_logged:
                _yf_rate_limited_logged = True
                print(f"yfinance rate limited, using cached {ticker}")
    if cached:
        return {k: v for k, v in cached.items() if k != "ts"}
    return None


class ContextFeed:
    def __init__(self, mt5_bridge):
        self.mt5 = mt5_bridge
        self.tickers = {
            "DXY": "DX-Y.NYB",
            "US10Y": "^TNX",
            "VIX": "^VIX",
            "SPX": "^GSPC",
            "OIL": "CL=F",
            "GOLD": "GC=F",
        }

    def get_mt5_context_symbol(self, name):
        # Try to get DXY etc from MT5 if broker has them
        for cand in [name, name + "c", "US30", "SPX500", "VIX"]:
            tick = mt5.symbol_info_tick(cand)
            if tick:
                return cand
        return None

    def get_yfinance_price(self, ticker):
        res = _yf_fetch(ticker)
        if res is not None:
            return res
        # fallback by ticker
        for key, tk in self.tickers.items():
            if tk == ticker:
                return dict(_YF_FALLBACKS.get(key, {"price": 0, "change_pct": 0, "trend": "FLAT"}))
        return None

    def get_context(self):
        result = {}
        # Try MT5 first for GOLD (since we have XAU), then yfinance fallback
        for key, yf_ticker in self.tickers.items():
            # Special: GOLD we already have from MT5 XAUUSD mapping
            if key == "GOLD":
                xau_price = self.mt5.get_price("XAUUSD")
                if xau_price:
                    # Calculate change from daily rates
                    df = self.mt5.get_rates("XAUUSD", mt5.TIMEFRAME_D1, 5)
                    if not df.empty and len(df) >= 2:
                        prev_close = _safe_num(df.iloc[-2]['close'])
                        last_close = _safe_num(df.iloc[-1]['close'])
                        change = _safe_change(last_close, prev_close)
                        result[key] = {
                            "price": _safe_num(xau_price['bid']),
                            "change_pct": round(change, 2),
                            "trend": "UP" if change > 0 else "DOWN",
                            "source": "MT5",
                        }
                        continue
            # yfinance (cached 300s, rate-limit once)
            yf_data = self.get_yfinance_price(yf_ticker)
            if yf_data:
                result[key] = {**yf_data, "source": "yfinance"}
            else:
                fb = _YF_FALLBACKS.get(key, {"price": 0, "change_pct": 0, "trend": "FLAT"})
                result[key] = {**fb, "source": "none"}
        return result

    def get_risk_sentiment(self, context):
        # RISK-ON = VIX down + SPX up + DXY down
        vix = context.get("VIX", {}).get("change_pct", 0)
        spx = context.get("SPX", {}).get("change_pct", 0)
        dxy = context.get("DXY", {}).get("change_pct", 0)
        if vix < -0.5 and spx > 0 and dxy < 0:
            return "RISK-ON"
        if vix > 0.5 and spx < 0 and dxy > 0:
            return "RISK-OFF"
        return "NEUTRAL"

    def get_session(self):
        hour = datetime.now(timezone.utc).hour
        if 0 <= hour < 8:
            return "TOKYO", "active" if hour < 2 else ""
        if 8 <= hour < 13:
            return "LONDON", "active"
        if 13 <= hour < 21:
            return "NY", "active"
        return "SYDNEY", ""
