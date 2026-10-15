import time
import math
import urllib.request
import yfinance as yf
from datetime import datetime, timezone

# Cache settings
_FRED_TTL = 3600 * 6  # 6 hours for daily series
_YF_TTL = 300         # 5 minutes for live market data

_cache = {
    "fred_dfii10": {"val": 2.88, "ts": 0, "date": ""},
    "yf_data": {}
}

_FALLBACK_MATRIX = {
    "DXY": {"price": 103.20, "change_pct": 0.0, "trend": "FLAT"},
    "US10Y": {"price": 4.30, "change_pct": 0.0, "trend": "FLAT"},
    "TIPS_ETF": {"price": 104.50, "change_pct": 0.0, "trend": "FLAT"},
    "OIL": {"price": 78.00, "change_pct": 0.0, "trend": "FLAT"},
    "SPX": {"price": 5600.0, "change_pct": 0.0, "trend": "FLAT"},
    "VIX": {"price": 18.0, "change_pct": 0.0, "trend": "FLAT"},
}

def _safe_float(v, default=0.0):
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default

def _safe_pct_change(last, prev):
    p = _safe_float(prev)
    if p == 0.0:
        return 0.0
    return round((_safe_float(last) - p) / p * 100, 2)


class MacroIntelligenceEngine:
    def __init__(self):
        self.tickers = {
            "DXY": "DX-Y.NYB",
            "US10Y": "^TNX",
            "TIPS_ETF": "TIP",
            "OIL": "CL=F",
            "SPX": "^GSPC",
            "VIX": "^VIX",
        }

    def get_fred_10y_real_yield(self):
        """
        Fetch latest 10-Year Treasury Inflation-Indexed Security (Constant Maturity)
        from St. Louis Fed (FRED: DFII10). Zero API key required.
        """
        now = time.time()
        cached = _cache["fred_dfii10"]
        if cached["ts"] > 0 and (now - cached["ts"]) < _FRED_TTL:
            return cached["val"], cached["date"]

        url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Tomoko-MacroEngine/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                lines = resp.read().decode("utf-8").strip().splitlines()
                for line in reversed(lines[1:]):
                    parts = line.split(",")
                    if len(parts) == 2 and parts[1].strip() not in ("", "."):
                        val = _safe_float(parts[1].strip(), default=cached["val"])
                        _cache["fred_dfii10"] = {
                            "val": val,
                            "ts": now,
                            "date": parts[0].strip()
                        }
                        return val, parts[0].strip()
        except Exception:
            pass

        return cached["val"], cached["date"] or "Latest Fed Close"

    def _fetch_yf_ticker(self, ticker):
        now = time.time()
        cached = _cache["yf_data"].get(ticker)
        if cached and (now - cached["ts"]) < _YF_TTL:
            return {k: v for k, v in cached.items() if k != "ts"}

        try:
            t = yf.Ticker(ticker)
            df = t.history(period="2d")
            if len(df) >= 2:
                last = _safe_float(df["Close"].iloc[-1])
                prev = _safe_float(df["Close"].iloc[-2])
                chg = _safe_pct_change(last, prev)
                res = {
                    "price": last,
                    "change_pct": chg,
                    "trend": "UP" if chg > 0 else "DOWN" if chg < 0 else "FLAT",
                    "ts": now
                }
                _cache["yf_data"][ticker] = res
                return {k: v for k, v in res.items() if k != "ts"}
        except Exception:
            pass

        if cached:
            return {k: v for k, v in cached.items() if k != "ts"}
        return None

    def get_macro_matrix(self):
        """
        Extracts all 6 core pillars, including the Hybrid 10Y TIPS data.
        """
        matrix = {}
        for key, sym in self.tickers.items():
            data = self._fetch_yf_ticker(sym)
            if not data:
                data = dict(_FALLBACK_MATRIX.get(key, {"price": 0.0, "change_pct": 0.0, "trend": "FLAT"}))
            matrix[key] = data

        fed_yield, fed_date = self.get_fred_10y_real_yield()
        tip_etf = matrix["TIPS_ETF"]
        
        # Real yield interpretation:
        # If TIP ETF is UP -> Real Yield is softening (falling)
        # If TIP ETF is DOWN -> Real Yield is expanding (rising)
        yield_press = "FALLING" if tip_etf["change_pct"] > 0 else "RISING" if tip_etf["change_pct"] < 0 else "STEADY"

        matrix["TIPS_HYBRID"] = {
            "real_yield_pct": fed_yield,
            "benchmark_date": fed_date,
            "live_etf_price": tip_etf["price"],
            "live_etf_change": tip_etf["change_pct"],
            "live_etf_trend": tip_etf["trend"],
            "yield_pressure": yield_press
        }

        return matrix

    def get_sessions_intelligence(self, vix_price=18.0):
        """
        Calculates session state, pre-market windows, and expected volatility.
        """
        now = datetime.now(timezone.utc)
        cur_min = now.hour * 60 + now.minute

        def format_delta(target_min):
            diff = (target_min - cur_min) % 1440
            h = diff // 60
            m = diff % 60
            if h == 0:
                return f"{m}m"
            return f"{h}h {m}m" if m > 0 else f"{h}h"

        # Session intervals (in minutes from UTC midnight)
        # Tokyo: 00:00 (0) - 09:00 (540)
        # London: 07:00 (420) - 16:00 (960)
        # New York: 12:00 (720) - 21:00 (1260)
        tokyo_active = 0 <= cur_min < 540
        london_active = 420 <= cur_min < 960
        ny_active = 720 <= cur_min < 1260
        overlap_active = 720 <= cur_min < 960  # London & NY overlap

        # Base volatility tier driven by VIX
        vol_base = "HIGH" if vix_price > 20.0 else "LOW" if vix_price < 14.5 else "MODERATE"

        # Tokyo details
        if tokyo_active:
            tokyo_state = "ACTIVE"
            tokyo_vol = "MODERATE" if vol_base != "HIGH" else "HIGH"
            tokyo_note = "Asian range setting liquidity benchmarks."
        elif cur_min >= 1260 or cur_min < 0:
            tokyo_state = f"PRE-MARKET ({format_delta(0)})"
            tokyo_vol = "ACCUMULATION"
            tokyo_note = "Pre-Tokyo positioning."
        else:
            tokyo_state = "CLOSED"
            tokyo_vol = "LOW"
            tokyo_note = f"Opens in {format_delta(0)}"

        # London details
        if london_active:
            tokyo_vol_desc = "PEAK (OVERLAP)" if overlap_active else vol_base
            london_state = "ACTIVE (OVERLAP)" if overlap_active else "ACTIVE"
            london_vol = tokyo_vol_desc
            london_note = "Primary European institutional liquidity active."
        elif 300 <= cur_min < 420:
            london_state = f"PRE-MARKET ({format_delta(420)})"
            london_vol = "EXPANDING"
            london_note = "Liquidity vacuum possible ahead of European open."
        else:
            london_state = "CLOSED"
            london_vol = "LOW"
            london_note = f"Opens in {format_delta(420)}"

        # New York details
        if ny_active:
            ny_state = "ACTIVE (OVERLAP)" if overlap_active else "ACTIVE"
            ny_vol = "PEAK (OVERLAP)" if overlap_active else vol_base
            ny_note = "High volatility window with US macro data digested."
        elif 600 <= cur_min < 720:
            ny_state = f"PRE-MARKET ({format_delta(720)})"
            ny_vol = "ACCELERATING"
            ny_note = "US pre-market liquidity builds; watch 8:30 AM EST releases."
        else:
            ny_state = "CLOSED"
            ny_vol = "LOW"
            ny_note = f"Opens in {format_delta(720)}"

        # Determine overall dominant market liquidity regime
        if overlap_active:
            macro_liquidity = "PEAK INSTITUTIONAL OVERLAP (London + NY)"
        elif london_active or ny_active:
            macro_liquidity = "HIGH ACTIVE LIQUIDITY"
        elif tokyo_active:
            macro_liquidity = "MODERATE ASIAN SESSION LIQUIDITY"
        else:
            macro_liquidity = "OFF-PEAK INTER-SESSION WINDOW"

        return {
            "macro_liquidity": macro_liquidity,
            "overlap_active": overlap_active,
            "tokyo": {"state": tokyo_state, "volatility": tokyo_vol, "note": tokyo_note},
            "london": {"state": london_state, "volatility": london_vol, "note": london_note},
            "new_york": {"state": ny_state, "volatility": ny_vol, "note": ny_note},
        }

    def synthesize_macro_insight(self, matrix):
        """
        Synthesizes macro metrics into deterministic, professional decision-support context.
        """
        dxy = matrix.get("DXY", {})
        spx = matrix.get("SPX", {})
        vix = matrix.get("VIX", {})
        us10y = matrix.get("US10Y", {})
        tips = matrix.get("TIPS_HYBRID", {})
        oil = matrix.get("OIL", {})

        vix_p = vix.get("price", 18.0)
        vix_chg = vix.get("change_pct", 0.0)
        spx_chg = spx.get("change_pct", 0.0)
        dxy_chg = dxy.get("change_pct", 0.0)
        us10y_chg = us10y.get("change_pct", 0.0)
        yield_press = tips.get("yield_pressure", "STEADY")
        real_yield = tips.get("real_yield_pct", 2.88)
        oil_chg = oil.get("change_pct", 0.0)

        # 1. Determine Broad Market Regime
        if vix_p > 22.0 or (vix_chg > 4.0 and spx_chg < -0.5):
            regime = "RISK-OFF // ELEVATED VOLATILITY"
            regime_class = "bear"
            color = "#FF4D4D"
        elif vix_p < 16.5 and spx_chg >= 0 and dxy_chg <= 0.15:
            regime = "RISK-ON // LIQUIDITY EXPANSION"
            regime_class = "bull"
            color = "#00FF88"
        elif dxy_chg > 0.35 and us10y_chg > 0.5:
            regime = "DOLLAR DOMINANCE // RATES PRESSURE"
            regime_class = "neut"
            color = "#FFB800"
        elif oil_chg > 2.0 and us10y_chg > 0.3:
            regime = "INFLATION SQUEEZE // ENERGY WATCH"
            regime_class = "neut"
            color = "#FFB800"
        else:
            regime = "BALANCED // CONSOLIDATION REGIME"
            regime_class = "neut"
            color = "#00E5FF"

        # 2. Intermarket Cross Impacts
        if yield_press == "RISING":
            gold_impact = "Headwinds for Gold (XAUUSD) as real yields climb."
            usd_impact = "Supports USD upside on widening real yield spreads."
        elif yield_press == "FALLING":
            gold_impact = "Tailwinds for Gold (XAUUSD) as real yield costs soften."
            usd_impact = "Softens aggressive USD bids; favors non-dollar liquidity."
        else:
            gold_impact = "Real yields neutral; Gold following structural levels."
            usd_impact = "USD driven by equity correlations and session flow."

        if oil_chg > 1.5:
            oil_impact = f"WTI surge ({oil_chg:+.2f}%) fuels inflation expectations."
        elif oil_chg < -1.5:
            oil_impact = f"WTI cooling ({oil_chg:+.2f}%) eases headline commodity pressures."
        else:
            oil_impact = "Crude oil stable."

        # 3. Decision Narrative for Human Traders
        headline = f"10Y Real Yield at {real_yield:.2f}% ({yield_press.lower()} intraday). VIX at {vix_p:.1f} ({vix_chg:+.1f}%)."
        decision_context = f"{gold_impact} {usd_impact} {oil_impact} S&P shift ({spx_chg:+.2f}%) indicates {'broad risk appetite' if spx_chg > 0 else 'defensive rotation'}."

        return {
            "regime": regime,
            "regime_class": regime_class,
            "color": color,
            "headline": headline,
            "decision_context": decision_context,
            "gold_impact": gold_impact,
            "usd_impact": usd_impact,
            "oil_impact": oil_impact,
            "yield_pressure": yield_press,
            "vix_regime": "HIGH" if vix_p > 22 else "LOW" if vix_p < 15 else "NORMAL"
        }

    def get_pair_macro_confluence(self, symbol, matrix=None, insight=None):
        """
        Translates broader macro forces into specific, actionable confluence for a given currency pair.
        Supports Option 1 (Deep Dive Alignment).
        """
        if not matrix:
            matrix = self.get_macro_matrix()
        if not insight:
            insight = self.synthesize_macro_insight(matrix)

        dxy = matrix.get("DXY", {})
        tips = matrix.get("TIPS_HYBRID", {})
        us10y = matrix.get("US10Y", {})
        vix = matrix.get("VIX", {})
        regime = insight.get("regime", "")
        yield_press = tips.get("yield_pressure", "STEADY")

        sym = symbol.upper()

        if "XAU" in sym or "GOLD" in sym:
            # Gold strongly driven by Real Yields & USD
            if yield_press == "FALLING" and dxy.get("change_pct", 0) <= 0.1:
                status = "FAVORABLE LONGS"
                cls = "bull"
                rationale = "Softening real yields (TIPS pressure FALLING) + soft DXY provides macro tailwind for Gold upside."
            elif yield_press == "RISING" and dxy.get("change_pct", 0) > 0:
                status = "BEARISH HEADWIND"
                cls = "bear"
                rationale = "Rising real yields + stronger USD create strong macro friction against long positioning."
            else:
                status = "MIXED MACRO"
                cls = "neut"
                rationale = f"Real yield pressure {yield_press}; prioritize technical liquidity levels over macro momentum."

        elif sym in ("EURUSD", "GBPUSD"):
            dxy_chg = dxy.get("change_pct", 0)
            if dxy_chg < -0.15 and "RISK-ON" in regime:
                status = "FAVORABLE LONGS"
                cls = "bull"
                rationale = "DXY downward momentum coupled with Risk-On sentiment supports pro-cyclical currency appreciation."
            elif dxy_chg > 0.25 or "RISK-OFF" in regime:
                status = "BEARISH HEADWIND"
                cls = "bear"
                rationale = "DXY upside momentum and defensive risk tone pose headwinds for EUR/GBP longs."
            else:
                status = "BALANCED"
                cls = "neut"
                rationale = "DXY trend is neutral; watch key session high/low breakouts."

        elif sym == "USDJPY":
            us10y_chg = us10y.get("change_pct", 0)
            vix_val = vix.get("price", 18.0)
            if us10y_chg > 0.5 and vix_val < 20.0:
                status = "FAVORABLE LONGS"
                cls = "bull"
                rationale = "US 10Y Yields climbing while risk appetite remains stable; widening yield differential favors USDJPY."
            elif vix_val > 22.0 or us10y_chg < -1.0:
                status = "BEARISH HEADWIND"
                cls = "bear"
                rationale = "Risk-Off flight to safety boosts JPY demand; lower Treasury yields compress US-Japan rate spread."
            else:
                status = "CONSOLIDATION"
                cls = "neut"
                rationale = "US rate spread stable; look for range exhaustion at liquidity boundaries."

        elif "JPY" in sym:  # GBPJPY, EURJPY
            vix_val = vix.get("price", 18.0)
            if "RISK-OFF" in regime or vix_val > 22.0:
                status = "BEARISH RISK-OFF"
                cls = "bear"
                rationale = "High VIX and risk aversion favor Japanese Yen safe-haven flows; watch for JPY cross liquidations."
            elif "RISK-ON" in regime:
                status = "BULLISH CARRY"
                cls = "bull"
                rationale = "Risk-On environment favors carry trade upside; JPY crosses supported."
            else:
                status = "NEUTRAL CONFLUENCE"
                cls = "neut"
                rationale = "Cross-currency flows balanced; monitor session high/low bounds."

        else:
            status = "NEUTRAL"
            cls = "neut"
            rationale = "Macro factors neutral for this asset; rely on technical alignment scores."

        return {
            "symbol": sym,
            "status": status,
            "class": cls,
            "rationale": rationale,
            "yield_pressure": yield_press,
            "regime": regime
        }

    def get_full_intelligence(self):
        matrix = self.get_macro_matrix()
        insight = self.synthesize_macro_insight(matrix)
        vix_price = matrix.get("VIX", {}).get("price", 18.0)
        sessions = self.get_sessions_intelligence(vix_price)
        
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "matrix": matrix,
            "insight": insight,
            "sessions": sessions
        }
