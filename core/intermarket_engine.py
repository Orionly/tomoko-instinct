# IntermarketEngine - real macro snapshot (MT5 GOLD + yfinance proxies)
# yfinance cached 300s TTL to avoid rate-limit spam
import time
import math
import yfinance as yf
import MetaTrader5 as mt5

_YF_TTL = 300  # seconds
_yf_cache = {}  # ticker -> {"price","chg","dir","ts"}
_yf_rate_limited_logged = False

_YF_FALLBACKS = {
    "DXY": {"price": 103.2, "chg": 0.0, "dir": "FLAT"},
    "US10Y": {"price": 4.30, "chg": 0.0, "dir": "FLAT"},
    "VIX": {"price": 18.0, "chg": 0.0, "dir": "FLAT"},
    "SPX": {"price": 5600.0, "chg": 0.0, "dir": "FLAT"},
    "OIL": {"price": 78.0, "chg": 0.0, "dir": "FLAT"},
}


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
            last = float(data['Close'].iloc[-1])
            prev = float(data['Close'].iloc[-2])
            if prev and not (math.isnan(prev) or math.isinf(prev)) and prev != 0:
                chg = (last - prev) / prev * 100
                if math.isnan(chg) or math.isinf(chg):
                    chg = 0.0
            else:
                chg = 0.0
            res = {
                "price": round(last, 2) if not (math.isnan(last) or math.isinf(last)) else 0.0,
                "chg": round(chg, 2),
                "dir": "UP" if chg > 0 else "DOWN" if chg < 0 else "FLAT",
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


class IntermarketEngine:
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

    def get_snapshot(self):
        out = {}
        # GOLD from real MT5 feed
        try:
            g = self.mt5.get_price("XAUUSD")
            if g:
                out["GOLD"] = {"price": g['bid'], "chg": 0.0, "dir": "FLAT"}
        except Exception:
            pass
        # Macro proxies from yfinance (cached 300s, rate-limit once)
        for key, tk in self.tickers.items():
            if key == "GOLD":
                continue
            res = _yf_fetch(tk)
            out[key] = res if res is not None else dict(_YF_FALLBACKS.get(key, {"price": 0, "chg": 0.0, "dir": "FLAT"}))
        return out
