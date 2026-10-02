# RiskEngine - SL/TP and lot sizing
class RiskEngine:
    def calc_sl_tp(self, symbol, entry, direction, atr):
        is_gold = "XAU" in symbol
        sl_mult = 1.8 if is_gold else 1.2
        tp_mult = 3.6 if is_gold else 2.4
        sl = entry - sl_mult * atr if direction == "BUY" else entry + sl_mult * atr
        tp = entry + tp_mult * atr if direction == "BUY" else entry - tp_mult * atr
        return {"sl": sl, "tp": tp, "lot": 0.01}
