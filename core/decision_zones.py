# Tomoko Instinct - Dynamic Multi-Timeframe Decision Zones Engine
# Decision support zones derived from dynamic swing tips, SNR fractals, and EMA mean-reversion.
# NOT entry signals: price either REJECTS (fade) or BREAKS (wait for retest flip).
# Automatically shifts and promotes zones as price moves through them.

import math
from typing import Dict, List, Any, Optional
import pandas as pd
from core.zone_engine import build_zones, select_slots, zone_tradeability, htf_swing_levels


def _safe_float(val, default=0.0) -> float:
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def _find_swing_levels(df: Optional[pd.DataFrame], lookback: int = 50, window: int = 2) -> List[Dict[str, Any]]:
    """Detect local fractal swing highs and swing lows from recent price action."""
    if df is None or len(df) < (window * 2 + 1):
        return []

    subset = df.iloc[-lookback:].copy().reset_index(drop=True)
    n = len(subset)
    levels = []

    for i in range(window, n - window):
        high_i = _safe_float(subset['high'].iloc[i])
        low_i = _safe_float(subset['low'].iloc[i])
        
        # Check swing high
        is_swing_high = True
        for j in range(i - window, i + window + 1):
            if j != i and _safe_float(subset['high'].iloc[j]) >= high_i:
                is_swing_high = False
                break
        if is_swing_high and high_i > 0:
            levels.append({
                "price": high_i,
                "type": "SWING_HIGH",
                "label": "Recent Swing High (Fractal Tip)",
                "bars_ago": n - 1 - i
            })

        # Check swing low
        is_swing_low = True
        for j in range(i - window, i + window + 1):
            if j != i and _safe_float(subset['low'].iloc[j]) <= low_i:
                is_swing_low = False
                break
        if is_swing_low and low_i > 0:
            levels.append({
                "price": low_i,
                "type": "SWING_LOW",
                "label": "Recent Swing Low (Fractal Floor)",
                "bars_ago": n - 1 - i
            })

    return levels


def _cluster_levels(raw_levels: List[Dict[str, Any]], cluster_threshold: float) -> List[Dict[str, Any]]:
    """Cluster levels that sit within threshold distance of each other into high-confluence zones."""
    if not raw_levels:
        return []

    # Sort by price
    sorted_levels = sorted(raw_levels, key=lambda x: x["price"])
    clusters = []
    current_cluster = [sorted_levels[0]]

    for lvl in sorted_levels[1:]:
        if abs(lvl["price"] - current_cluster[-1]["price"]) <= cluster_threshold:
            current_cluster.append(lvl)
        else:
            clusters.append(current_cluster)
            current_cluster = [lvl]
    if current_cluster:
        clusters.append(current_cluster)

    results = []
    for cl in clusters:
        avg_price = sum(x["price"] for x in cl) / len(cl)
        types = set(x["type"] for x in cl)
        labels = list(dict.fromkeys(x["label"] for x in cl))
        min_bars_ago = min(x.get("bars_ago", 999) for x in cl)

        results.append({
            "price": avg_price,
            "min_price": min(x["price"] for x in cl),
            "max_price": max(x["price"] for x in cl),
            "types": list(types),
            "labels": labels,
            "confluence_count": len(cl),
            "bars_ago": min_bars_ago
        })

    return results


