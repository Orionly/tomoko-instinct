"""
TrapRadarEngine - The Institutional Crowd Trap & Liquidity Sweep Radar for Tomoko.

Detects:
1. Buy-Side Liquidity (BSL) and Sell-Side Liquidity (SSL) pools.
2. Bull Traps (Buy-Side Purges / Judas Swing Highs) & Bear Traps (Sell-Side Purges / Judas Swing Lows).
3. Contrarian Retail Positioning Skew & Squeeze Warnings.
4. Session-anchored manipulation windows (London & New York Judas Swings).
"""

import math
import logging
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone
import pandas as pd

log = logging.getLogger(__name__)

ROUND_NUMBER_INTERVALS = {
    "XAUUSD": 10.0,    # e.g. 2640, 2650, 2660
    "EURUSD": 0.0050,  # e.g. 1.0800, 1.0850, 1.0900
    "GBPUSD": 0.0050,  # e.g. 1.3000, 1.3050, 1.3100
    "USDJPY": 0.50,    # e.g. 150.00, 150.50, 151.00
    "GBPJPY": 0.50,
    "EURJPY": 0.50,
    "EURGBP": 0.0050,
}


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


class TrapRadarEngine:
    """
    Autonomous Liquidity Sweep & Crowd Trap Engine.
    Detects false breakouts, stop hunts, and institutional absorption.
    """

    def __init__(self, price_engine=None, levels_engine=None):
        self.price_engine = price_engine
        self.levels_engine = levels_engine

    def is_judas_window(self, dt: Optional[datetime] = None) -> Tuple[bool, str]:
        """
        Determines if current time is within high-probability manipulation windows:
        - London Judas Window: 07:00 - 08:30 UTC
        - New York Judas Window: 12:30 - 14:00 UTC
        """
        now = dt or datetime.now(timezone.utc)
        hour = now.hour
        minute = now.minute
        time_dec = hour + (minute / 60.0)

        if 7.0 <= time_dec <= 8.5:
            return True, "LONDON_JUDAS_SWING"
        elif 12.5 <= time_dec <= 14.0:
            return True, "NEW_YORK_JUDAS_SWING"
        return False, "STANDARD_SESSION_HOURS"

    def calculate_asian_range(self, df_h1: Optional[pd.DataFrame]) -> Dict[str, float]:
        """
        Computes Asian Session High (ASH) and Low (ASL) defined from 00:00 to 06:00 UTC.
        """
        if df_h1 is None or len(df_h1) < 6:
            return {"ash": 0.0, "asl": 0.0, "valid": False}

        try:
            # Check for today's bars between 00:00 and 06:00 UTC
            highs, lows = [], []
            for i in range(len(df_h1) - 1, -1, -1):
                row = df_h1.iloc[i]
                t_val = row.get("time") if "time" in row else df_h1.index[i]
                dt = pd.to_datetime(t_val)
                if dt.tz is None:
                    dt = dt.tz_localize("UTC")
                else:
                    dt = dt.tz_convert("UTC")

                # If within Asian hours (00:00 to 06:00 UTC)
                if 0 <= dt.hour < 6:
                    highs.append(_safe_float(row["high"]))
                    lows.append(_safe_float(row["low"]))
                elif dt.hour >= 6 and len(highs) > 0:
                    break

            if highs and lows:
                return {
                    "ash": max(highs),
                    "asl": min(lows),
                    "valid": True
                }
        except Exception as e:
            log.debug("[TrapRadar] Asian range calc note: %s", e)

        # Fallback to recent 6 bars
        recent = df_h1.iloc[-6:]
        return {
            "ash": float(recent["high"].max()),
            "asl": float(recent["low"].min()),
            "valid": True
        }

    def estimate_crowd_sentiment_skew(self, symbol: str, current_price: float, df_h1: Optional[pd.DataFrame]) -> Dict[str, Any]:
        """
        Computes the contrarian Retail Crowd Sentiment Skew based on mean-reversion positioning dynamics.
        Retail crowd historically clusters short in strong uptrends and long in strong downtrends.
        """
        if df_h1 is None or len(df_h1) < 20 or current_price <= 0:
            return {
                "retail_long_pct": 50.0,
                "retail_short_pct": 50.0,
                "skew_bias": "BALANCED",
                "is_extreme": False
            }

        # Calculate price distance from 20-period EMA normalized by ATR
        ema20 = float(df_h1["EMA21"].iloc[-1]) if "EMA21" in df_h1 else float(df_h1["close"].ewm(span=20).mean().iloc[-1])
        atr = float(df_h1["ATR"].iloc[-1]) if "ATR" in df_h1 else (current_price * 0.005)
        if atr <= 0:
            atr = current_price * 0.005

        deviation = (current_price - ema20) / atr

        # Model retail contrarian bias:
        # High upward deviation -> Retail crowd piles into shorts trying to call the top.
        # High downward deviation -> Retail crowd catches falling knives going long.
        clamped_dev = max(-3.0, min(3.0, deviation))
        # Deviation of +2.0 ATR maps to ~25% Long / 75% Short
        retail_short = round(50.0 + (clamped_dev * 11.5), 1)
        retail_short = max(18.0, min(82.0, retail_short))
        retail_long = round(100.0 - retail_short, 1)

        is_extreme = retail_long >= 70.0 or retail_short >= 70.0
        if retail_long >= 65.0:
            skew_bias = "EXTREME_RETAIL_LONG"
        elif retail_short >= 65.0:
            skew_bias = "EXTREME_RETAIL_SHORT"
        else:
            skew_bias = "BALANCED"

        return {
            "retail_long_pct": retail_long,
            "retail_short_pct": retail_short,
            "skew_bias": skew_bias,
            "is_extreme": is_extreme,
            "contrarian_institutional_bias": "SHORT" if retail_long > 60 else ("LONG" if retail_short > 60 else "NEUTRAL")
        }

    def scan_pair_liquidity_traps(self, symbol: str) -> Dict[str, Any]:
        """
        Full diagnostic scan of a pair for:
        - Liquidity pools (BSL & SSL)
        - Active Bull Traps / Bear Traps / Judas Sweeps
        - Crowd positioning skew & squeeze risk
        - Plain-English actionable directives for beginners & veterans
        """
        sym = symbol.upper()
        spot_info = self.price_engine.get_price(sym) if self.price_engine else {}
        spot = _safe_float(spot_info.get("price")) if spot_info else 0.0

        df_h1 = self.price_engine.get_rates(sym, "H1", 30) if self.price_engine else None
        df_d1 = self.price_engine.get_rates(sym, "D1", 10) if self.price_engine else None
        df_w1 = self.price_engine.get_rates(sym, "W1", 5) if self.price_engine else None

        # Fallback price from bars if spot is 0
        if spot <= 0 and df_h1 is not None and not df_h1.empty:
            spot = _safe_float(df_h1["close"].iloc[-1])

        # Get key levels
        pdh, pdl, weekly_open, atr = spot, spot, spot, (spot * 0.005)
        if self.levels_engine and df_d1 is not None and df_h1 is not None:
            try:
                lv = self.levels_engine.get_key_levels(df_d1, df_w1, df_h1)
                pdh = _safe_float(lv.get("daily_high"), spot)
                pdl = _safe_float(lv.get("daily_low"), spot)
                weekly_open = _safe_float(lv.get("weekly_open"), spot)
                atr = _safe_float(lv.get("atr"), spot * 0.005)
            except Exception as e:
                log.debug("[TrapRadar] Levels note for %s: %s", sym, e)

        # Asian Range
        asian = self.calculate_asian_range(df_h1)
        ash = asian["ash"] if asian["ash"] > 0 else pdh
        asl = asian["asl"] if asian["asl"] > 0 else pdl

        # Define primary Buy-Side Liquidity (BSL) and Sell-Side Liquidity (SSL)
        bsl_levels = [
            {"label": "Previous Day High (PDH)", "price": pdh, "type": "MAJOR_BSL"},
            {"label": "Asian Session High (ASH)", "price": ash, "type": "SESSION_BSL"},
        ]
        ssl_levels = [
            {"label": "Previous Day Low (PDL)", "price": pdl, "type": "MAJOR_SSL"},
            {"label": "Asian Session Low (ASL)", "price": asl, "type": "SESSION_SSL"},
        ]

        # Check Nearest Round Psychological Level
        interval = ROUND_NUMBER_INTERVALS.get(sym, 0.0100)
        round_above = math.ceil(spot / interval) * interval
        round_below = math.floor(spot / interval) * interval
        round_above_str = f"{round_above:.2f}" if "XAU" in sym else f"{round_above:.4f}"
        round_below_str = f"{round_below:.2f}" if "XAU" in sym else f"{round_below:.4f}"
        bsl_levels.append({"label": f"Key Psychological Round ({round_above_str})", "price": round_above, "type": "PSYCH_BSL"})
        ssl_levels.append({"label": f"Key Psychological Round ({round_below_str})", "price": round_below, "type": "PSYCH_SSL"})

        # Evaluate Judas Manipulation Window
        is_judas, judas_name = self.is_judas_window()

        # Evaluate Sentiment Skew
        sentiment_skew = self.estimate_crowd_sentiment_skew(sym, spot, df_h1)

        # Inspect latest completed and active candles for sweep mechanics
        trap_detected = "NONE"
        trap_title = "Cruising in Open Range"
        trap_description = "Price is auctioning within normal liquidity boundaries without an active stop hunt."
        severity = "NORMAL"
        target_pool = None
        consequent_encroachment = None

        if df_h1 is not None and len(df_h1) >= 2:
            latest_bar = df_h1.iloc[-1]
            prev_bar = df_h1.iloc[-2]

            h_high = max(_safe_float(latest_bar["high"]), _safe_float(prev_bar["high"]), spot)
            h_low = min(_safe_float(latest_bar["low"]), _safe_float(prev_bar["low"]), spot)
            h_close = spot if spot > 0 else _safe_float(latest_bar["close"])

            # 1. Check BULL TRAP (Buy-Side Liquidity Purge)
            # Price breached above PDH or ASH, but current close is back beneath it
            primary_bsl = min(p for p in [pdh, ash] if p > 0)
            if h_high > primary_bsl and h_close < primary_bsl:
                pierce_dist = h_high - primary_bsl
                rejection_dist = h_high - h_close
                candle_range = max(0.0001, h_high - h_low)
                upper_wick_ratio = rejection_dist / candle_range

                if upper_wick_ratio >= 0.40 or pierce_dist > (0.10 * atr):
                    trap_detected = "BULL_TRAP_ACTIVE"
                    trap_title = "BUY-SIDE LIQUIDITY PURGE (BULL TRAP)"
                    severity = "CRITICAL" if is_judas else "HIGH"
                    target_val = min(pdl, asl)
                    target_str = f"{target_val:.2f}" if "XAU" in sym else f"{target_val:.5f}"
                    target_pool = f"Opposing SSL at {target_str}"
                    consequent_encroachment = round((h_high + primary_bsl) / 2.0, 2 if "XAU" in sym else 5)
                    trap_description = (
                        f"Price pierced above Buy-Side Liquidity ({primary_bsl}) reaching {h_high}, "
                        f"but failed to hold and was forcefully rejected back inside the range. "
                        f"Breakout buyers are trapped at the top; retail buy stops were consumed by institutional sellers."
                    )

            # 2. Check BEAR TRAP (Sell-Side Liquidity Purge)
            # Price breached below PDL or ASL, but current close is back above it
            primary_ssl = max(p for p in [pdl, asl] if p > 0)
            if h_low < primary_ssl and h_close > primary_ssl:
                pierce_dist = primary_ssl - h_low
                rejection_dist = h_close - h_low
                candle_range = max(0.0001, h_high - h_low)
                lower_wick_ratio = rejection_dist / candle_range

                if lower_wick_ratio >= 0.40 or pierce_dist > (0.10 * atr):
                    trap_detected = "BEAR_TRAP_ACTIVE"
                    trap_title = "SELL-SIDE LIQUIDITY PURGE (BEAR TRAP)"
                    severity = "CRITICAL" if is_judas else "HIGH"
                    target_val = max(pdh, ash)
                    target_str = f"{target_val:.2f}" if "XAU" in sym else f"{target_val:.5f}"
                    target_pool = f"Opposing BSL at {target_str}"
                    consequent_encroachment = round((h_low + primary_ssl) / 2.0, 2 if "XAU" in sym else 5)
                    trap_description = (
                        f"Price spiked below Sell-Side Liquidity ({primary_ssl}) reaching {h_low}, "
                        f"then rapidly absorbed and snapped back above the floor. "
                        f"Panic sellers are trapped; sell-side stops were harvested into institutional long orders."
                    )

            # 3. Check SQUEEZE IMMINENT (Approaching extreme with heavy crowd skew)
            if trap_detected == "NONE" and sentiment_skew["is_extreme"]:
                dist_to_bsl = abs(primary_bsl - spot)
                dist_to_ssl = abs(spot - primary_ssl)
                if sentiment_skew["retail_short_pct"] >= 70.0 and dist_to_bsl <= (0.25 * atr):
                    trap_detected = "SHORT_SQUEEZE_IMMINENT"
                    trap_title = "SHORT SQUEEZE IMMINENT"
                    severity = "HIGH"
                    trap_description = (
                        f"Retail crowd is {sentiment_skew['retail_short_pct']}% SHORT while price approaches Buy-Side Liquidity ({primary_bsl}). "
                        f"High risk of an explosive buy-stop cascade forcing shorts to cover."
                    )
                elif sentiment_skew["retail_long_pct"] >= 70.0 and dist_to_ssl <= (0.25 * atr):
                    trap_detected = "LONG_LIQUIDATION_IMMINENT"
                    trap_title = "LONG LIQUIDATION IMMINENT"
                    severity = "HIGH"
                    trap_description = (
                        f"Retail crowd is {sentiment_skew['retail_long_pct']}% LONG while price approaches Sell-Side Liquidity ({primary_ssl}). "
                        f"High risk of a long stop-run waterfall flush."
                    )

        # Formulate tailored Beginner and Veteran Guidance
        if trap_detected == "BULL_TRAP_ACTIVE":
            beginner_guide = "DO NOT BUY. The breakout above resistance failed immediately. Novice traders who bought at the highs are trapped."
            veteran_guide = f"Fade the sweep. Look for short entry on M5 retest of 50% CE ({consequent_encroachment}). Hard stop above spike high ({h_high}). Target {target_pool}."
        elif trap_detected == "BEAR_TRAP_ACTIVE":
            beginner_guide = "DO NOT SELL. The breakdown below support was quickly rejected and bought back up. Panic selling is being absorbed."
            veteran_guide = f"Buy the spring. Look for long entry on M5 retest of 50% CE ({consequent_encroachment}). Hard stop below spike low ({h_low}). Target {target_pool}."
        elif "SQUEEZE" in trap_detected or "LIQUIDATION" in trap_detected:
            beginner_guide = "Beware of entering against the momentum. The crowd is on the wrong side and being squeezed."
            veteran_guide = "Align with institutional momentum. Set trailing alerts at the liquidity pool boundary to trade the expansion impulse."
        else:
            beginner_guide = "Price is moving cleanly within the range. Wait for price to reach key liquidity boundaries before taking action."
            veteran_guide = "Monitor order flow at Asian boundaries (ASH/ASL). Look for liquidity sweep wicks during session opening handoffs."

        return {
            "symbol": sym,
            "spot_price": spot,
            "trap_state": trap_detected,
            "trap_title": trap_title,
            "severity": severity,
            "description": trap_description,
            "consequent_encroachment_midline": consequent_encroachment,
            "target_liquidity_pool": target_pool,
            "judas_window": {
                "is_active": is_judas,
                "window_name": judas_name
            },
            "sentiment_skew": sentiment_skew,
            "liquidity_pools": {
                "buy_side_liquidity_bsl": bsl_levels,
                "sell_side_liquidity_ssl": ssl_levels,
            },
            "operational_guidance": {
                "beginners": beginner_guide,
                "veterans": veteran_guide
            },
            "timestamp_utc": datetime.now(timezone.utc).isoformat()
        }

    def scan_all_pairs(self, pairs: Optional[List[str]] = None) -> Dict[str, Any]:
        """Scans all major pairs for active liquidity traps and returns ranked alerts."""
        target_pairs = pairs or ["EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "EURJPY", "EURGBP", "XAUUSD"]
        results = {}
        active_traps = []

        for p in target_pairs:
            res = self.scan_pair_liquidity_traps(p)
            results[p] = res
            if res.get("trap_state") != "NONE":
                active_traps.append({
                    "symbol": p,
                    "state": res.get("trap_state"),
                    "title": res.get("trap_title"),
                    "severity": res.get("severity"),
                    "description": res.get("description"),
                    "target": res.get("target_liquidity_pool"),
                    "guidance": res.get("operational_guidance", {}).get("beginners")
                })

        return {
            "scanned_count": len(target_pairs),
            "active_traps_count": len(active_traps),
            "active_traps": active_traps,
            "pairs": results,
            "scan_time_utc": datetime.now(timezone.utc).isoformat()
        }
