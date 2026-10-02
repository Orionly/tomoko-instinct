from .base_strategy import BaseStrategy


class TrendFollowingV2(BaseStrategy):
    def check_entry(self, h4, h1, score):
        if h4['gate'] == "OPEN" and h4['adx'] > 22 and h1['dist_ema21_atr'] <= 0.5 and score > 70:
            return f"LOOK FOR {h4['direction']} PULLBACK", "H4 intact, pullback OK"
        if h4['gate'] == "OPEN" and h4['adx'] > 22 and score > 70:
            return "WAIT FOR PULLBACK", f"High score but extended {h1['dist_ema21_atr']} ATR"
        return "WAIT", f"Gate {h4['gate']} ADX {h4['adx']}"