def calculate_tf_decision_zones(
    df: Optional[pd.DataFrame],
    current_price: float,
    timeframe: str,
    atr_override: float = 0.0,
    extra_anchors: Optional[List[Dict[str, Any]]] = None,
    symbol: str = "EURUSD"
) -> Dict[str, Any]:
    """Calculate Upper DZ 2, Upper DZ 1, Lower DZ 1, Lower DZ 2 dynamically relative to current price."""
    current_price = _safe_float(current_price)
    is_gold = "XAU" in symbol.upper()
    is_jpy = "JPY" in symbol.upper()

    # Determine local ATR
    atr = atr_override
    if (atr <= 0 or math.isnan(atr)) and df is not None and len(df) > 0 and 'ATR' in df.columns:
        atr = _safe_float(df['ATR'].iloc[-1])
    if atr <= 0:
        atr = 0.0020 if not is_gold else 2.50

    # Zone width factors and strict buffers by timeframe
    min_pip = 1.0 if is_gold else (0.02 if is_jpy else 0.0002)
    if timeframe.lower() == "m15":
        lookback = 45
        window = 2
        zone_half = max(atr * 0.25, min_pip * 0.8)
        min_buffer = max(atr * 0.25, min_pip)
    elif timeframe.lower() == "h1":
        lookback = 60
        window = 2
        zone_half = max(atr * 0.30, min_pip * 1.0)
        min_buffer = max(atr * 0.30, min_pip * 1.5)
    else:  # h4
        lookback = 80
        window = 3
        zone_half = max(atr * 0.35, min_pip * 1.5)
        min_buffer = max(atr * 0.35, min_pip * 2.0)

    # 1. Collect swing points
    raw_levels = _find_swing_levels(df, lookback=lookback, window=window)

    # 2. Add EMA21 / EMA50 dynamic levels if available
    if df is not None and len(df) > 0:
        if 'EMA21' in df.columns:
            ema21_val = _safe_float(df['EMA21'].iloc[-1])
            if ema21_val > 0:
                raw_levels.append({
                    "price": ema21_val,
                    "type": "DYNAMIC_EMA21",
                    "label": f"{timeframe.upper()} EMA21 Dynamic Value",
                    "bars_ago": 0
                })
        if 'EMA50' in df.columns:
            ema50_val = _safe_float(df['EMA50'].iloc[-1])
            if ema50_val > 0:
                raw_levels.append({
                    "price": ema50_val,
                    "type": "DYNAMIC_EMA50",
                    "label": f"{timeframe.upper()} EMA50 Baseline",
                    "bars_ago": 0
                })

    # 3. Add extra structural anchors (Daily High, Daily Low, Weekly Open, Liq High, Liq Low)
    if extra_anchors:
        for anc in extra_anchors:
            if anc.get("price", 0) > 0:
                raw_levels.append(anc)

    # Cluster nearby levels
    clustered = _cluster_levels(raw_levels, cluster_threshold=zone_half * 0.8)

    # 4. Filter Above (Ceilings) & Below (Floors)
    above_levels = [x for x in clustered if x["price"] > (current_price + min_buffer)]
    below_levels = [x for x in clustered if x["price"] < (current_price - min_buffer)]

    # Sort ascending for upper (nearest first)
    above_levels.sort(key=lambda x: x["price"])
    # Sort descending for lower (nearest first)
    below_levels.sort(key=lambda x: x["price"], reverse=True)

    # Determine pip size
    if is_gold:
        pip_size = 0.1
    elif is_jpy:
        pip_size = 0.01
    else:
        pip_size = 0.0001

    def _fmt_p(v: float) -> str:
        if v is None or math.isnan(v):
            return "—"
        if is_gold:
            return f"{v:.2f}"
        if is_jpy:
            return f"{v:.3f}"
        return f"{v:.5f}"

    # Helper to evaluate real-time order flow interaction for a zone (5 Deterministic States)
    def evaluate_zone_interaction(z: Dict[str, Any]) -> Dict[str, Any]:
        low = z["low"]
        high = z["high"]
        is_upper = z["is_upper"]
        z_title = z.get("title", "DECISION ZONE")

        # Proximity threshold: 5–15 pips (or 0.30 ATR)
        if is_gold:
            proximity_dist = max(1.5, min(5.0, (atr * 0.30)))
            rejection_thresh = max(0.6, (atr * 0.12))
        else:
            proximity_dist = max(5.0, min(15.0, (atr * 0.30) / pip_size))
            rejection_thresh = max(2.0, (atr * 0.08) / pip_size)

        # 1. State: TESTING (Price currently trading inside [zone_low, zone_high])
        if low <= current_price <= high:
            return {
                "state": "TESTING",
                "badge_text": "⚡ TESTING ZONE",
                "badge_class": "badge-testing",
                "status_text": f"Testing {z_title} ({_fmt_p(low)} – {_fmt_p(high)}). Order flow absorption in progress.",
                "pips_dist": 0.0,
                "action_hint": "Wait for candle close confirmation — do not anticipate the breakout."
            }

        # Inspect latest 1-2 bars only (rejection must be active/fresh, not from 1-2 hours ago)
        recent_high = current_price
        recent_low = current_price
        last_close = current_price
        if df is not None and len(df) >= 2:
            last_bars = df.tail(2)
            recent_high = max([_safe_float(h) for h in last_bars["high"]] + [current_price])
            recent_low = min([_safe_float(l) for l in last_bars["low"]] + [current_price])
            last_close = _safe_float(df["close"].iloc[-1])

        unit_str = "pts" if is_gold else "pips"
        unit_abbr = "pts" if is_gold else "p"

        if is_upper:
            # Upper Zone (Resistance)
            if current_price < low:
                diff = low - current_price
                dist_val = diff if is_gold else (diff / pip_size)

                # Active Rejection: Price recently reached into the zone and was rejected downward
                # Must have reached at least to the zone edge [low - 0.05*atr, high + zone_half]
                in_zone_reach = (recent_high >= (low - 0.05 * atr)) and (recent_high <= (high + zone_half))
                drop_val = (recent_high - current_price) if is_gold else ((recent_high - current_price) / pip_size)

                if in_zone_reach and drop_val >= rejection_thresh and current_price >= (low - atr * 0.65):
                    tested_p = min(recent_high, high)
                    return {
                        "state": "REJECTED",
                        "badge_text": f"🛡️ REJECTED (-{drop_val:.1f}{unit_abbr})",
                        "badge_class": "badge-rejected",
                        "status_text": f"Rejection Confirmed: Tested {z_title} at {_fmt_p(tested_p)} and dropped -{drop_val:.1f} {unit_str}. Seller defense.",
                        "pips_dist": round(dist_val, 1),
                        "action_hint": "Bearish rejection confirmed. Look for confirmation before entering fade."
                    }

                # Approaching
                if dist_val <= proximity_dist:
                    return {
                        "state": "APPROACHING",
                        "badge_text": f"🎯 APPROACHING ({dist_val:.1f}{unit_abbr})",
                        "badge_class": "badge-approaching",
                        "status_text": f"Approaching {z_title} ({_fmt_p(low)} – {_fmt_p(high)}) · {dist_val:.1f} {unit_str} away. Prepare reaction plan.",
                        "pips_dist": round(dist_val, 1),
                        "action_hint": "Approaching resistance. Observe whether buyers absorb liquidity or stall."
                    }

                # In Transit
                return {
                    "state": "IN_TRANSIT",
                    "badge_text": f"⏳ IN TRANSIT (+{dist_val:.1f}{unit_abbr})",
                    "badge_class": "badge-transit",
                    "status_text": f"Ceiling sitting +{dist_val:.1f} {unit_str} above current price ({_fmt_p(low)} – {_fmt_p(high)}).",
                    "pips_dist": round(dist_val, 1),
                    "action_hint": "Structure mapped. No immediate zone pressure."
                }
            else:
                # Breakout through resistance
                surpass_val = (current_price - high) if is_gold else ((current_price - high) / pip_size)
                return {
                    "state": "BREAKOUT",
                    "badge_text": f"🚀 BROKE THROUGH (+{surpass_val:.1f}{unit_abbr})",
                    "badge_class": "badge-breakout",
                    "status_text": f"Breakthrough: Cleared {z_title} ({_fmt_p(high)}). Watch for pullback & S/R flip retest.",
                    "pips_dist": round(-surpass_val, 1),
                    "action_hint": "Breakthrough in progress. Do not chase; await pullback retest of broken zone."
                }
        else:
            # Lower Zone (Support)
            if current_price > high:
                diff = current_price - high
                dist_val = diff if is_gold else (diff / pip_size)

                # Active Rejection: Price dipped into support [low - zone_half, high + 0.05*atr] and bounced upward
                in_zone_reach = (recent_low <= (high + 0.05 * atr)) and (recent_low >= (low - zone_half))
                bounce_val = (current_price - recent_low) if is_gold else ((current_price - recent_low) / pip_size)

                if in_zone_reach and bounce_val >= rejection_thresh and current_price <= (high + atr * 0.65):
                    tested_p = max(recent_low, low)
                    return {
                        "state": "REJECTED",
                        "badge_text": f"🛡️ REJECTED (+{bounce_val:.1f}{unit_abbr})",
                        "badge_class": "badge-rejected",
                        "status_text": f"Rejection Confirmed: Tested {z_title} at {_fmt_p(tested_p)} and bounced +{bounce_val:.1f} {unit_str}. Buyer defense.",
                        "pips_dist": round(dist_val, 1),
                        "action_hint": "Bullish absorption confirmed. Look for continuation confirmation."
                    }

                # Approaching
                if dist_val <= proximity_dist:
                    return {
                        "state": "APPROACHING",
                        "badge_text": f"🎯 APPROACHING ({dist_val:.1f}{unit_abbr})",
                        "badge_class": "badge-approaching",
                        "status_text": f"Approaching {z_title} ({_fmt_p(low)} – {_fmt_p(high)}) · {dist_val:.1f} {unit_str} away. Prepare reaction plan.",
                        "pips_dist": round(dist_val, 1),
                        "action_hint": "Approaching support. Observe whether sellers exhaust or punch through."
                    }

                # In Transit
                return {
                    "state": "IN_TRANSIT",
                    "badge_text": f"⏳ IN TRANSIT (-{dist_val:.1f}{unit_abbr})",
                    "badge_class": "badge-transit",
                    "status_text": f"Floor sitting -{dist_val:.1f} {unit_str} below current price ({_fmt_p(low)} – {_fmt_p(high)}).",
                    "pips_dist": round(dist_val, 1),
                    "action_hint": "Structure mapped. No immediate zone pressure."
                }
            else:
                # Breakdown below support
                break_val = (low - current_price) if is_gold else ((low - current_price) / pip_size)
                return {
                    "state": "BREAKOUT",
                    "badge_text": f"🚀 BROKE THROUGH (-{break_val:.1f}{unit_abbr})",
                    "badge_class": "badge-breakout",
                    "status_text": f"Breakthrough: Cleared below {z_title} ({_fmt_p(low)}). Watch for pullback & S/R flip retest.",
                    "pips_dist": round(-break_val, 1),
                    "action_hint": "Floor broken. Watch for weak retest from below."
                }

    # Helper to construct a DZ item
    def build_zone(item: Optional[Dict[str, Any]], zone_id: str, default_offset: float, is_upper: bool) -> Dict[str, Any]:
        if item is not None:
            center = item["price"]
            raw_low = min(item["min_price"] - zone_half * 0.3, center - zone_half)
            raw_high = max(item["max_price"] + zone_half * 0.3, center + zone_half)
            confluences = item["labels"]
            is_flip = ("SWING_HIGH" in item.get("types", []) and not is_upper) or \
                      ("SWING_LOW" in item.get("types", []) and is_upper)
            flip_note = "🔁 S/R FLIP RETEST ZONE (Broken Structure)" if is_flip else None
        else:
            center = current_price + default_offset
            raw_low = center - zone_half
            raw_high = center + zone_half
            confluences = [f"Structural {timeframe.upper()} ATR Extension (+{abs(default_offset/atr):.1f}x ATR)"]
            flip_note = None

        if is_upper:
            # Upper Zone (Supply) sits strictly ABOVE current price
            low = max(raw_low, current_price + min_buffer * 0.4)
            high = max(raw_high, low + zone_half * 0.6)
            center = (low + high) / 2
        else:
            # Lower Zone (Demand) sits strictly BELOW current price
            high = min(raw_high, current_price - min_buffer * 0.4)
            low = min(raw_low, high - zone_half * 0.6)
            center = (low + high) / 2

        diff = center - current_price
        if is_gold:
            dist_label = f"{'+' if diff >= 0 else ''}{diff:.2f} pts"
            pips_val = round(diff, 2)
        elif is_jpy:
            pips_val = round(diff * 100, 1)
            dist_label = f"{'+' if diff >= 0 else ''}{pips_val:.1f} pips"
        else:
            pips_val = round(diff * 10000, 1)
            dist_label = f"{'+' if diff >= 0 else ''}{pips_val:.1f} pips"

        tier_title = "IMMEDIATE" if "1" in zone_id else "SECONDARY / EXPANSION"
        direction_title = "UPPER" if is_upper else "LOWER"

        z_res = {
            "id": zone_id,
            "title": f"{direction_title} DECISION ZONE {1 if '1' in zone_id else 2}",
            "subtitle": f"{tier_title} {'RESISTANCE' if is_upper else 'SUPPORT'}",
            "center": round(center, 5 if not (is_gold or is_jpy) else 3),
            "low": round(low, 5 if not (is_gold or is_jpy) else 3),
            "high": round(high, 5 if not (is_gold or is_jpy) else 3),
            "distance_pips": pips_val,
            "distance_label": dist_label,
            "distance_atr": round(abs(diff) / atr, 2),
            "is_upper": is_upper,
            "is_flip": bool(flip_note),
            "flip_note": flip_note,
            "confluences": confluences,
            "playbook_reject": "Look for wick exhaustion & absorption inside zone to fade back toward mean." if is_upper else "Look for lower wick absorption & buying reaction to retest higher levels.",
            "playbook_break": "If candle body closes through zone, await pullback retest of broken level before continuation."
        }
        z_res["interaction"] = evaluate_zone_interaction(z_res)
        return z_res

    # Upper DZ 1 (Immediate) & Upper DZ 2 (Secondary)
    u1 = above_levels[0] if len(above_levels) >= 1 else None
    upper_dz_1 = build_zone(u1, "upper_dz_1", atr * 0.8, is_upper=True)

    # Upper DZ 2 (Secondary / Expansion) - MUST sit strictly above Upper DZ 1
    min_u2_center = upper_dz_1["high"] + max(zone_half * 2.0, atr * 0.8)
    u2 = None
    for cand in above_levels[1:]:
        if cand["price"] >= min_u2_center:
            u2 = cand
            break

    if u2 is not None:
        upper_dz_2 = build_zone(u2, "upper_dz_2", atr * 1.8, is_upper=True)
    else:
        synthetic_offset = max(atr * 1.8, (upper_dz_1["high"] + atr * 0.8) - current_price)
        upper_dz_2 = build_zone(None, "upper_dz_2", synthetic_offset, is_upper=True)

    # Lower DZ 1 (Immediate) & Lower DZ 2 (Secondary)
    l1 = below_levels[0] if len(below_levels) >= 1 else None
    lower_dz_1 = build_zone(l1, "lower_dz_1", -atr * 0.8, is_upper=False)

    # Lower DZ 2 (Secondary / Expansion) - MUST sit strictly below Lower DZ 1
    max_l2_center = lower_dz_1["low"] - max(zone_half * 2.0, atr * 0.8)
    l2 = None
    for cand in below_levels[1:]:
        if cand["price"] <= max_l2_center:
            l2 = cand
            break

    if l2 is not None:
        lower_dz_2 = build_zone(l2, "lower_dz_2", -atr * 1.8, is_upper=False)
    else:
        synthetic_offset = -max(atr * 1.8, current_price - (lower_dz_1["low"] - atr * 0.8))
        lower_dz_2 = build_zone(None, "lower_dz_2", synthetic_offset, is_upper=False)

    # Determine active proximity radar for this timeframe
    zones_list = [upper_dz_1, lower_dz_1, upper_dz_2, lower_dz_2]
    testing_zone = next((z for z in zones_list if z["interaction"]["state"] == "TESTING"), None)
    rejected_zone = next((z for z in zones_list if z["interaction"]["state"] == "REJECTED"), None)
    breakout_zone = next((z for z in zones_list if z["interaction"]["state"] == "BREAKOUT"), None)
    approaching_candidates = [z for z in zones_list if z["interaction"]["state"] == "APPROACHING"]
    approaching_zone = min(approaching_candidates, key=lambda z: abs(z["interaction"]["pips_dist"]), default=None)

    # Compute distances to opposite immediate zones for telemetry message
    dist_to_u1 = (upper_dz_1["low"] - current_price) / pip_size
    dist_to_l1 = (current_price - lower_dz_1["high"]) / pip_size

    if testing_zone:
        other_note = f"Nearest Upper DZ 1 is +{dist_to_u1:.1f} pips away." if not testing_zone["is_upper"] else f"Nearest Lower DZ 1 is -{dist_to_l1:.1f} pips away."
        radar = {
            "state": "TESTING",
            "badge_text": "🔵 TESTING ZONE",
            "headline": f"Price is currently TESTING {testing_zone['title']} ({_fmt_p(testing_zone['low'])} – {_fmt_p(testing_zone['high'])}) on {timeframe.upper()}",
            "detail": f"{testing_zone['interaction']['status_text']} · {other_note}",
            "badge_class": "badge-testing",
            "action_hint": testing_zone["interaction"]["action_hint"],
            "active_zone_id": testing_zone["id"],
            "pips_dist": 0.0
        }
    elif rejected_zone:
        radar = {
            "state": "REJECTED",
            "badge_text": "🔴 REJECTION CONFIRMED",
            "headline": f"Rejection Confirmed at {rejected_zone['title']} on {timeframe.upper()}",
            "detail": rejected_zone["interaction"]["status_text"],
            "badge_class": "badge-rejected",
            "action_hint": rejected_zone["interaction"]["action_hint"],
            "active_zone_id": rejected_zone["id"],
            "pips_dist": rejected_zone["interaction"]["pips_dist"]
        }
    elif breakout_zone:
        radar = {
            "state": "BREAKOUT",
            "badge_text": "🟢 BREAKTHROUGH",
            "headline": f"Breakthrough: {breakout_zone['title']} Cleared on {timeframe.upper()}",
            "detail": breakout_zone["interaction"]["status_text"],
            "badge_class": "badge-breakout",
            "action_hint": breakout_zone["interaction"]["action_hint"],
            "active_zone_id": breakout_zone["id"],
            "pips_dist": breakout_zone["interaction"]["pips_dist"]
        }
    elif approaching_zone:
        p_dist = abs(approaching_zone["interaction"]["pips_dist"])
        radar = {
            "state": "APPROACHING",
            "badge_text": f"🟡 APPROACHING ({p_dist:.1f}p)",
            "headline": f"Approaching {approaching_zone['title']} ({_fmt_p(approaching_zone['low'])} – {_fmt_p(approaching_zone['high'])}) · {p_dist:.1f} pips away",
            "detail": approaching_zone["interaction"]["status_text"],
            "badge_class": "badge-approaching",
            "action_hint": approaching_zone["interaction"]["action_hint"],
            "active_zone_id": approaching_zone["id"],
            "pips_dist": round(p_dist, 1)
        }
    else:
        radar = {
            "state": "IN_TRANSIT",
            "badge_text": "⚪ IN TRANSIT",
            "headline": f"Trading in open range between Lower DZ 1 ({_fmt_p(lower_dz_1['high'])}) and Upper DZ 1 ({_fmt_p(upper_dz_1['low'])})",
            "detail": f"Cruising freely in open range · Nearest Upper DZ 1 is +{dist_to_u1:.1f}p away, Lower DZ 1 is -{dist_to_l1:.1f}p away.",
            "badge_class": "badge-transit",
            "action_hint": "Price is cruising freely between zones. Prepare reaction plan as boundary approaches.",
            "active_zone_id": None,
            "pips_dist": round(min(dist_to_u1, dist_to_l1), 1)
        }

    return {
        "timeframe": timeframe.upper(),
        "current_price": current_price,
        "atr": round(atr, 5 if not (is_gold or is_jpy) else 3),
        "radar": radar,
        "upper_dz_2": upper_dz_2,
        "upper_dz_1": upper_dz_1,
        "lower_dz_1": lower_dz_1,
        "lower_dz_2": lower_dz_2,
    }


