"""
Tomoko Instinct — Weekend Comparison & Model Fine-Tuning CLI
-----------------------------------------------------------
Compares the human trader's hand-drawn TradingView markups stored in
`data/trader_profile.json` against Tomoko's autonomous zone engine
(`core.zone_engine` and `core.decision_zones`).

Usage:
    python scripts/weekend_comparison.py [--symbol XAUUSD] [--verbose]
"""

import os
import sys
import json
import pickle
import argparse
from datetime import datetime
from typing import Dict, Any, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.zone_engine import build_zones, select_slots, htf_swing_levels
from core.decision_zones import calculate_all_decision_zones


def load_trader_history(symbol: str = "XAUUSD") -> List[Dict[str, Any]]:
    profile_path = os.path.join(ROOT, "data", "trader_profile.json")
    if not os.path.exists(profile_path):
        print(f"[!] Trader profile not found at {profile_path}")
        return []

    with open(profile_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    snapshots = []
    # 1. Historical entries
    for entry in data.get("history", []):
        if entry.get("symbol", "").upper() == symbol.upper():
            snapshots.append(entry)

    # 2. Active session entries
    active = data.get("active_session", {})
    for tf_key, tf_sess in active.items():
        if tf_sess.get("symbol", "").upper() == symbol.upper():
            snapshots.append(tf_sess)

    # Deduplicate by file_timestamp
    seen = set()
    deduped = []
    for s in snapshots:
        ts = s.get("file_timestamp") or f"{s.get('chart_date')}_{s.get('session')}_{s.get('timeframe')}"
        if ts not in seen:
            seen.add(ts)
            deduped.append(s)

    return deduped


def load_candles_cache():
    cache_path = os.path.join(ROOT, "logs", "candles_cache.pkl")
    if not os.path.exists(cache_path):
        print(f"[!] Candles cache not found at {cache_path}")
        return {}
    with open(cache_path, "rb") as f:
        data = pickle.load(f)
    return data.get("candles", {})


def compute_overlap(z1_low: float, z1_high: float, z2_low: float, z2_high: float) -> float:
    """Returns overlap fraction relative to the smaller zone height."""
    inter_low = max(z1_low, z2_low)
    inter_high = min(z1_high, z2_high)
    if inter_high <= inter_low:
        return 0.0
    inter_len = inter_high - inter_low
    min_len = min(z1_high - z1_low, z2_high - z2_low)
    if min_len <= 0:
        return 0.0
    return inter_len / min_len


def run_weekend_comparison(symbol: str = "XAUUSD", verbose: bool = False):
    print("=" * 76)
    print(f"  TOMOKO INSTINCT // WEEKEND COMPARISON & FINE-TUNING SUITE [{symbol}]")
    print("=" * 76)

    snapshots = load_trader_history(symbol)
    if not snapshots:
        print(f"[i] No ingested trader markups found for {symbol} in data/trader_profile.json.")
        print("    Ingest chart snapshots using `/ingest` during the week to build training data.")
        return

    print(f"[*] Loaded {len(snapshots)} human benchmark snapshot(s) for {symbol}.\n")

    candles_data = load_candles_cache()
    sym_candles = candles_data.get(symbol, {})
    df_m15 = sym_candles.get("M15")
    df_h1 = sym_candles.get("H1")
    df_h4 = sym_candles.get("H4")

    if df_m15 is None or df_m15.empty:
        print(f"[!] No M15 cached candles available for {symbol}.")
        return

    is_gold = "XAU" in symbol
    is_jpy = "JPY" in symbol
    unit_str = "pts" if is_gold else "pips"
    pip_scale = 1.0 if is_gold else (100.0 if is_jpy else 10000.0)

    total_trader_zones = 0
    matched_zones = 0
    ce_midline_errors = []
    category_matches = 0

    for idx, snap in enumerate(snapshots, 1):
        tf = snap.get("timeframe", "M15").upper()
        snap_time = snap.get("file_timestamp") or snap.get("chart_date") or f"Snapshot #{idx}"
        live_p = snap.get("live_price_on_chart") or (float(df_m15["close"].iloc[-1]) if not df_m15.empty else 0.0)
        trader_zones = snap.get("zones", [])

        print(f"\n[{idx}/{len(snapshots)}] Benchmark: {symbol} {tf} ({snap_time}) - Ref Price: {live_p:.2f}")
        print("-" * 76)

        # Slice candles up to snapshot time if possible, or use full series
        if tf == "M15":
            cand_df = df_m15
            res = build_zones(cand_df, timeframe="M15", symbol=symbol)
            tomoko_slots = select_slots(res["zones"], live_p, res["unit"], res["params"])
            active_tomoko = tomoko_slots["upper"] + tomoko_slots["lower"]
        else:
            cand_df = df_h1 if tf == "H1" else df_h4
            if cand_df is None or cand_df.empty:
                cand_df = df_m15
            dz_all = calculate_all_decision_zones(df_m15, df_h1, df_h4, live_p, {}, symbol=symbol)
            tf_dz = dz_all.get(tf.lower(), {})
            active_tomoko = []
            for k in ["upper_dz_1", "upper_dz_2", "lower_dz_1", "lower_dz_2"]:
                if tf_dz.get(k):
                    z = tf_dz[k]
                    active_tomoko.append(type("ZoneObj", (), {
                        "top": z["high"], "bottom": z["low"],
                        "mid": z.get("midline", (z["high"] + z["low"]) / 2),
                        "role": "RESISTANCE" if z.get("is_upper") else "SUPPORT",
                        "grade": "HTF_STRUCTURAL", "score": 80
                    })())

        print(f"  Trader Hand-Drawn Zones: {len(trader_zones)}  |  Tomoko Algorithmic Zones: {len(active_tomoko)}")

        for tz in trader_zones:
            total_trader_zones += 1
            t_high = float(tz.get("high", 0))
            t_low = float(tz.get("low", 0))
            t_mid = float(tz.get("midline", (t_high + t_low) / 2))
            t_type = tz.get("type", "RESISTANCE").upper()
            t_name = tz.get("name", "Zone")

            # Find best matching Tomoko zone
            best_match = None
            best_overlap = 0.0
            for mz in active_tomoko:
                ov = compute_overlap(t_low, t_high, mz.bottom, mz.top)
                if ov > best_overlap:
                    best_overlap = ov
                    best_match = mz

            if best_match and best_overlap >= 0.35:
                matched_zones += 1
                mid_diff = abs(best_match.mid - t_mid) * pip_scale
                ce_midline_errors.append(mid_diff)

                type_match = (t_type == getattr(best_match, "role", "RESISTANCE"))
                if type_match:
                    category_matches += 1

                print(f"  [MATCH] {t_name:<16} [{t_low:.2f} - {t_high:.2f}] (50% CE: {t_mid:.2f})")
                print(f"    -> Tomoko Match:   [{best_match.bottom:.2f} - {best_match.top:.2f}] (50% CE: {best_match.mid:.2f})")
                print(f"    -> Overlap: {best_overlap*100:.1f}% | 50% CE Discrepancy: {mid_diff:.2f} {unit_str} | Grade: {getattr(best_match, 'grade', 'N/A')}")
            else:
                # Discrepancy: Trader marked a zone that Tomoko did not isolate
                print(f"  [MISS ] {t_name:<16} [{t_low:.2f} - {t_high:.2f}] (50% CE: {t_mid:.2f})")
                print(f"    -> [CALIBRATION TARGET]: No overlapping Tomoko zone detected above 35% threshold.")

    # -------------------------------------------------------------
    # Weekend Scorecard Summary
    # -------------------------------------------------------------
    print("\n" + "=" * 76)
    print("  WEEKEND ACCURACY SCORECARD & FINE-TUNING RECOMMENDATIONS")
    print("=" * 76)

    recall_pct = (matched_zones / total_trader_zones * 100) if total_trader_zones > 0 else 0.0
    avg_ce_error = (sum(ce_midline_errors) / len(ce_midline_errors)) if ce_midline_errors else 0.0
    cat_pct = (category_matches / matched_zones * 100) if matched_zones > 0 else 0.0

    print(f"  * Total Trader Zones Audited:         {total_trader_zones}")
    print(f"  * Tomoko Confluent Matches:          {matched_zones} ({recall_pct:.1f}%)")
    print(f"  * Role Classification Accuracy:      {category_matches}/{matched_zones} ({cat_pct:.1f}%)")
    print(f"  * Average 50% CE Midline Offset:     {avg_ce_error:.2f} {unit_str}")

    print("\n  [+] Calibration & Tuning Insights:")
    if recall_pct >= 75.0:
        print("      [HIGH ALIGNMENT (>75%)]: Autonomous engine faithfully replicates institutional shelves.")
    elif recall_pct >= 50.0:
        print("      [MODERATE ALIGNMENT (50-75%)]: Engine captures major swings; review cluster_atr tolerance.")
    else:
        print("      [LOW ALIGNMENT (<50%)]: Consider lowering min_displacement_atr or expanding shelf lookback.")

    if avg_ce_error <= 3.0:
        print(f"      [ACCURATE 50% CE]: Consequent Encroachment midline accuracy is institutional grade (<= 3.0 {unit_str}).")
    else:
        print(f"      [CE OFFSET ({avg_ce_error:.2f} {unit_str})]: Wick-to-body cluster anchors should be tightened.")

    print("\n  [i] Ingested markups remain safely stored in `data/trader_profile.json`.")
    print("      Live Tomoko trading engines continue operating 100% autonomously.")
    print("=" * 76 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tomoko Weekend Comparison & Fine-Tuning CLI")
    parser.add_argument("--symbol", default="XAUUSD", help="Symbol to compare (default: XAUUSD)")
    parser.add_argument("--verbose", action="store_true", help="Verbose telemetry")
    args = parser.parse_args()
    run_weekend_comparison(args.symbol, args.verbose)
