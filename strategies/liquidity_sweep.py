from .base_strategy import BaseStrategy


class LiquiditySweep(BaseStrategy):
    def check_entry(self, h4, h1, levels, score):
        adx = h4['adx']
        dist = h1['dist_ema21_atr']
        liq_high = levels['liq_high']
        liq_low = levels['liq_low']
        if h4['gate'] == "OPEN" and h4['direction'] == "UP" and adx > 40 and dist > 1.2 and score > 70:
            return f"LIMIT AT LIQ HIGH {liq_high:.2f}", f"H4 OPEN UP ADX {adx}>40 Dist {dist}>1.2 => LIMIT at Liq High {liq_high:.2f} instead of 0.5 ATR pullback. Your example 4647.41"
        if h4['gate'] == "OPEN" and h4['direction'] == "DOWN" and adx > 40 and dist > 1.2 and score > 70:
            return f"LIMIT AT LIQ LOW {liq_low:.2f}", f"H4 OPEN DOWN ADX {adx}>40 Dist {dist}>1.2 => LIMIT {liq_low:.2f}"
        return None