def calculate_key_levels_radar(
    current_price: float,
    levels: Dict[str, Any],
    symbol: str = "EURUSD",
    atr: float = 0.0020
) -> Dict[str, Any]:
    """Track real-time interaction & proximity with benchmark levels (Daily Open 6:00 AM MYT, PDH, PDL, Weekly Open)."""
    is_gold = "XAU" in symbol.upper()
    is_jpy = "JPY" in symbol.upper()
    pip_size = 0.1 if is_gold else (0.01 if is_jpy else 0.0001)

    def _fmt_p(v: float) -> str:
        if v is None or math.isnan(v):
            return "—"
        if is_gold:
            return f"{v:.2f}"
        if is_jpy:
            return f"{v:.3f}"
        return f"{v:.5f}"

    benchmarks = [
        {"key": "daily_open", "name": "Daily Open (6:00 AM MYT)", "tag": "Day Open", "bias_driver": True},
        {"key": "daily_high", "name": "Previous Day High (PDH)", "tag": "PDH", "is_ceiling": True},
        {"key": "daily_low", "name": "Previous Day Low (PDL)", "tag": "PDL", "is_floor": True},
        {"key": "weekly_open", "name": "Weekly Open (Macro Pivot)", "tag": "Wk Open", "bias_driver": True},
        {"key": "today_high", "name": "Today Session High", "tag": "Day High", "is_ceiling": True},
        {"key": "today_low", "name": "Today Session Low", "tag": "Day Low", "is_floor": True},
    ]

    unit = "pts" if is_gold else "p"
    unit_full = "pts" if is_gold else "pips"

    test_threshold = max(2.5, (atr * 0.08) / pip_size if not is_gold else 1.5)
    approach_threshold = max(12.0, min(25.0, (atr * 0.35) / pip_size if not is_gold else 4.0))

    evaluated = []
    primary_interaction = None

    for b in benchmarks:
        val = _safe_float(levels.get(b["key"]))
        if val <= 0:
            continue

        if b["key"] in ("daily_open", "weekly_open"):
            # Session net performance: +14.8 pts if above open
            perf = current_price - val
            dist_val = perf if is_gold else (perf / pip_size)
            abs_dist = abs(dist_val)
            dist_label = f"{'+' if dist_val >= 0 else ''}{dist_val:.1f}{unit}"
            abs_check = abs(perf if is_gold else (perf / pip_size))
        else:
            diff = val - current_price
            dist_val = diff if is_gold else (diff / pip_size)
            abs_dist = abs(dist_val)
            dist_label = f"{'+' if dist_val >= 0 else ''}{dist_val:.1f}{unit}"
            abs_check = abs(diff if is_gold else (diff / pip_size))

        # Classify state
        if abs_check <= test_threshold:
            state = "TESTING"
            badge_class = "badge-testing"
            badge_text = f"⚡ TESTING ({abs_dist:.1f}{unit})"
            if b["key"] == "daily_open":
                status = f"Price is testing Daily Open ({_fmt_p(val)}) — Bullish intraday bias holds while price stays above."
            elif b.get("is_ceiling"):
                status = f"Price is testing {b['name']} ({_fmt_p(val)}) — Watching for ceiling absorption or wick rejection."
            elif b.get("is_floor"):
                status = f"Price is testing {b['name']} ({_fmt_p(val)}) — Watching for floor defense or breakdown."
            else:
                status = f"Price is testing {b['name']} ({_fmt_p(val)})."
        elif abs_check <= approach_threshold:
            state = "APPROACHING"
            badge_class = "badge-approaching"
            badge_text = f"🎯 APPROACHING ({abs_dist:.1f}{unit})"
            direction = "above" if dist_val > 0 else "below"
            status = f"Approaching {b['name']} ({_fmt_p(val)}) · Sitting {abs_dist:.1f} {unit_full} {direction} market."
        else:
            state = "IN_TRANSIT"
            badge_class = "badge-transit"
            badge_text = dist_label
            if b["key"] in ("daily_open", "weekly_open"):
                above = current_price >= val
                bias_word = "BULLISH" if above else "BEARISH"
                status = f"Trading {abs_dist:.1f} {unit_full} {'above' if above else 'below'} {b['name']} ({_fmt_p(val)}) — Intraday bias is {bias_word}."
            else:
                direction = "above" if dist_val > 0 else "below"
                status = f"{b['name']} sits {abs_dist:.1f} {unit_full} {direction} current price."

        item = {
            "key": b["key"],
            "name": b["name"],
            "tag": b["tag"],
            "price": val,
            "dist_pips": round(dist_val, 1),
            "dist_label": dist_label,
            "abs_pips": round(abs_dist, 1),
            "state": state,
            "badge_class": badge_class,
            "badge_text": badge_text,
            "status_text": status,
        }
        evaluated.append(item)

        # Determine primary benchmark interaction (TESTING > closest APPROACHING > Daily Open)
        if state == "TESTING" and (primary_interaction is None or primary_interaction["state"] != "TESTING"):
            primary_interaction = item
        elif state == "APPROACHING" and primary_interaction is None:
            primary_interaction = item

    if primary_interaction is None:
        do_item = next((it for it in evaluated if it["key"] == "daily_open"), None)
        primary_interaction = do_item or (evaluated[0] if evaluated else None)

    return {
        "benchmarks": evaluated,
        "primary": primary_interaction,
        "summary": primary_interaction["status_text"] if primary_interaction else "Benchmark levels mapped."
    }


