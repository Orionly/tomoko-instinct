
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


def score_structure(regime, fund=None):
    """Market Structure score (0-100) from H4 Value Gate, ADX strength, and MTF alignment."""
    if not regime or not isinstance(regime, dict):
        fallback = int(_safe_score((fund or {}).get("structural", 50)))
        return fallback, f"Fallback structure = {fallback}", {
            "score": fallback,
            "weight_pct": 30,
            "weighted_pts": round(fallback * 0.30, 1),
            "status": "NEUTRAL",
            "items": [{"label": "Structure Telemetry", "val": "Fallback", "pts": f"{fallback}/100"}],
            "rule": "Max 100: H4 Gate (45) + ADX Strength (25) + MTF Alignment (30)",
        }

    h4 = regime.get("H4") or {}
    gate = str(h4.get("gate", "INSIDE")).upper()
    direction = str(h4.get("direction", "FLAT")).upper()
    adx = _safe_score(h4.get("adx", 0))
    h4_bias = str(regime.get("H4_bias", "Neutral"))
    h1_bias = str(regime.get("H1_bias", "Neutral"))
    m15_bias = str(regime.get("M15_bias", "Neutral"))

    # 1. H4 Value Gate (max 45 pts)
    if gate == "OPEN" and direction in ("UP", "DOWN"):
        gate_pts = 45
        gate_val = f"OPEN ({direction})"
    elif direction in ("UP", "DOWN"):
        gate_pts = 25
        gate_val = f"INSIDE ({direction} bias)"
    else:
        gate_pts = 10
        gate_val = "FLAT / COMPRESSED"

    # 2. ADX Trend Strength (max 25 pts)
    if adx >= 30:
        adx_pts = 25
        adx_val = f"ADX {adx:.1f} (Strong ≥30)"
    elif adx >= 22:
        adx_pts = 18
        adx_val = f"ADX {adx:.1f} (Active 22–30)"
    elif adx >= 15:
        adx_pts = 10
        adx_val = f"ADX {adx:.1f} (Weak <22)"
    else:
        adx_pts = 5
        adx_val = f"ADX {adx:.1f} (Ranging <15)"

    # 3. Multi-Timeframe Alignment H4 / H1 / M15 (max 30 pts)
    non_neutral = [b for b in (h4_bias, h1_bias, m15_bias) if b in ("Bullish", "Bearish")]
    bull_count = sum(1 for b in (h4_bias, h1_bias, m15_bias) if b == "Bullish")
    bear_count = sum(1 for b in (h4_bias, h1_bias, m15_bias) if b == "Bearish")
    max_same = max(bull_count, bear_count)
    dom_dir = "Bullish" if bull_count >= bear_count else "Bearish"

    if max_same == 3:
        mtf_pts = 30
        mtf_val = f"3/3 Aligned ({dom_dir})"
    elif max_same == 2:
        mtf_pts = 20
        mtf_val = f"2/3 Aligned ({dom_dir})"
    elif len(non_neutral) >= 1:
        mtf_pts = 12
        mtf_val = f"Mixed (H4 {h4_bias[:4]} · H1 {h1_bias[:4]} · M15 {m15_bias[:4]})"
    else:
        mtf_pts = 5
        mtf_val = "Neutral / Unaligned"

    total = int(max(0, min(100, gate_pts + adx_pts + mtf_pts)))
    status = "STRONG TREND" if total >= 75 else ("DEVELOPING" if total >= 50 else "CONSOLIDATING")
    tooltip = f"H4 Gate {gate_val} (+{gate_pts}) + {adx_val} (+{adx_pts}) + MTF {mtf_val} (+{mtf_pts}) = {total}/100"

    detail = {
        "score": total,
        "weight_pct": 30,
        "weighted_pts": round(total * 0.30, 1),
        "status": status,
        "items": [
            {"label": "H4 Value Gate", "val": gate_val, "pts": f"+{gate_pts}/45"},
            {"label": "Trend Strength", "val": adx_val, "pts": f"+{adx_pts}/25"},
            {"label": "MTF Alignment", "val": mtf_val, "pts": f"+{mtf_pts}/30"},
        ],
        "rule": "Max 100: H4 Gate OPEN (45) + ADX ≥30 (25) + 3-TF Alignment (30)",
    }
    return total, tooltip, detail


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


