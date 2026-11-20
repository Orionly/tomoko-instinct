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
    "DXY": {"price": 103.20, "change_pct": 0.0, "change_abs": 0.0, "change_5d_pct": 0.0, "change_20d_pct": 0.0, "trend": "FLAT", "macro_trend": "FLAT"},
    "US10Y": {"price": 4.30, "change_pct": 0.0, "change_abs": 0.0, "change_bps": 0.0, "change_5d_pct": 0.0, "change_20d_pct": 0.0, "trend": "FLAT", "macro_trend": "FLAT"},
    "TIPS_ETF": {"price": 104.50, "change_pct": 0.0, "change_abs": 0.0, "change_5d_pct": 0.0, "change_20d_pct": 0.0, "trend": "FLAT", "macro_trend": "FLAT"},
    "OIL": {"price": 78.00, "change_pct": 0.0, "change_abs": 0.0, "change_5d_pct": 0.0, "change_20d_pct": 0.0, "trend": "FLAT", "macro_trend": "FLAT"},
    "SPX": {"price": 5600.0, "change_pct": 0.0, "change_abs": 0.0, "change_5d_pct": 0.0, "change_20d_pct": 0.0, "trend": "FLAT", "macro_trend": "FLAT"},
    "VIX": {"price": 18.0, "change_pct": 0.0, "change_abs": 0.0, "change_5d_pct": 0.0, "change_20d_pct": 0.0, "trend": "FLAT", "macro_trend": "FLAT"},
    "GOLD_FUT": {"price": 2650.0, "change_pct": 0.0, "change_abs": 0.0, "change_5d_pct": 0.0, "change_20d_pct": 0.0, "trend": "FLAT", "macro_trend": "FLAT"},
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
            "GOLD_FUT": "GC=F",
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
            df = t.history(period="1mo")
            if len(df) >= 2:
                closes = df["Close"].dropna()
                if len(closes) >= 2:
                    last = _safe_float(closes.iloc[-1])
                    prev = _safe_float(closes.iloc[-2])
                    chg = _safe_pct_change(last, prev)
                    chg_abs = round(last - prev, 4)

                    # Multi-day macro trend (5D and ~20D / 1-month window)
                    idx_5d = max(0, len(closes) - 6)
                    base_5d = _safe_float(closes.iloc[idx_5d], prev)
                    chg_5d = _safe_pct_change(last, base_5d)

                    base_20d = _safe_float(closes.iloc[0], prev)
                    chg_20d = _safe_pct_change(last, base_20d)

                    high_20d = round(_safe_float(df["High"].dropna().max(), last), 4) if "High" in df else round(float(closes.max()), 4)
                    low_20d = round(_safe_float(df["Low"].dropna().min(), last), 4) if "Low" in df else round(float(closes.min()), 4)

                    # Composite multi-day macro trend (avoids 1-day pullback noise masking a multi-week trend)
                    if chg_5d >= 0.75 and chg_20d >= 1.25:
                        macro_trend = "STRONG_UP"
                    elif chg_5d <= -0.75 and chg_20d <= -1.25:
                        macro_trend = "STRONG_DOWN"
                    elif chg_5d >= 0.35 or chg_20d >= 1.0:
                        macro_trend = "UP"
                    elif chg_5d <= -0.35 or chg_20d <= -1.0:
                        macro_trend = "DOWN"
                    else:
                        macro_trend = "FLAT"

                    res = {
                        "price": round(last, 4),
                        "prev_close": round(prev, 4),
                        "change_abs": chg_abs,
                        "change_pct": chg,
                        "change_5d_pct": chg_5d,
                        "change_20d_pct": chg_20d,
                        "high_20d": high_20d,
                        "low_20d": low_20d,
                        "trend": "UP" if chg > 0 else "DOWN" if chg < 0 else "FLAT",
                        "macro_trend": macro_trend,
                        "ts": now,
                    }
                    if ticker == "^TNX":
                        # ^TNX quotes in percentage points (e.g., 4.98 = 4.98%), so 0.01 = 1 bp
                        res["change_bps"] = round((last - prev) * 100.0, 1)
                        res["change_5d_bps"] = round((last - base_5d) * 100.0, 1)
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
        Includes full calendar day-of-week awareness for global FX market closures.
        """
        now = datetime.now(timezone.utc)
        weekday = now.weekday()  # 0=Monday, 4=Friday, 5=Saturday, 6=Sunday
        cur_min = now.hour * 60 + now.minute

        # Global FX institutional trading week:
        # Closes Friday at 21:00 UTC (17:00 EST)
        # Re-opens Sunday at 21:00 UTC (17:00 EST / Sydney Monday open)
        is_weekend = (weekday == 5) or (weekday == 4 and cur_min >= 1260) or (weekday == 6 and cur_min < 1260)

        if is_weekend:
            # Calculate remaining minutes until Sunday 21:00 UTC
            if weekday == 4:  # Friday post-21:00
                mins_left = (1440 - cur_min) + 1440 + 1260
            elif weekday == 5:  # Saturday
                mins_left = (1440 - cur_min) + 1260
            else:  # Sunday pre-21:00
                mins_left = max(0, 1260 - cur_min)

            h = mins_left // 60
            m = mins_left % 60
            time_str = f"{h}h {m}m" if m > 0 else f"{h}h"
            reopen_note = f"Opens Sunday 21:00 UTC (in {time_str})"

            return {
                "macro_liquidity": f"WEEKEND MARKET CLOSE // OPENS SUN 21:00 UTC ({time_str})",
                "overlap_active": False,
                "is_weekend": True,
                "tokyo": {"state": "CLOSED (WEEKEND)", "volatility": "OFFLINE", "note": reopen_note},
                "london": {"state": "CLOSED (WEEKEND)", "volatility": "OFFLINE", "note": reopen_note},
                "new_york": {"state": "CLOSED (WEEKEND)", "volatility": "OFFLINE", "note": reopen_note},
            }

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
            "is_weekend": False,
            "tokyo": {"state": tokyo_state, "volatility": tokyo_vol, "note": tokyo_note},
            "london": {"state": london_state, "volatility": london_vol, "note": london_note},
            "new_york": {"state": ny_state, "volatility": ny_vol, "note": ny_note},
        }

    def synthesize_macro_insight(self, matrix):
        """
        Synthesizes macro metrics into deterministic, professional decision-support context.
        Directly connects detected market regime (including multi-day trend & liquidity-crunch crash mode)
        to actionable human trading guidance.
        """
        dxy = matrix.get("DXY", {})
        spx = matrix.get("SPX", {})
        vix = matrix.get("VIX", {})
        us10y = matrix.get("US10Y", {})
        tips = matrix.get("TIPS_HYBRID", {})
        oil = matrix.get("OIL", {})
        gold_fut = matrix.get("GOLD_FUT", {})

        vix_p = vix.get("price", 18.0)
        vix_chg = vix.get("change_pct", 0.0)
        spx_chg = spx.get("change_pct", 0.0)
        spx_5d = spx.get("change_5d_pct", 0.0)
        dxy_p = dxy.get("price", 103.0)
        dxy_chg = dxy.get("change_pct", 0.0)
        dxy_5d = dxy.get("change_5d_pct", 0.0)
        dxy_20d = dxy.get("change_20d_pct", 0.0)
        dxy_macro_trend = dxy.get("macro_trend", "FLAT")
        us10y_p = us10y.get("price", 4.30)
        us10y_chg = us10y.get("change_pct", 0.0)
        us10y_bps = us10y.get("change_bps", round(us10y_chg * 0.045 * 100, 1))
        yield_press = tips.get("yield_pressure", "STEADY")
        real_yield = tips.get("real_yield_pct", 2.88)
        oil_chg = oil.get("change_pct", 0.0)
        gold_5d = gold_fut.get("change_5d_pct", 0.0)
        gold_20d = gold_fut.get("change_20d_pct", 0.0)

        # Detect whether Gold is liquidating alongside risk assets (hawkish-rates / dollar-squeeze crash)
        dxy_multi_firm = dxy_macro_trend in ("UP", "STRONG_UP") or dxy_5d >= 0.45 or dxy_20d >= 1.2
        gold_liquidating = (gold_5d <= -2.0) or (gold_20d <= -4.5)
        high_vol_stress = (vix_p > 22.0) or (vix_chg > 4.0 and spx_chg < -0.5)

        # 1. Determine Broad Market Regime
        if high_vol_stress and (gold_liquidating or (dxy_multi_firm and us10y_p >= 4.50)):
            regime = "LIQUIDITY CRUNCH // DOLLAR SQUEEZE (CRASH MODE)"
            regime_class = "bear"
            color = "#FF4D4D"
        elif high_vol_stress:
            regime = "RISK-OFF // SAFE-HAVEN FLIGHT"
            regime_class = "bear"
            color = "#FF4D4D"
        elif dxy_multi_firm and (us10y_p >= 4.50 or us10y_chg > 0.5 or dxy_chg > 0.35):
            regime = "DOLLAR DOMINANCE // RATES PRESSURE"
            regime_class = "neut"
            color = "#FFB800"
        elif vix_p < 16.5 and spx_chg >= 0 and dxy_chg <= 0.15 and not dxy_multi_firm:
            regime = "RISK-ON // LIQUIDITY EXPANSION"
            regime_class = "bull"
            color = "#00FF88"
        elif oil_chg > 2.0 and us10y_chg > 0.3:
            regime = "INFLATION SQUEEZE // ENERGY WATCH"
            regime_class = "neut"
            color = "#FFB800"
        else:
            regime = "BALANCED // CONSOLIDATION REGIME"
            regime_class = "neut"
            color = "#00E5FF"

        # 2. Intermarket Cross Impacts (multi-day aware)
        if gold_liquidating:
            gold_impact = f"Severe liquidation pressure on Gold (5D: {gold_5d:+.2f}%, 20D: {gold_20d:+.2f}%) under strong USD ({dxy_p:.2f}) & high yields ({us10y_p:.2f}%)."
        elif yield_press == "RISING" or dxy_multi_firm:
            gold_impact = f"Macro headwinds for Gold (XAUUSD) as DXY holds {dxy_macro_trend.replace('_', ' ')} ({dxy_5d:+.2f}% 5D) and real yields sit at {real_yield:.2f}%."
        elif yield_press == "FALLING":
            gold_impact = "Tailwinds for Gold (XAUUSD) as real yield costs soften."
        else:
            gold_impact = "Real yields neutral; Gold following structural levels."

        if dxy_multi_firm:
            usd_impact = f"Multi-day USD uptrend intact (DXY {dxy_p:.2f}, 5D {dxy_5d:+.2f}%, 20D {dxy_20d:+.2f}%)."
        elif yield_press == "RISING":
            usd_impact = "Supports USD upside on widening real yield spreads."
        elif yield_press == "FALLING":
            usd_impact = "Softens aggressive USD bids; favors non-dollar liquidity."
        else:
            usd_impact = "USD driven by equity correlations and session flow."

        if oil_chg > 1.5:
            oil_impact = f"WTI surge ({oil_chg:+.2f}%) fuels inflation expectations."
        elif oil_chg < -1.5:
            oil_impact = f"WTI cooling ({oil_chg:+.2f}%) eases headline commodity pressures."
        else:
            oil_impact = "Crude oil stable."

        # 3. Dynamic Regime-Driven Narrative
        now_dt = datetime.now(timezone.utc)
        wday = now_dt.weekday()
        c_min = now_dt.hour * 60 + now_dt.minute
        is_weekend_close = (wday == 5) or (wday == 4 and c_min >= 1260) or (wday == 6 and c_min < 1260)

        vix_status = "ELEVATED" if (vix_p >= 20.0 or vix_chg > 4.0) else "LOW" if vix_p < 15.0 else "NORMAL"

        if "LIQUIDITY CRUNCH" in regime:
            playbook = "CRASH/LIQUIDATION MODE: Respect USD strength & Gold breakdown. Sell relief rallies into supply; avoid blind hero longs and use reduced position sizing."
            asset_biases = [
                {"asset": "USD", "bias": "BULL", "reason": f"Dollar squeeze (DXY {dxy_p:.2f}, 5D {dxy_5d:+.2f}%) & high rates ({us10y_p:.2f}%)"},
                {"asset": "XAUUSD", "bias": "BEAR", "reason": f"Liquidation & rates headwind (5D {gold_5d:+.2f}%) — do NOT buy dips blindly"},
                {"asset": "EUR / GBP", "bias": "BEAR", "reason": "High-beta FX pressured by VIX stress & USD strength"},
                {"asset": "JPY", "bias": "NEUT", "reason": "Tug-of-war between VIX flight-to-safety and high US yields"},
            ]
            driver_bullets = [
                {"label": "VIX Volatility", "val": f"{vix_p:.1f} ({vix_chg:+.1f}%)", "tone": "stress"},
                {"label": "Dollar Squeeze", "val": f"DXY {dxy_p:.2f} (5D {dxy_5d:+.2f}%)", "tone": "stress"},
                {"label": "Gold 5D Flow", "val": f"{gold_5d:+.2f}% · US10Y {us10y_p:.2f}% ({us10y_bps:+.0f}bp)", "tone": "stress"},
            ]
            regime_thesis = (
                f"Hawkish liquidity-crunch / dollar-squeeze regime active under elevated VIX ({vix_p:.1f}), high US 10Y yields ({us10y_p:.2f}%), "
                f"and sustained DXY strength ({dxy_p:.2f}, 5D {dxy_5d:+.2f}%). Gold is liquidating (5D {gold_5d:+.2f}%) rather than catching a clean safe-haven bid. "
                f"Equities under stress (S&P 500 5D {spx_5d:+.2f}%); favor defensive sizing and selling failed rallies into overhead supply."
            )
        elif "RISK-OFF" in regime:
            playbook = "Buy Gold pullbacks; avoid chasing breakout momentum on high-beta EUR & GBP."
            asset_biases = [
                {"asset": "XAUUSD", "bias": "BULL", "reason": "Safe-haven resilience & yield softening"},
                {"asset": "JPY", "bias": "BULL", "reason": "Defensive flight-to-safety flows"},
                {"asset": "EUR / GBP", "bias": "BEAR", "reason": "High-beta equities under rotation"},
                {"asset": "USD", "bias": "NEUT", "reason": "Softening real yields cap dollar upside"},
            ]
            driver_bullets = [
                {"label": "VIX Volatility", "val": f"{vix_p:.1f} ({vix_chg:+.1f}%)", "tone": "stress"},
                {"label": "10Y Real Yield", "val": f"{real_yield:.2f}% ({yield_press.lower()})", "tone": "good" if yield_press == "FALLING" else "warn"},
                {"label": "Equity Tone", "val": f"S&P 500 {spx_chg:+.2f}%", "tone": "stress"},
            ]
            regime_thesis = (
                f"Defensive flight-to-safety active under elevated VIX stress ({vix_p:.1f}, {vix_chg:+.1f}%). "
                "Favors safe-haven resilience in Gold (XAUUSD) and JPY crosses while pressuring high-beta currencies (EUR, GBP). "
                f"Equities in defensive rotation ({spx_chg:+.2f}%); avoid chasing aggressive intraday breakout momentum."
            )
        elif "RISK-ON" in regime:
            playbook = "Favor equity & foreign currency dip buys (EUR, GBP); fade safe-haven spikes."
            asset_biases = [
                {"asset": "EURUSD", "bias": "BULL", "reason": "Risk appetite & non-dollar relief"},
                {"asset": "GBPUSD", "bias": "BULL", "reason": "Broad market liquidity expansion"},
                {"asset": "XAUUSD", "bias": "NEUT", "reason": "Driven by flow rather than crisis bid"},
                {"asset": "USD", "bias": "BEAR", "reason": "Capital rotating into risk-on assets"},
            ]
            driver_bullets = [
                {"label": "VIX Volatility", "val": f"{vix_p:.1f} ({vix_chg:+.1f}%)", "tone": "good"},
                {"label": "10Y Real Yield", "val": f"{real_yield:.2f}% ({yield_press.lower()})", "tone": "neutral"},
                {"label": "Equity Tone", "val": f"S&P 500 {spx_chg:+.2f}%", "tone": "good"},
            ]
            regime_thesis = (
                f"Broad liquidity expansion active with subdued market volatility (VIX {vix_p:.1f}). "
                "Favorable macro backdrop supports equity flows and foreign currency relief (EURUSD, GBPUSD). "
                "Aggressive USD bids softened; Gold driven by liquidity flows rather than crisis hedging."
            )
        elif "DOLLAR DOMINANCE" in regime:
            playbook = "Sell EURUSD & GBPUSD rallies; favor USDJPY longs on shallow pullbacks."
            asset_biases = [
                {"asset": "USD", "bias": "BULL", "reason": "Aggressive rates & DXY momentum dominating"},
                {"asset": "USDJPY", "bias": "BULL", "reason": "Widening bond yield differentials"},
                {"asset": "EURUSD", "bias": "BEAR", "reason": "Persistent dollar strength headwinds"},
                {"asset": "XAUUSD", "bias": "BEAR", "reason": "Rising real yield opportunity cost"},
            ]
            driver_bullets = [
                {"label": "Dollar Index", "val": f"DXY {dxy_chg:+.2f}%", "tone": "stress"},
                {"label": "US 10Y Yield", "val": f"{us10y.get('price', 0):.2f}% ({us10y_chg:+.2f}%)", "tone": "stress"},
                {"label": "10Y Real Yield", "val": f"{real_yield:.2f}% (rising)", "tone": "stress"},
            ]
            regime_thesis = (
                f"Aggressive US rates pressure (US10Y {us10y_chg:+.2f}%) and USD bid (DXY {dxy_chg:+.2f}%) dominating price action. "
                "Strong macro headwinds for EURUSD, GBPUSD, and Gold upside. USDJPY supported by widening yield differentials."
            )
        elif "INFLATION SQUEEZE" in regime:
            playbook = "Prioritize Gold (XAUUSD) hedges; exercise caution on energy-dependent EUR & JPY."
            asset_biases = [
                {"asset": "XAUUSD", "bias": "BULL", "reason": "Tangible hedge against headline CPI friction"},
                {"asset": "EURUSD", "bias": "BEAR", "reason": "Energy terms-of-trade degradation"},
                {"asset": "USDJPY", "bias": "BULL", "reason": "Energy import cost pressure on Yen"},
                {"asset": "WTI OIL", "bias": "BULL", "reason": "Crude momentum fueling cost spikes"},
            ]
            driver_bullets = [
                {"label": "WTI Crude Oil", "val": f"${oil.get('price', 0):.2f} ({oil_chg:+.2f}%)", "tone": "stress"},
                {"label": "Inflation Outlook", "val": "Stagflationary friction", "tone": "warn"},
                {"label": "Gold Hedge", "val": "Supported by tangible demand", "tone": "good"},
            ]
            regime_thesis = (
                f"Energy supply shock (WTI {oil_chg:+.2f}%) fueling bond yield spikes and stagflation friction. "
                "Pressures energy-dependent currencies (EUR, JPY); supports tangible commodity hedges (XAUUSD)."
            )
        else:  # BALANCED / CONSOLIDATION
            playbook = "Focus on session liquidity extremes and key structure; avoid chasing macro breakouts."
            asset_biases = [
                {"asset": "XAUUSD", "bias": "NEUT", "reason": "Consolidating within intraday ATR bounds"},
                {"asset": "EURUSD", "bias": "NEUT", "reason": "Respecting technical range extremes"},
                {"asset": "GBPUSD", "bias": "NEUT", "reason": "Balanced order flow ahead of catalysts"},
                {"asset": "USDJPY", "bias": "NEUT", "reason": "Yields holding steady channel"},
            ]
            driver_bullets = [
                {"label": "Market Volatility", "val": f"VIX {vix_p:.1f} (normal)", "tone": "neutral"},
                {"label": "10Y Real Yield", "val": f"{real_yield:.2f}% (steady)", "tone": "neutral"},
                {"label": "Trend Conviction", "val": "Consolidation mode", "tone": "neutral"},
            ]
            regime_thesis = (
                "Intermarket forces in neutral consolidation. Yields and risk sentiment lack directional conviction. "
                "Prioritize technical market structure and key session liquidity levels over aggressive macro momentum."
            )

        driver_context = f"{gold_impact} {usd_impact} {oil_impact}"
        decision_context = f"{regime_thesis} {driver_context}".strip()

        prefix = "[WEEKEND BENCHMARK] " if is_weekend_close else ""
        headline = f"{prefix}10Y Real Yield at {real_yield:.2f}% ({yield_press.lower()} intraday). VIX at {vix_p:.1f} ({vix_chg:+.1f}%)."

        return {
            "regime": regime,
            "regime_class": regime_class,
            "color": color,
            "headline": headline,
            "decision_context": decision_context,
            "playbook": playbook,
            "asset_biases": asset_biases,
            "driver_bullets": driver_bullets,
            "gold_impact": gold_impact,
            "usd_impact": usd_impact,
            "oil_impact": oil_impact,
            "yield_pressure": yield_press,
            "vix_regime": vix_status,
            "is_weekend": is_weekend_close
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
        gold_fut = matrix.get("GOLD_FUT", {})
        regime = insight.get("regime", "")
        yield_press = tips.get("yield_pressure", "STEADY")
        dxy_macro_trend = dxy.get("macro_trend", "FLAT")
        dxy_5d = dxy.get("change_5d_pct", 0.0)
        gold_5d = gold_fut.get("change_5d_pct", 0.0)

        sym = symbol.upper()

        if "XAU" in sym or "GOLD" in sym:
            # Gold strongly driven by Real Yields, multi-day USD trend & liquidation regime
            if "LIQUIDITY CRUNCH" in regime or gold_5d <= -2.0 or (dxy_macro_trend in ("UP", "STRONG_UP") and us10y.get("price", 0) >= 4.5):
                status = "BEARISH HEADWIND"
                cls = "bear"
                rationale = f"Liquidation / strong-USD headwind (DXY {dxy_macro_trend.replace('_',' ')} 5D {dxy_5d:+.2f}%, Gold 5D {gold_5d:+.2f}%, US10Y {us10y.get('price', 0):.2f}%)."
            elif yield_press == "FALLING" and dxy.get("change_pct", 0) <= 0.1 and dxy_macro_trend not in ("UP", "STRONG_UP"):
                status = "FAVORABLE LONGS"
                cls = "bull"
                rationale = "Softening real yields (TIPS pressure FALLING) + soft DXY provides macro tailwind for Gold upside."
            elif yield_press == "RISING" and (dxy.get("change_pct", 0) > 0 or dxy_macro_trend in ("UP", "STRONG_UP")):
                status = "BEARISH HEADWIND"
                cls = "bear"
                rationale = "Rising real yields + stronger USD create strong macro friction against long positioning."
            else:
                status = "MIXED MACRO"
                cls = "neut"
                rationale = f"Real yield pressure {yield_press}; DXY 5D {dxy_5d:+.2f}%; prioritize technical liquidity levels."

        elif sym in ("EURUSD", "GBPUSD"):
            dxy_chg = dxy.get("change_pct", 0)
            if dxy_chg < -0.15 and dxy_macro_trend not in ("UP", "STRONG_UP") and "RISK-ON" in regime:
                status = "FAVORABLE LONGS"
                cls = "bull"
                rationale = "DXY downward momentum coupled with Risk-On sentiment supports pro-cyclical currency appreciation."
            elif dxy_chg > 0.25 or dxy_macro_trend in ("UP", "STRONG_UP") or "RISK-OFF" in regime or "LIQUIDITY CRUNCH" in regime:
                status = "BEARISH HEADWIND"
                cls = "bear"
                rationale = f"Sustained DXY strength (5D {dxy_5d:+.2f}%) and defensive risk tone pose headwinds for {sym} longs."
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

    def get_snapshot(self):
        """Backwards-compatible alias returning the live macro pillar matrix."""
        return self.get_macro_matrix()

    def get_tips_real_yield(self):
        """Backwards-compatible alias returning the hybrid 10Y TIPS real yield telemetry."""
        return self.get_macro_matrix().get("TIPS_HYBRID", {})

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