def calculate_m15_decision_zones(
    df: Optional[pd.DataFrame],
    current_price: float,
    symbol: str = "EURUSD",
    anchors: Optional[List[Dict[str, Any]]] = None,
    htf_levels: Optional[List[float]] = None,
    htf_direction: str = "NEUTRAL"
) -> Dict[str, Any]:
    """
    Dedicated M15 Intraday Decision Zones Engine V2 powered by core.zone_engine.
    Features:
      * Strength scoring (0-100: STRONG / MEDIUM / WEAK)
      * Retest lifecycle tracking (FRESH, 1st/2nd retest OK, 3rd retest HTF-aligned only, 4th retest EXHAUSTED)
      * Sweep vs Break detection (Wick beyond = SWEEP; Body close beyond = BREAK to SWAP zone)
      * SWAP zone retention until tested
      * Realistic spacing constraints (no overlapping / cramped zones)
      * No synthetic/hallucinated zones (returns None when no qualified structure exists)
      * Range detection when Upper 1 and Lower 1 are in tight consolidation
    """
    current_price = _safe_float(current_price)
    is_gold = "XAU" in symbol.upper()
    is_jpy = "JPY" in symbol.upper()
    pip_size = 0.1 if is_gold else (0.01 if is_jpy else 0.0001)

    def _fmt_p(v: float) -> str:
        if v is None or math.isnan(v):
            return "—"
        if is_gold:
            return f"{v:.2f}"
        if is_jpy:
            return f"{v:.3f}"
        return f"{v:.5f}"

    unit_str = "pts" if is_gold else "pips"
    unit_abbr = "pts" if is_gold else "p"

    # Degraded / empty df check
    if df is None or len(df) < 15:
        empty_radar = {
            "state": "IN_TRANSIT",
            "badge_text": "⚪ AWAITING DATA",
            "headline": "Awaiting M15 intraday candle feed",
            "detail": "Insufficient M15 bars to extract structural zones.",
            "badge_class": "badge-transit",
            "action_hint": "Wait for market data feed connection.",
            "active_zone_id": None,
            "pips_dist": 0.0
        }
        return {
            "timeframe": "M15",
            "current_price": current_price,
            "atr": 2.50 if is_gold else (0.15 if is_jpy else 0.0010),
            "radar": empty_radar,
            "upper_dz_2": None,
            "upper_dz_1": None,
            "lower_dz_1": None,
            "lower_dz_2": None,
            "faded_zones": [],
        }

    # Run zone_engine
    built = build_zones(df, timeframe="M15", symbol=symbol, anchors=anchors, htf_levels=htf_levels)
    unit = built["unit"]
    if unit <= 0:
        unit = 2.50 if is_gold else (0.15 if is_jpy else 0.0010)

    p = built["params"]
    slots = select_slots(built["zones"], current_price, unit, p)

    proximity_dist = max(1.5, min(5.0, unit * 0.30)) if is_gold else max(5.0, min(15.0, (unit * 0.30) / pip_size))
    rejection_thresh = max(0.6, unit * 0.12) if is_gold else max(2.0, (unit * 0.08) / pip_size)

    recent_high = current_price
    recent_low = current_price
    if len(df) >= 2:
        last_bars = df.tail(2)
        recent_high = max([_safe_float(h) for h in last_bars["high"]] + [current_price])
        recent_low = min([_safe_float(l) for l in last_bars["low"]] + [current_price])

    def evaluate_m15_interaction(z_low: float, z_high: float, is_upper: bool, z_title: str, is_inside: bool) -> Dict[str, Any]:
        # 1. State: TESTING
        if is_inside or (z_low <= current_price <= z_high):
            return {
                "state": "TESTING",
                "badge_text": "⚡ TESTING ZONE",
                "badge_class": "badge-testing",
                "status_text": f"Testing {z_title} ({_fmt_p(z_low)} – {_fmt_p(z_high)}). Order flow absorption in progress.",
                "pips_dist": 0.0,
                "action_hint": "Wait for candle close confirmation — do not anticipate the breakout."
            }

        if is_upper:
            if current_price < z_low:
                diff = z_low - current_price
                dist_val = diff if is_gold else (diff / pip_size)
                in_reach = (recent_high >= (z_low - 0.05 * unit)) and (recent_high <= (z_high + (z_high - z_low)))
                drop_val = (recent_high - current_price) if is_gold else ((recent_high - current_price) / pip_size)

                if in_reach and drop_val >= rejection_thresh and current_price >= (z_low - unit * 0.65):
                    tested_p = min(recent_high, z_high)
                    return {
                        "state": "REJECTED",
                        "badge_text": f"🛡️ REJECTED (-{drop_val:.1f}{unit_abbr})",
                        "badge_class": "badge-rejected",
                        "status_text": f"Rejection Confirmed: Tested {z_title} at {_fmt_p(tested_p)} and dropped -{drop_val:.1f} {unit_str}. Seller defense.",
                        "pips_dist": round(dist_val, 1),
                        "action_hint": "Bearish wick rejection confirmed. Look for continuation toward mean."
                    }
                if dist_val <= proximity_dist:
                    return {
                        "state": "APPROACHING",
                        "badge_text": f"🎯 APPROACHING ({dist_val:.1f}{unit_abbr})",
                        "badge_class": "badge-approaching",
                        "status_text": f"Approaching {z_title} ({_fmt_p(z_low)} – {_fmt_p(z_high)}) · {dist_val:.1f} {unit_str} away. Prepare reaction plan.",
                        "pips_dist": round(dist_val, 1),
                        "action_hint": "Approaching resistance. Observe whether buyers exhaust or punch through."
                    }
                return {
                    "state": "IN_TRANSIT",
                    "badge_text": f"⏳ IN TRANSIT (+{dist_val:.1f}{unit_abbr})",
                    "badge_class": "badge-transit",
                    "status_text": f"Ceiling sitting +{dist_val:.1f} {unit_str} above current price ({_fmt_p(z_low)} – {_fmt_p(z_high)}).",
                    "pips_dist": round(dist_val, 1),
                    "action_hint": "Structure mapped. No immediate zone pressure."
                }
            else:
                surpass = (current_price - z_high) if is_gold else ((current_price - z_high) / pip_size)
                return {
                    "state": "BREAKOUT",
                    "badge_text": f"🚀 BROKE THROUGH (+{surpass:.1f}{unit_abbr})",
                    "badge_class": "badge-breakout",
                    "status_text": f"Breakthrough: Cleared {z_title} ({_fmt_p(z_high)}). Watch for pullback & S/R flip retest.",
                    "pips_dist": round(-surpass, 1),
                    "action_hint": "Breakthrough in progress. Do not chase; await pullback retest of broken zone."
                }
        else:
            if current_price > z_high:
                diff = current_price - z_high
                dist_val = diff if is_gold else (diff / pip_size)
                in_reach = (recent_low <= (z_high + 0.05 * unit)) and (recent_low >= (z_low - (z_high - z_low)))
                bounce_val = (current_price - recent_low) if is_gold else ((current_price - recent_low) / pip_size)

                if in_reach and bounce_val >= rejection_thresh and current_price <= (z_high + unit * 0.65):
                    tested_p = max(recent_low, z_low)
                    return {
                        "state": "REJECTED",
                        "badge_text": f"🛡️ REJECTED (+{bounce_val:.1f}{unit_abbr})",
                        "badge_class": "badge-rejected",
                        "status_text": f"Rejection Confirmed: Tested {z_title} at {_fmt_p(tested_p)} and bounced +{bounce_val:.1f} {unit_str}. Buyer defense.",
                        "pips_dist": round(dist_val, 1),
                        "action_hint": "Bullish absorption confirmed. Look for continuation confirmation."
                    }
                if dist_val <= proximity_dist:
                    return {
                        "state": "APPROACHING",
                        "badge_text": f"🎯 APPROACHING ({dist_val:.1f}{unit_abbr})",
                        "badge_class": "badge-approaching",
                        "status_text": f"Approaching {z_title} ({_fmt_p(z_low)} – {_fmt_p(z_high)}) · {dist_val:.1f} {unit_str} away. Prepare reaction plan.",
                        "pips_dist": round(dist_val, 1),
                        "action_hint": "Approaching support. Observe whether sellers exhaust or punch through."
                    }
                return {
                    "state": "IN_TRANSIT",
                    "badge_text": f"⏳ IN TRANSIT (-{dist_val:.1f}{unit_abbr})",
                    "badge_class": "badge-transit",
                    "status_text": f"Floor sitting -{dist_val:.1f} {unit_str} below current price ({_fmt_p(z_low)} – {_fmt_p(z_high)}).",
                    "pips_dist": round(dist_val, 1),
                    "action_hint": "Structure mapped. No immediate zone pressure."
                }
            else:
                break_val = (z_low - current_price) if is_gold else ((z_low - current_price) / pip_size)
                return {
                    "state": "BREAKOUT",
                    "badge_text": f"🚀 BROKE THROUGH (-{break_val:.1f}{unit_abbr})",
                    "badge_class": "badge-breakout",
                    "status_text": f"Breakthrough: Cleared below {z_title} ({_fmt_p(z_low)}). Watch for pullback & S/R flip retest.",
                    "pips_dist": round(-break_val, 1),
                    "action_hint": "Floor broken. Watch for weak retest from below."
                }

    def package_zone(z, zone_id: str, is_upper: bool) -> Dict[str, Any]:
        low = z.bottom
        high = z.top
        center = z.mid
        midline = z.mid
        diff = center - current_price

        if is_gold:
            pips_val = round(diff, 2)
            dist_label = f"{'+' if diff >= 0 else ''}{diff:.2f} pts"
        elif is_jpy:
            pips_val = round(diff * 100, 1)
            dist_label = f"{'+' if diff >= 0 else ''}{pips_val:.1f} pips"
        else:
            pips_val = round(diff * 10000, 1)
            dist_label = f"{'+' if diff >= 0 else ''}{pips_val:.1f} pips"

        is_immediate = "1" in zone_id
        tier_title = "IMMEDIATE" if is_immediate else "SECONDARY"
        direction_title = "UPPER" if is_upper else "LOWER"

        trade_info = zone_tradeability(z, htf_direction)

        if z.is_swap:
            sub = "🔁 SWAP ZONE · AWAITING PULLBACK"
        elif is_upper:
            sub = "ACTIVE INTRADAY SUPPLY SHELF" if is_immediate else "LIQUIDITY SWEEP CEILING"
        else:
            sub = "ACTIVE INTRADAY DEMAND SHELF" if is_immediate else "INTRADAY BASE / SWEEP FLOOR"

        if is_gold:
            height_str = f"{high - low:.2f} pts"
        elif is_jpy:
            height_str = f"{(high - low) * 100:.1f} pips"
        else:
            height_str = f"{(high - low) * 10000:.1f} pips"

        conf = [
            f"M15 {'Supply' if is_upper else 'Demand'} Shelf (Score: {z.score} · {z.grade})",
            f"Retest: {trade_info['label']}",
            f"50% CE: {_fmt_p(midline)}",
            f"Height: {height_str}"
        ]
        if z.swept_count > 0:
            conf.append(f"Swept {z.swept_count}x (liquidity defended)")
        conf.extend(z.confluences)

        z_title = f"{direction_title} DECISION ZONE {1 if is_immediate else 2}"
        interaction = evaluate_m15_interaction(low, high, is_upper, z_title, z.inside_now)

        return {
            "id": zone_id,
            "title": z_title,
            "subtitle": f"{tier_title} · {sub}",
            "center": round(center, 3 if (is_gold or is_jpy) else 5),
            "low": round(low, 3 if (is_gold or is_jpy) else 5),
            "high": round(high, 3 if (is_gold or is_jpy) else 5),
            "midline": round(midline, 3 if (is_gold or is_jpy) else 5),
            "distance_pips": pips_val,
            "distance_label": dist_label,
            "distance_atr": round(abs(diff) / unit, 2),
            "is_upper": is_upper,
            "is_flip": z.is_swap,
            "flip_note": "🔁 SWAP ZONE (awaiting pullback)" if z.is_swap else None,
            "confluences": conf,
            "playbook_reject": "Look for wick exhaustion & absorption inside zone to fade back toward mean." if is_upper else "Look for lower wick absorption & buying reaction to retest higher levels.",
            "playbook_break": "If candle body closes through zone, await pullback retest of broken level before continuation.",
            "interaction": interaction,
            "strength_grade": z.grade,
            "strength_score": z.score,
            "retest_count": z.retest_count,
            "next_test_label": trade_info["label"],
            "status": z.status,
            "role": z.role,
            "is_swap": z.is_swap,
            "swept_count": z.swept_count,
            "tradeability": trade_info,
        }

    upper_dz_1 = package_zone(slots["upper"][0], "upper_dz_1", True) if len(slots["upper"]) >= 1 else None
    upper_dz_2 = package_zone(slots["upper"][1], "upper_dz_2", True) if len(slots["upper"]) >= 2 else None
    lower_dz_1 = package_zone(slots["lower"][0], "lower_dz_1", False) if len(slots["lower"]) >= 1 else None
    lower_dz_2 = package_zone(slots["lower"][1], "lower_dz_2", False) if len(slots["lower"]) >= 2 else None

    # Package faded / exhausted zones for optional faded charting
    faded_zones = []
    for fz in slots.get("faded", []):
        t_info = zone_tradeability(fz, htf_direction)
        faded_zones.append({
            "low": round(fz.bottom, 3 if (is_gold or is_jpy) else 5),
            "high": round(fz.top, 3 if (is_gold or is_jpy) else 5),
            "midline": round(fz.mid, 3 if (is_gold or is_jpy) else 5),
            "role": fz.role,
            "status": fz.status,
            "strength_grade": fz.grade,
            "strength_score": fz.score,
            "retest_count": fz.retest_count,
            "label": t_info["label"],
            "is_swap": fz.is_swap
        })

    # Proximity radar determination
    zones_list = [z for z in [upper_dz_1, lower_dz_1, upper_dz_2, lower_dz_2] if z is not None]
    testing_zone = next((z for z in zones_list if z["interaction"]["state"] == "TESTING"), None)
    rejected_zone = next((z for z in zones_list if z["interaction"]["state"] == "REJECTED"), None)
    breakout_zone = next((z for z in zones_list if z["interaction"]["state"] == "BREAKOUT"), None)
    approaching_candidates = [z for z in zones_list if z["interaction"]["state"] == "APPROACHING"]
    approaching_zone = min(approaching_candidates, key=lambda z: abs(z["interaction"]["pips_dist"]), default=None)

    dist_to_u1 = ((upper_dz_1["low"] - current_price) / pip_size) if upper_dz_1 else None
    dist_to_l1 = ((current_price - lower_dz_1["high"]) / pip_size) if lower_dz_1 else None

    # Check tight range condition (Upper 1 & Lower 1 in tight squeeze)
    is_tight_range = False
    if upper_dz_1 and lower_dz_1:
        gap = upper_dz_1["low"] - lower_dz_1["high"]
        if gap < (p.get("range_gap", 1.2) * unit):
            is_tight_range = True

    if testing_zone:
        other_note = ""
        if not testing_zone["is_upper"] and dist_to_u1 is not None:
            other_note = f" · Nearest Upper DZ 1 is +{dist_to_u1:.1f}p away."
        elif testing_zone["is_upper"] and dist_to_l1 is not None:
            other_note = f" · Nearest Lower DZ 1 is -{dist_to_l1:.1f}p away."

        radar = {
            "state": "TESTING",
            "badge_text": "🔵 TESTING ZONE",
            "headline": f"Price is currently TESTING {testing_zone['title']} ({_fmt_p(testing_zone['low'])} – {_fmt_p(testing_zone['high'])}) on M15",
            "detail": f"{testing_zone['interaction']['status_text']}{other_note}",
            "badge_class": "badge-testing",
            "action_hint": testing_zone["interaction"]["action_hint"],
            "active_zone_id": testing_zone["id"],
            "pips_dist": 0.0
        }
    elif is_tight_range:
        gap_pips = (upper_dz_1["low"] - lower_dz_1["high"]) / pip_size
        gap = upper_dz_1["low"] - lower_dz_1["high"]
        radar = {
            "state": "RANGE",
            "badge_text": f"🔄 CONSOLIDATION RANGE ({gap_pips:.1f}p)",
            "headline": f"Trading inside tight range: Lower DZ 1 ({_fmt_p(lower_dz_1['high'])}) to Upper DZ 1 ({_fmt_p(upper_dz_1['low'])}) on M15",
            "detail": f"Range span is only {gap_pips:.1f} {unit_str} ({gap/unit:.1f}x unit). Await breakout expansion before entering.",
            "badge_class": "badge-transit",
            "action_hint": "Price is coiling in a narrow corridor. Do not anticipate — wait for confirmed boundary break.",
            "active_zone_id": None,
            "pips_dist": round(min(dist_to_u1, dist_to_l1), 1)
        }
    elif rejected_zone:
        radar = {
            "state": "REJECTED",
            "badge_text": "🔴 REJECTION CONFIRMED",
            "headline": f"Rejection Confirmed at {rejected_zone['title']} on M15",
            "detail": rejected_zone["interaction"]["status_text"],
            "badge_class": "badge-rejected",
            "action_hint": rejected_zone["interaction"]["action_hint"],
            "active_zone_id": rejected_zone["id"],
            "pips_dist": rejected_zone["interaction"]["pips_dist"]
        }
    elif breakout_zone:
        radar = {
            "state": "BREAKOUT",
            "badge_text": "🟢 BREAKTHROUGH",
            "headline": f"Breakthrough: {breakout_zone['title']} Cleared on M15",
            "detail": breakout_zone["interaction"]["status_text"],
            "badge_class": "badge-breakout",
            "action_hint": breakout_zone["interaction"]["action_hint"],
            "active_zone_id": breakout_zone["id"],
            "pips_dist": breakout_zone["interaction"]["pips_dist"]
        }
    elif approaching_zone:
        p_dist = abs(approaching_zone["interaction"]["pips_dist"])
        radar = {
            "state": "APPROACHING",
            "badge_text": f"🟡 APPROACHING ({p_dist:.1f}p)",
            "headline": f"Approaching {approaching_zone['title']} ({_fmt_p(approaching_zone['low'])} – {_fmt_p(approaching_zone['high'])}) · {p_dist:.1f} pips away",
            "detail": approaching_zone["interaction"]["status_text"],
            "badge_class": "badge-approaching",
            "action_hint": approaching_zone["interaction"]["action_hint"],
            "active_zone_id": approaching_zone["id"],
            "pips_dist": round(p_dist, 1)
        }
    else:
        # In transit or open range
        u_info = f"Upper DZ 1 sits +{dist_to_u1:.1f}p away ({_fmt_p(upper_dz_1['low'])})" if upper_dz_1 else "No overhead resistance within reach"
        l_info = f"Lower DZ 1 sits -{dist_to_l1:.1f}p away ({_fmt_p(lower_dz_1['high'])})" if lower_dz_1 else "No underlying demand within reach"

        valid_dists = [d for d in [dist_to_u1, dist_to_l1] if d is not None]
        radar = {
            "state": "IN_TRANSIT",
            "badge_text": "⚪ IN TRANSIT",
            "headline": f"Open Range on M15 · {u_info} · {l_info}",
            "detail": f"Cruising freely in open space. {u_info}. {l_info}.",
            "badge_class": "badge-transit",
            "action_hint": "Price is cruising freely between structural zones. Prepare reaction plan as boundary approaches.",
            "active_zone_id": None,
            "pips_dist": round(min(valid_dists) if valid_dists else 0.0, 1)
        }

    return {
        "timeframe": "M15",
        "current_price": current_price,
        "atr": round(unit, 3 if (is_gold or is_jpy) else 5),
        "radar": radar,
        "upper_dz_2": upper_dz_2,
        "upper_dz_1": upper_dz_1,
        "lower_dz_1": lower_dz_1,
        "lower_dz_2": lower_dz_2,
        "faded_zones": faded_zones,
    }


