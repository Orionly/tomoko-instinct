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
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.zone_engine import build_zones, select_slots, htf_swing_levels, TF_PARAMS
from core.decision_zones import calculate_all_decision_zones
from core.trader_dna_engine import purge_weekly_archive, load_trader_dna_profile


def load_trader_history(symbol: str = "XAUUSD") -> List[Dict[str, Any]]:
    data = load_trader_dna_profile()
    if not data or (not data.get("history") and not data.get("active_session")):
        return []

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


def run_weekend_comparison(symbol: str = "XAUUSD", verbose: bool = False, tune: bool = False, purge_archive: bool = False):
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

    # Zone DNA Aggregators
    anchor_counter = Counter()
    role_counter = Counter()
    box_heights_by_tf: Dict[str, List[float]] = {"M15": [], "H1": [], "H4": []}
    why_marked_log: List[str] = []

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

            # Collect Zone DNA
            box_h = tz.get("box_height_pts", abs(t_high - t_low))
            if tf in box_heights_by_tf:
                box_heights_by_tf[tf].append(box_h)
            
            anchor = tz.get("anchor_type")
            if anchor:
                anchor_counter[anchor] += 1
            role = tz.get("structural_role")
            if role:
                role_counter[role] += 1
            why = tz.get("why_marked")
            if why:
                why_marked_log.append(f"[{tf}] {t_name} ({t_type}): {why}")

            # Find best matching Tomoko zone
            best_match = None
            best_overlap = 0.0
            for mz in active_tomoko:
                ov = compute_overlap(t_low, t_high, mz.bottom, mz.top)
                if ov > best_overlap:
                    best_overlap = ov
                    best_match = mz

            why_disp = f" | Why: {why}" if why else ""
            if best_match and best_overlap >= 0.35:
                matched_zones += 1
                mid_diff = abs(best_match.mid - t_mid) * pip_scale
                ce_midline_errors.append(mid_diff)

                type_match = (t_type == getattr(best_match, "role", "RESISTANCE"))
                if type_match:
                    category_matches += 1

                print(f"  [MATCH] {t_name:<16} [{t_low:.2f} - {t_high:.2f}] (50% CE: {t_mid:.2f}){why_disp}")
                print(f"    -> Tomoko Match:   [{best_match.bottom:.2f} - {best_match.top:.2f}] (50% CE: {best_match.mid:.2f})")
                print(f"    -> Overlap: {best_overlap*100:.1f}% | 50% CE Discrepancy: {mid_diff:.2f} {unit_str} | Grade: {getattr(best_match, 'grade', 'N/A')}")
            else:
                # Discrepancy: Trader marked a zone that Tomoko did not isolate
                print(f"  [MISS ] {t_name:<16} [{t_low:.2f} - {t_high:.2f}] (50% CE: {t_mid:.2f}){why_disp}")
                print(f"    -> [CALIBRATION TARGET]: No overlapping Tomoko zone detected above 35% threshold.")

    # -------------------------------------------------------------
    # Executed Trade Replay & Entry Style Alignment
    # -------------------------------------------------------------
    profile_data = load_trader_dna_profile()
    executed_trades = [t for t in profile_data.get("executed_trades", []) if t.get("symbol", "").upper() == symbol.upper()]
    if executed_trades:
        print("\n" + "=" * 76)
        print(f"  EXECUTED TRADE REPLAY & ENTRY STYLE ALIGNMENT ({len(executed_trades)} trade(s))")
        print("=" * 76)
        res_m15 = build_zones(df_m15, timeframe="M15", symbol=symbol)
        all_m15_zones = res_m15.get("zones", [])

        for t_idx, trade in enumerate(executed_trades, 1):
            t_dir = trade.get("direction", "LONG")
            t_entry = float(trade.get("entry_price", 0.0))
            t_sl = float(trade.get("stop_loss", 0.0))
            t_tp = float(trade.get("take_profit", 0.0))
            t_rr = trade.get("risk_reward", 0.0)
            t_pat = trade.get("pattern_type", "N/A")
            t_why = trade.get("why_entered") or trade.get("invalidation_logic", "")

            best_entry_zone = None
            min_entry_dist = float("inf")
            for mz in all_m15_zones:
                dist = abs(mz.mid - t_entry)
                if dist < min_entry_dist:
                    min_entry_dist = dist
                    best_entry_zone = mz

            match_status = "[CONFLUENT]" if min_entry_dist <= 4.0 else "[CALIBRATION TARGET]"
            print(f"\n  {match_status} Trade #{t_idx}: {symbol} {t_dir} at {t_entry:.2f} (SL: {t_sl:.2f}, TP: {t_tp:.2f}, R:R {t_rr})")
            print(f"      Setup Model: {t_pat}")
            if t_why:
                print(f"      Execution Rationale: {t_why}")
            if best_entry_zone and min_entry_dist <= 4.0:
                print(f"      -> Confluent Algorithmic Shelf: [{best_entry_zone.bottom:.2f} - {best_entry_zone.top:.2f}] (50% CE: {best_entry_zone.mid:.2f}, Delta: {min_entry_dist:.2f} {unit_str})")
            else:
                print(f"      -> Discrepancy: Closest Tomoko zone was {min_entry_dist:.2f} {unit_str} away. Check if displacement threshold was satisfied.")

    # -------------------------------------------------------------
    # Weekend Scorecard Summary
    # -------------------------------------------------------------
    print("\n" + "=" * 76)
    print("  WEEKEND ACCURACY SCORECARD & ZONE DNA ANALYSIS")
    print("=" * 76)

    recall_pct = (matched_zones / total_trader_zones * 100) if total_trader_zones > 0 else 0.0
    avg_ce_error = (sum(ce_midline_errors) / len(ce_midline_errors)) if ce_midline_errors else 0.0
    cat_pct = (category_matches / matched_zones * 100) if matched_zones > 0 else 0.0

    print(f"  * Total Trader Zones Audited:         {total_trader_zones}")
    print(f"  * Tomoko Confluent Matches:          {matched_zones} ({recall_pct:.1f}%)")
    print(f"  * Role Classification Accuracy:      {category_matches}/{matched_zones} ({cat_pct:.1f}%)")
    print(f"  * Average 50% CE Midline Offset:     {avg_ce_error:.2f} {unit_str}")

    # Zone DNA Profile Output
    print("\n  [+] Ingested Trader DNA Profile (Empirical Ground Truth):")
    if anchor_counter:
        print("      * Anchor Types:")
        for anc, cnt in anchor_counter.most_common():
            pct = (cnt / sum(anchor_counter.values())) * 100
            print(f"        - {anc:<24}: {cnt} ({pct:.1f}%)")
    if role_counter:
        print("      * Structural Roles:")
        for rle, cnt in role_counter.most_common():
            pct = (cnt / sum(role_counter.values())) * 100
            print(f"        - {rle:<24}: {cnt} ({pct:.1f}%)")
    
    print("      * Average Box Thickness:")
    for tf_key in ["M15", "H1", "H4"]:
        arr = box_heights_by_tf.get(tf_key, [])
        if arr:
            avg_h = sum(arr) / len(arr)
            print(f"        - {tf_key}: {avg_h:.2f} {unit_str} (min {min(arr):.2f}, max {max(arr):.2f})")
        else:
            print(f"        - {tf_key}: N/A (no snapshots logged)")

    # -------------------------------------------------------------
    # Algorithmic Calibration Recommendations (--tune)
    # -------------------------------------------------------------
    if tune:
        print("\n" + "=" * 76)
        print("  RECOMMENDED ALGORITHMIC CALIBRATIONS (core/zone_engine.py)")
        print("=" * 76)
        
        current_m15 = TF_PARAMS.get("M15", {})
        m15_heights = box_heights_by_tf.get("M15", [])
        
        if m15_heights:
            avg_m15_h = sum(m15_heights) / len(m15_heights)
            print(f"  [1] Box Height Clamp Calibration:")
            print(f"      Current M15 Range: [{current_m15.get('min_height', 0.9)} - {current_m15.get('max_height', 2.6)}] units")
            print(f"      Trader Empirical Avg: {avg_m15_h:.2f} {unit_str}")
        
        if anchor_counter:
            top_anchor, top_count = anchor_counter.most_common(1)[0]
            print(f"  [2] Anchor Bias Alignment:")
            print(f"      Dominant Trader Anchor: {top_anchor} ({top_count} occurrences)")
            if top_anchor == "WICK_CLUSTER":
                print("      -> Ensure `cluster_bars` >= 4 and `cluster_tol` is tuned to tighten wick clusters.")
            elif top_anchor == "BODY_EXTREME":
                print("      -> Favor candle body closes over extreme wick tips in anchor selection.")
            elif top_anchor == "ORDER_BLOCK":
                print("      -> Prioritize origin candle before high-displacement legs.")

        if recall_pct < 70.0:
            print(f"  [3] Zone Selection Tuning:")
            print(f"      Alignment is {recall_pct:.1f}%. Consider relaxing `min_departure` from {current_m15.get('min_departure', 2.0)} to 1.5")
            print("      to capture secondary consolidation bases marked by the trader.")
        else:
            print(f"  [3] Zone Selection Tuning: High alignment ({recall_pct:.1f}%). Current `TF_PARAMS` are well-calibrated.")

    print("\n  [i] Ingested numerical markups remain safely stored in `trader_dna/data/trader_dna_profile.json`.")
    print("      Live Tomoko trading engines continue operating 100% autonomously.")

    # -------------------------------------------------------------
    # Post-Tune Weekly Archive Purge
    # -------------------------------------------------------------
    if purge_archive:
        print("\n" + "=" * 76)
        print("  WEEKLY ARCHIVE PURGE EXECUTED")
        print("=" * 76)
        purged = purge_weekly_archive()
        print(f"  Successfully purged {purged} weekly screenshot files from archive.")
        print("  `markups/` and `markups/archive/` are clean for the upcoming trading week.")

    print("=" * 76 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tomoko Weekend Comparison & Fine-Tuning CLI")
    parser.add_argument("--symbol", default="XAUUSD", help="Symbol to compare (default: XAUUSD)")
    parser.add_argument("--verbose", action="store_true", help="Verbose telemetry")
    parser.add_argument("--tune", action="store_true", help="Generate algorithmic parameter calibrations for core/zone_engine.py")
    parser.add_argument("--purge-archive", action="store_true", help="Purge the weekly raw screenshot images after running fine-tuning comparison")
    args = parser.parse_args()
    run_weekend_comparison(args.symbol, args.verbose, tune=args.tune, purge_archive=args.purge_archive)
