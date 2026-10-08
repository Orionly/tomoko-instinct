
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


# --- Dynamic component scores ------------------------------------------------ #
# These replace the old hardcoded 70/75 placeholders so the number actually
# reacts to the market. Each returns (score, tooltip) where the tooltip is the
# human explanation shown on hover in the pair deep-dive.

# Volatility: band the H4 ATR ratio, then adjust for how extended price is.
VOL_BANDS = (
    (0.6, 20, "dead (<0.6)"),
    (0.8, 50, "thin (0.6-0.8)"),
    (1.2, 80, "healthy (0.8-1.2)"),
)
VOL_OVEREXTENDED = 70          # ATR ratio > 1.2
VOL_EXTENDED_PENALTY = -10     # dist > 1.2 ATR
VOL_PULLBACK_BONUS = 10        # dist < 0.5 ATR


def score_volatility(atr_ratio, dist_atr):
    """Volatility score from H4 ATR regime, penalised/boosted by EMA21 extension."""
    atr_ratio = _safe_score(atr_ratio)
    dist_atr = _safe_score(dist_atr)
    if atr_ratio <= 0:
        return 20, "ATR ratio unavailable = vol 20 (dead)"

    base, band = VOL_OVEREXTENDED, "overextended (>1.2)"
    for edge, band_score, label in VOL_BANDS:
        if atr_ratio < edge:
            base, band = band_score, label
            break

    if dist_atr > 1.2:
        score = int(max(0, min(100, base + VOL_EXTENDED_PENALTY)))
        adj = f"dist {dist_atr:.2f} ATR extended {VOL_EXTENDED_PENALTY}"
    elif dist_atr < 0.5:
        score = int(max(0, min(100, base + VOL_PULLBACK_BONUS)))
        adj = f"dist {dist_atr:.2f} ATR pullback +{VOL_PULLBACK_BONUS}"
    else:
        score = int(max(0, min(100, base)))
        adj = f"dist {dist_atr:.2f} ATR neutral"

    return score, f"ATR ratio {atr_ratio:.2f} {band} + {adj} = vol {score}"


def score_levels(price, daily_low, daily_high, zone_label=""):
    """Levels score from where price sits in the daily range.

    Near an edge is good for a limit order; the middle is equilibrium chop.
    """
    price = _safe_score(price)
    daily_low = _safe_score(daily_low)
    daily_high = _safe_score(daily_high)
    span = daily_high - daily_low
    if span <= 0:
        return 40, "Daily range unavailable = levels 40 (equilibrium)"

    pct = max(0.0, min(100.0, (price - daily_low) / span * 100.0))
    pct = round(pct, 2)  # kill float dust so exact band edges land where they should
    if pct <= 25 or pct >= 75:
        score, zone = 80, "near range edge (good for limit)"
    elif pct < 40 or pct > 60:
        score, zone = 70, "mid edge"
    else:
        score, zone = 40, "equilibrium chop"
    # Watchout zone is extra context, so keep it parenthetical - never overwrite
    # the band, otherwise the two read as contradictory.
    zone = f"{zone} ({zone_label})" if zone_label else zone

    return score, f"Price at {pct:.0f}% of daily range = {zone} = levels {score}"

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