def calculate_all_decision_zones(
    df_m15: Optional[pd.DataFrame],
    df_h1: Optional[pd.DataFrame],
    df_h4: Optional[pd.DataFrame],
    current_price: float,
    levels: Dict[str, Any],
    symbol: str = "EURUSD",
    htf_direction: str = "NEUTRAL"
) -> Dict[str, Any]:
    """Calculate multi-timeframe Decision Zones (M15, H1, H4) anchored dynamically to current market price."""
    current_price = _safe_float(current_price)
    
    # Common macro anchors (clean horizontal S/R & session benchmarks for H1 & H4)
    macro_anchors = []
    if levels:
        dh = _safe_float(levels.get("daily_high"))
        dl = _safe_float(levels.get("daily_low"))
        th = _safe_float(levels.get("today_high"))
        tl = _safe_float(levels.get("today_low"))
        wo = _safe_float(levels.get("weekly_open"))
        do = _safe_float(levels.get("daily_open"))
        if dh > 0: macro_anchors.append({"price": dh, "type": "DAILY_HIGH", "label": "Previous Day High (PDH)", "bars_ago": 1})
        if dl > 0: macro_anchors.append({"price": dl, "type": "DAILY_LOW", "label": "Previous Day Low (PDL)", "bars_ago": 1})
        if th > 0 and abs(th - dh) > 0.0001: macro_anchors.append({"price": th, "type": "TODAY_HIGH", "label": "Today Session High", "bars_ago": 0})
        if tl > 0 and abs(tl - dl) > 0.0001: macro_anchors.append({"price": tl, "type": "TODAY_LOW", "label": "Today Session Low", "bars_ago": 0})
        if wo > 0: macro_anchors.append({"price": wo, "type": "WEEKLY_OPEN", "label": "Weekly Open (Macro Pivot)", "bars_ago": 5})
        if do > 0: macro_anchors.append({"price": do, "type": "DAILY_OPEN", "label": "Daily Open (Session Bias)", "bars_ago": 0})

    # M15 uses dedicated zone_engine with lifecycle, swap persistence, and tradeability rules
    htf_levels = htf_swing_levels(df_h1)
    m15_zones = calculate_m15_decision_zones(
        df_m15, current_price, symbol=symbol,
        anchors=macro_anchors, htf_levels=htf_levels, htf_direction=htf_direction
    )

    # H1 and H4 retain structural macro anchors
    h1_zones = calculate_tf_decision_zones(df_h1, current_price, "h1", extra_anchors=macro_anchors, symbol=symbol)
    h4_zones = calculate_tf_decision_zones(df_h4, current_price, "h4", extra_anchors=macro_anchors, symbol=symbol)

    # Calculate key benchmark levels proximity radar
    ref_atr = m15_zones.get("atr", 0.0020)
    key_levels_radar = calculate_key_levels_radar(current_price, levels, symbol=symbol, atr=ref_atr)

    return {
        "symbol": symbol,
        "current_price": current_price,
        "key_levels_radar": key_levels_radar,
        "m15": m15_zones,
        "h1": h1_zones,
        "h4": h4_zones,
    }