def build_score_details(
    structural_score,
    structural_detail,
    volatility_score,
    atr_ratio,
    dist_atr,
    levels_score,
    price,
    daily_low,
    daily_high,
    zone_label,
    intermarket_score,
    fund,
    dxy_change,
    us10y_change,
    symbol,
    news_score,
    news,
    news_why_str,
    total_score,
):
    """Build structured, human-explainable breakdown cards for all 5 pillars + overall score."""
    atr_ratio = _safe_score(atr_ratio)
    dist_atr = _safe_score(dist_atr)
    price = _safe_score(price)
    daily_low = _safe_score(daily_low)
    daily_high = _safe_score(daily_high)

    # --- 2. Volatility Detail ---
    if atr_ratio <= 0:
        vol_base, vol_band = 20, "Unavailable (<0.6x)"
    elif atr_ratio < 0.6:
        vol_base, vol_band = 20, f"{atr_ratio:.2f}x (Dead <0.6x)"
    elif atr_ratio < 0.8:
        vol_base, vol_band = 50, f"{atr_ratio:.2f}x (Thin 0.6–0.8x)"
    elif atr_ratio < 1.2:
        vol_base, vol_band = 80, f"{atr_ratio:.2f}x (Healthy 0.8–1.2x)"
    else:
        vol_base, vol_band = 70, f"{atr_ratio:.2f}x (Hot >1.2x)"

    if dist_atr > 1.2:
        dist_label = f"{dist_atr:.2f} ATR (Extended >1.2)"
        dist_pts = "-10 pts"
    elif dist_atr < 0.5 and atr_ratio > 0:
        dist_label = f"{dist_atr:.2f} ATR (Pullback <0.5)"
        dist_pts = "+10 pts"
    else:
        dist_label = f"{dist_atr:.2f} ATR (Normal 0.5–1.2)"
        dist_pts = "+0 pts"

    vol_status = "OPTIMAL VOL" if volatility_score >= 75 else ("MODERATE VOL" if volatility_score >= 50 else "LOW / EXTENDED")
    volatility_detail = {
        "score": int(volatility_score),
        "weight_pct": 20,
        "weighted_pts": round(volatility_score * 0.20, 1),
        "status": vol_status,
        "items": [
            {"label": "H4 ATR Ratio", "val": vol_band, "pts": f"{vol_base} base"},
            {"label": "EMA21 Distance", "val": dist_label, "pts": dist_pts},
        ],
        "rule": "Base: Healthy 0.8–1.2x (80), Hot >1.2x (70), Thin (50) · Pullback <0.5 ATR (+10), Extended >1.2 (-10)",
    }

    # --- 3. Levels Detail ---
    span = daily_high - daily_low
    if span > 0:
        pct = max(0.0, min(100.0, (price - daily_low) / span * 100.0))
        if pct <= 25:
            pos_str = f"{pct:.0f}% (Deep Discount ≤25%)"
            loc_quality = "High-R:R Buy/Limit Edge"
        elif pct >= 75:
            pos_str = f"{pct:.0f}% (Deep Premium ≥75%)"
            loc_quality = "High-R:R Sell/Limit Edge"
        elif pct < 40:
            pos_str = f"{pct:.0f}% (Lower-Mid 25–40%)"
            loc_quality = "Approaching Discount"
        elif pct > 60:
            pos_str = f"{pct:.0f}% (Upper-Mid 60–75%)"
            loc_quality = "Approaching Premium"
        else:
            pos_str = f"{pct:.0f}% (Middle 40–60%)"
            loc_quality = "Equilibrium Chop (Poor R:R)"
    else:
        pos_str = "Range Unavailable"
        loc_quality = "Default Equilibrium"

    levels_status = "RANGE EDGE" if levels_score >= 75 else ("APPROACHING EDGE" if levels_score >= 65 else "MID-RANGE CHOP")
    levels_detail = {
        "score": int(levels_score),
        "weight_pct": 20,
        "weighted_pts": round(levels_score * 0.20, 1),
        "status": levels_status,
        "items": [
            {"label": "Daily Range Pos", "val": pos_str, "pts": f"{int(levels_score)}/100"},
            {"label": "Location Edge", "val": f"{loc_quality}{f' · {zone_label}' if zone_label else ''}", "pts": "Edge ≥75/≤25%"},
        ],
        "rule": "Edge ≤25% or ≥75% of Daily Range = 80 pts · Mid-Edge (25–40% / 60–75%) = 70 pts · Middle 40–60% Chop = 40 pts",
    }

    # --- 4. Intermarket Detail ---
    fund = fund or {}
    rates = fund.get("rates") or {}
    bias_str = fund.get("bias", "Balanced")
    is_gold = "XAU" in str(symbol).upper()
    if is_gold:
        dxy_str = f"DXY {dxy_change:+.2f}%" if dxy_change else "DXY Steady"
        y10_str = f"US10Y {us10y_change:+.2f}%" if us10y_change else "US10Y Steady"
        im_items = [
            {"label": "USD / Real Yields", "val": f"{dxy_str} · {y10_str}", "pts": f"{int(intermarket_score)}/100"},
            {"label": "Gold Flow Bias", "val": bias_str, "pts": "Inverse USD"},
        ]
        im_rule = "DXY Down + Yields Down = 85 pts (Bullish Tailwind) · Mixed = 60 pts · DXY Up + Yields Up = 30 pts (Headwind)"
    else:
        s_up = str(symbol).upper()
        base_c, quote_c = s_up[:3], s_up[3:6]
        r_b, r_q = rates.get(base_c, 0), rates.get(quote_c, 0)
        diff = r_b - r_q
        im_items = [
            {"label": "Policy Rate Spread", "val": f"{base_c} {r_b}% vs {quote_c} {r_q}% ({diff:+.2f}%)", "pts": f"{int(intermarket_score)}/100"},
            {"label": "Macro Carry Bias", "val": f"DXY {dxy_change:+.2f}% · 10Y {us10y_change:+.2f}%", "pts": "Carry Aligned" if diff != 0 else "Neutral"},
        ]
        im_rule = "Central bank policy rate differential & USD/Yield cross-asset flow alignment (65–85 pts favorable)"

    im_status = "TAILWIND" if intermarket_score >= 70 else ("NEUTRAL FLOW" if intermarket_score >= 50 else "HEADWIND")
    intermarket_detail = {
        "score": int(intermarket_score),
        "weight_pct": 15,
        "weighted_pts": round(intermarket_score * 0.15, 1),
        "status": im_status,
        "items": im_items,
        "rule": im_rule,
    }

    # --- 5. News & Event Risk Detail ---
    is_clean = news_score >= 100
    blocking = (news or {}).get("blocking") or []
    upcoming = (news or {}).get("upcoming_events") or []
    if is_clean:
        gate_str = "CLEAR (No Tier-1 within 60m)"
        next_ev_str = f"{upcoming[0].get('currency', '')} {upcoming[0].get('event', '')[:24]}" if upcoming else "No imminent high-impact event"
    else:
        ev = blocking[0] if blocking else {}
        gate_str = "BLOCKED (Tier-1 < 60m)"
        next_ev_str = f"{ev.get('currency', '')} {ev.get('event', 'High-Impact Release')[:26]}"

    news_detail = {
        "score": int(news_score),
        "weight_pct": 15,
        "weighted_pts": round(news_score * 0.15, 1),
        "status": "CLEAR SKY" if is_clean else "NEWS BLACKOUT",
        "items": [
            {"label": "60m Blackout Gate", "val": gate_str, "pts": f"{int(news_score)}/100"},
            {"label": "Event Horizon", "val": next_ev_str, "pts": "+15 pts" if is_clean else "0 pts (Blocked)"},
        ],
        "rule": "100 pts (+15 weighted) when no High-Impact event within 60m · Drops to 0 pts during news blackout window",
    }

    s_w = structural_detail["weighted_pts"]
    v_w = volatility_detail["weighted_pts"]
    l_w = levels_detail["weighted_pts"]
    i_w = intermarket_detail["weighted_pts"]
    n_w = news_detail["weighted_pts"]

    return {
        "total_score": int(total_score),
        "formula_summary": f"Structure {s_w:.1f} + Volatility {v_w:.1f} + Levels {l_w:.1f} + Macro {i_w:.1f} + News {n_w:.1f} = {int(total_score)} / 100",
        "meaning": "Measures Setup & Environment Quality (0–100) — NOT win-rate or bullish/bearish direction. High scores mean clean structure, healthy volatility, range-edge location, and no news blackout.",
        "tiers": [
            {"range": "70–100", "label": "HIGH EDGE", "desc": "Clean institutional environment — execute valid setups at decision zones."},
            {"range": "40–69", "label": "MODERATE", "desc": "Mixed confluence or mid-range — wait for pullback to EMA21 or key boundary."},
            {"range": "0–39", "label": "CHOP / LOW", "desc": "Compressed chop, overextended, or news blackout — stand aside."},
        ],
        "pillars": {
            "structural": structural_detail,
            "volatility": volatility_detail,
            "levels": levels_detail,
            "intermarket": intermarket_detail,
            "news": news_detail,
        },
    }

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
