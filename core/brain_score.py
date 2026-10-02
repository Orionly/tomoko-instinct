
# Brain Score 0-100 = Information Quality, not winrate
import math


def _safe_score(v):
    """Convert to float, replacing NaN/Inf with 0."""
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return 0.0
        return f
    except (TypeError, ValueError):
        return 0.0


def calculate_brain_score(structural, volatility, levels, intermarket, news):
    s = _safe_score(structural)
    v = _safe_score(volatility)
    l = _safe_score(levels)
    i = _safe_score(intermarket)
    n = _safe_score(news)
    score = s * 0.30 + v * 0.20 + l * 0.20 + i * 0.15 + n * 0.15
    if math.isnan(score) or math.isinf(score):
        score = 0.0
    return int(max(0, min(100, score)))

def get_manual_action(score, gate, pullback, news_clean, risk_sentiment, symbol="", dist_atr=0, adx=0, liq_high=0, liq_low=0, price=0, levels=None):
    is_gold = "XAU" in symbol or "GOLD" in symbol

    if not news_clean:
        return "STAY OUT - NEWS", "High impact news in <60min. Information polluted." + (" Gold spikes hard on CPI/FOMC." if is_gold else "")

    if gate and isinstance(gate, dict) and gate.get('gate') == "OPEN" and adx > 40 and abs(dist_atr) > 1.2 and score > 70:
        if gate.get('direction') == "UP":
            return f"LIMIT AT LIQ HIGH {liq_high:.2f}", f"[LIQ RULE] H4 OPEN UP ADX {adx}>40 Dist {dist_atr} ATR >1.2 => LIMIT {liq_high:.2f}"
        else:
            return f"LIMIT AT LIQ LOW {liq_low:.2f}", f"[LIQ RULE] H4 OPEN DOWN ADX {adx}>40 Dist {dist_atr} ATR >1.2 => LIMIT {liq_low:.2f}"

    if score < 40:
        return "WAIT", f"Low info quality ({score}). H4 {gate} - no edge." + (" Gold choppy, wait for DXY direction." if is_gold else "")
    if score < 70:
        if pullback:
            return "LOOK - EARLY", f"Pullback forming but score {score}. Prepare, don't chase." + (" Check US10Y real yields for Gold." if is_gold else "")
        return "WAIT", f"Medium score {score}. Need pullback to EMA21."
    # High score
    if pullback:
        direction = gate.get('direction','UP') if isinstance(gate, dict) else gate
        extra = ""
        if is_gold:
            extra = " DXY down + US10Y down = Gold long favored. RISK-OFF = Gold bid." if direction=="UP" else " DXY up + yields up = Gold short pressure."
        return f"LOOK FOR {'BUY' if direction=='UP' else 'SELL'} PULLBACK", f"H4 {direction} intact, price in 0.5 ATR of EMA21, {risk_sentiment} supports. Manual entry window.{extra}"
    return "WAIT FOR PULLBACK", f"High score {score} but extended. Wait for 0.5 ATR retrace." + (" Gold often wicks 3-5 USD, be patient." if is_gold else "")

# XAU specific ATR handling - sourced from config.XAU_CONFIG
from config import XAU_CONFIG
