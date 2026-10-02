# RegimeEngine - Multi-timeframe state detection V2
import math


def _safe_divide(a, b, default=0.0):
    """Divide a by b, returning default if b is 0, NaN, or None."""
    try:
        if b is None:
            return default
        fb = float(b)
        if fb == 0.0 or math.isnan(fb) or math.isinf(fb):
            return default
        result = float(a) / fb
        if math.isnan(result) or math.isinf(result):
            return default
        return result
    except (TypeError, ValueError, ZeroDivisionError):
        return default


class RegimeEngine:
    def __init__(self, config=None):
        self.config = config

    def evaluate_h4_gate(self, df_h4):
        last = df_h4.iloc[-1]
        ema20, ema50, adx, atr = last['EMA20'], last['EMA50'], last['ADX'], last['ATR']
        atr_avg20 = df_h4['ATR'].rolling(20).mean().iloc[-1]
        atr_ratio = _safe_divide(atr, atr_avg20)
        trend_up = ema20 > ema50
        trend_down = ema20 < ema50
        gate_open = adx > 22 and atr_ratio > 0.8
        dist_ema21_atr = _safe_divide(abs(last['close'] - last['EMA21']), atr)
        return {
            "gate": "OPEN" if gate_open else "INSIDE",
            "direction": "UP" if trend_up else "DOWN" if trend_down else "FLAT",
            "adx": round(adx, 1) if not (math.isnan(adx) or math.isinf(adx)) else 0.0,
            "atr_ratio": round(atr_ratio, 2),
            "dist_ema21_atr": round(dist_ema21_atr, 2),
            "ema20": ema20 if not (math.isnan(ema20) or math.isinf(ema20)) else 0.0,
            "ema50": ema50 if not (math.isnan(ema50) or math.isinf(ema50)) else 0.0,
            "ema21": last['EMA21'] if not (math.isnan(last['EMA21']) or math.isinf(last['EMA21'])) else 0.0,
            "atr": atr if not (math.isnan(atr) or math.isinf(atr)) else 0.0,
            "close": last['close'] if not (math.isnan(last['close']) or math.isinf(last['close'])) else 0.0,
        }

    def evaluate_h1_pullback(self, df_h1):
        last = df_h1.iloc[-1]
        dist = _safe_divide(abs(last['close'] - last['EMA21']), last['ATR'], default=99.0)
        return {
            "dist_ema21_atr": round(dist, 2),
            "is_pullback": bool(dist <= 0.5),
            "rsi": round(last['RSI'], 1) if not (math.isnan(last['RSI']) or math.isinf(last['RSI'])) else 50.0,
            "close_vs_ema": "ABOVE" if last['close'] > last['EMA21'] else "BELOW",
            "close": last['close'] if not (math.isnan(last['close']) or math.isinf(last['close'])) else 0.0,
        }

    def evaluate_mtf_trend(self, df_h4, df_h1, df_m15):
        h4 = self.evaluate_h4_gate(df_h4)
        h1 = self.evaluate_h1_pullback(df_h1)
        # M15 bias
        last_m15 = df_m15.iloc[-1] if len(df_m15) > 0 else df_h1.iloc[-1]
        m15_bull = last_m15['close'] > last_m15['EMA21']
        m15_bias = "Bullish" if m15_bull else "Bearish"
        m15_dist = _safe_divide(abs(last_m15['close'] - last_m15['EMA21']), last_m15['ATR'], default=99.0)
        if m15_dist < 0.3:
            m15_bias = "Neutral"
        h4_bias = "Bullish" if h4['direction'] == "UP" and h4['gate'] == "OPEN" else "Bearish" if h4['direction'] == "DOWN" and h4['gate'] == "OPEN" else "Neutral"
        h1_bias = "Bullish" if h1['close_vs_ema'] == "ABOVE" and h1['rsi'] > 50 else "Bearish" if h1['close_vs_ema'] == "BELOW" and h1['rsi'] < 50 else "Neutral"
        return {
            "H4": h4,
            "H1": h1,
            "M15_bias": m15_bias,
            "H4_bias": h4_bias,
            "H1_bias": h1_bias,
            "aligned": h4_bias == h1_bias == m15_bias and h4_bias != "Neutral",
        }

    def evaluate_m15_structure(self, df_m15):
        if len(df_m15) < 2:
            return {"bias": "Neutral", "dist_ema21_atr": 0, "is_pullback": False}
        last = df_m15.iloc[-1]
        dist = _safe_divide(abs(last['close'] - last['EMA21']), last['ATR'], default=99.0)
        bias = "Bullish" if last['close'] > last['EMA21'] else "Bearish"
        if dist < 0.15:
            bias = "Neutral"
        return {"bias": bias, "dist_ema21_atr": round(dist, 2), "is_pullback": bool(dist <= 0.5), "rsi": round(last['RSI'], 1) if not (math.isnan(last['RSI']) or math.isinf(last['RSI'])) else 50.0, "close": last['close'] if not (math.isnan(last['close']) or math.isinf(last['close'])) else 0.0, "ema21": last['EMA21'] if not (math.isnan(last['EMA21']) or math.isinf(last['EMA21'])) else 0.0}
