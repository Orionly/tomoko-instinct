"""
Tomoko Instinct — Weekend Comparison & ML Fine-Tuning CLI
--------------------------------------------------------
Compares the human trader's hand-drawn TradingView markups stored across
`data/trader_profile.json` (/ingest) and `trader_dna/data/trader_dna_profile.json` (/dna)
against Tomoko's autonomous zone engine (`core.zone_engine` and `core.decision_zones`).

Usage:
    python scripts/weekend_comparison.py [--symbol XAUUSD] [--tune] [--trials 160] [--dry-run] [--reset-defaults]
"""

import os
import sys
import pickle
import argparse
from typing import Dict, Any, List
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.zone_engine import (
    DEFAULT_TF_PARAMS,
    TUNED_PARAMS_PATH,
    build_zones,
    get_tf_params,
    htf_swing_levels,
    select_slots,
)
from core.decision_zones import DEFAULT_HTF_PARAMS, calculate_tf_decision_zones
from core.trader_dna_engine import purge_weekly_archive
from core.dna_ml_tuner import (
    compute_overlap,
    load_unified_snapshots,
    reset_tuned_params,
    run_ml_calibration,
    slice_candles_at_timestamp,
)


def load_candles_cache():
    cache_path = os.path.join(ROOT, "logs", "candles_cache.pkl")
    if not os.path.exists(cache_path):
        print(f"[!] Candles cache not found at {cache_path}")
        return {}
    with open(cache_path, "rb") as f:
        data = pickle.load(f)
    return data.get("candles", {})


def _print_param_diffs(m15_tuned: Dict[str, float], htf_tuned: Dict[str, Dict[str, float]]):
    base_m15 = DEFAULT_TF_PARAMS["M15"]
    diffs = []
    for k, base_v in base_m15.items():
        new_v = m15_tuned.get(k, base_v)
        if abs(float(new_v) - float(base_v)) > 1e-6:
            diffs.append((f"M15.{k}", base_v, new_v))
    for tf in ("H1", "H4"):
        base_h = DEFAULT_HTF_PARAMS[tf]
        cur_h = htf_tuned.get(tf, base_h)
        for k, base_v in base_h.items():
            new_v = cur_h.get(k, base_v)
            if abs(float(new_v) - float(base_v)) > 1e-6:
                diffs.append((f"{tf}.{k}", base_v, new_v))

    if not diffs:
        print("  [*] Parameter Calibration: Baseline defaults already optimal for current snapshots.")
    else:
        print("  [*] Parameter Calibrations Applied (Baseline -> Tuned):")
        for name, old_v, new_v in diffs:
            print(f"      - {name:<22}: {old_v} -> {new_v}")


def run_weekend_comparison(
    symbol: str = "XAUUSD",
    verbose: bool = False,
    tune: bool = False,
    purge_archive: bool = False,
    n_trials: int = 160,
    dry_run: bool = False,
):
    print("=" * 76)
    print(f"  TOMOKO INSTINCT // WEEKEND COMPARISON & ML FINE-TUNING SUITE [{symbol}]")
    print("=" * 76)

    snapshots, executed_trades = load_unified_snapshots(symbol)
    if not snapshots and not executed_trades:
        print(f"[i] No ingested trader markups found for {symbol}.")
        print("    Ingest chart snapshots using `/ingest` or `/dna` during the week to build training data.")
        return

    print(f"[*] Loaded {len(snapshots)} unified benchmark snapshot(s) + {len(executed_trades)} trade(s) for {symbol}.\n")

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
    skipped_out_of_window = 0
    matched_zones = 0
    ce_midline_errors = []
    category_matches = 0

    anchor_counter = Counter()
    role_counter = Counter()
    box_heights_by_tf: Dict[str, List[float]] = {"M15": [], "H1": [], "H4": [], "M5": []}
    why_marked_log: List[str] = []

    for idx, snap in enumerate(snapshots, 1):
        tf = snap.get("timeframe", "M15").upper()
        snap_time = snap.get("file_timestamp") or snap.get("chart_date") or f"Snapshot #{idx}"
        utc_ts = snap.get("_utc_ts")
        trader_zones = snap.get("zones", [])

        # Collect Zone DNA characteristics regardless of cache window
        for tz in trader_zones:
            t_high = float(tz.get("high", 0))
            t_low = float(tz.get("low", 0))
            box_h = tz.get("box_height_pts", abs(t_high - t_low))
            if tf in box_heights_by_tf:
                box_heights_by_tf[tf].append(box_h)
            if tz.get("anchor_type"):
                anchor_counter[tz["anchor_type"]] += 1
            if tz.get("structural_role"):
                role_counter[tz["structural_role"]] += 1
            if tz.get("why_marked"):
                why_marked_log.append(f"[{tf}] {tz.get('name', 'Zone')} ({tz.get('type', '')}): {tz['why_marked']}")

        # Point-in-time candle slicing (no future lookahead)
        if tf == "M15":
            cand_df, in_window = slice_candles_at_timestamp(df_m15, utc_ts)
            h1_slice, _ = slice_candles_at_timestamp(df_h1, utc_ts, min_bars=15)
        elif tf == "H1":
            cand_df, in_window = slice_candles_at_timestamp(df_h1, utc_ts)
            h1_slice = cand_df
        elif tf == "H4":
            cand_df, in_window = slice_candles_at_timestamp(df_h4, utc_ts)
            h1_slice = None
        else:
            cand_df, in_window = None, False
            h1_slice = None

        if not in_window or cand_df is None:
            skipped_out_of_window += len(trader_zones)
            print(f"\n[{idx}/{len(snapshots)}] Benchmark: {symbol} {tf} ({snap_time}) - [OUT OF CACHE WINDOW]")
            print("-" * 76)
            print(f"  [SKIP] Candle cache does not cover {tf} at {snap_time} (retained for DNA stats only).")
            continue

        live_p = float(snap.get("live_price_on_chart") or cand_df["close"].iloc[-1])
        print(f"\n[{idx}/{len(snapshots)}] Benchmark: {symbol} {tf} ({snap_time}) - Ref Price: {live_p:.2f}")
        print("-" * 76)

        if tf == "M15":
            htf_lvls = htf_swing_levels(h1_slice) if h1_slice is not None else []
            res = build_zones(cand_df, timeframe="M15", symbol=symbol, htf_levels=htf_lvls)
            tomoko_slots = select_slots(res["zones"], live_p, res["unit"], res["params"], symbol=symbol)
            active_tomoko = tomoko_slots["upper"] + tomoko_slots["lower"]
        else:
            tf_dz = calculate_tf_decision_zones(cand_df, live_p, tf.lower(), symbol=symbol)
            active_tomoko = []
            for k in ["upper_dz_1", "upper_dz_2", "lower_dz_1", "lower_dz_2"]:
                if tf_dz.get(k):
                    z = tf_dz[k]
                    active_tomoko.append(type("ZoneObj", (), {
                        "top": z["high"], "bottom": z["low"],
                        "mid": z.get("midline", (z["high"] + z["low"]) / 2),
                        "role": "RESISTANCE" if z.get("is_upper") else "SUPPORT",
                        "grade": z.get("strength_grade", "HTF_STRUCTURAL"),
                    })())

        print(f"  Trader Hand-Drawn Zones: {len(trader_zones)}  |  Tomoko Algorithmic Zones: {len(active_tomoko)}")

        for tz in trader_zones:
            total_trader_zones += 1
            t_high = float(tz.get("high", 0))
            t_low = float(tz.get("low", 0))
            t_mid = float(tz.get("midline", (t_high + t_low) / 2))
            t_type = tz.get("type", "RESISTANCE").upper()
            t_name = tz.get("name", "Zone")
            why = tz.get("why_marked")

            best_match = None
            best_overlap = 0.0
            for mz in active_tomoko:
                ov = compute_overlap(t_low, t_high, mz.bottom, mz.top)
                if ov > best_overlap:
                    best_overlap = ov
                    best_match = mz

            why_disp = f" | Why: {why}" if (why and verbose) else ""
            if best_match and best_overlap >= 0.35:
                matched_zones += 1
                mid_diff = abs(best_match.mid - t_mid) * pip_scale
                ce_midline_errors.append(mid_diff)
                if t_type == getattr(best_match, "role", "RESISTANCE"):
                    category_matches += 1
                print(f"  [MATCH] {t_name:<16} [{t_low:.2f} - {t_high:.2f}] (50% CE: {t_mid:.2f}){why_disp}")
                print(f"    -> Tomoko Match:   [{best_match.bottom:.2f} - {best_match.top:.2f}] (50% CE: {best_match.mid:.2f})")
                print(f"    -> Overlap: {best_overlap*100:.1f}% | 50% CE Discrepancy: {mid_diff:.2f} {unit_str} | Grade: {getattr(best_match, 'grade', 'N/A')}")
            else:
                print(f"  [MISS ] {t_name:<16} [{t_low:.2f} - {t_high:.2f}] (50% CE: {t_mid:.2f}){why_disp}")
                print("    -> [CALIBRATION TARGET]: No overlapping Tomoko zone detected above 35% threshold.")

    # -------------------------------------------------------------
    # Executed Trade Replay & Entry Style Alignment
    # -------------------------------------------------------------
    if executed_trades:
        print("\n" + "=" * 76)
        print(f"  EXECUTED TRADE REPLAY & ENTRY STYLE ALIGNMENT ({len(executed_trades)} trade(s))")
        print("=" * 76)
        for t_idx, trade in enumerate(executed_trades, 1):
            t_dir = trade.get("direction", "LONG")
            t_entry = float(trade.get("entry_price", 0.0))
            t_sl = float(trade.get("stop_loss", 0.0))
            t_tp = float(trade.get("take_profit", 0.0))
            t_rr = trade.get("risk_reward", 0.0)
            t_pat = trade.get("pattern_type", "N/A")
            t_why = trade.get("why_entered") or trade.get("invalidation_logic", "")

            res_m15 = build_zones(df_m15, timeframe="M15", symbol=symbol)
            all_m15_zones = res_m15.get("zones", [])
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
                print(f"      -> Discrepancy: Closest Tomoko zone was {min_entry_dist:.2f} {unit_str} away.")

    # -------------------------------------------------------------
    # Weekend Scorecard Summary
    # -------------------------------------------------------------
    print("\n" + "=" * 76)
    print("  WEEKEND ACCURACY SCORECARD & ZONE DNA ANALYSIS")
    print("=" * 76)

    recall_pct = (matched_zones / total_trader_zones * 100) if total_trader_zones > 0 else 0.0
    avg_ce_error = (sum(ce_midline_errors) / len(ce_midline_errors)) if ce_midline_errors else 0.0
    cat_pct = (category_matches / matched_zones * 100) if matched_zones > 0 else 0.0

    print(f"  * In-Window Trader Zones Audited:    {total_trader_zones} (skipped out-of-window: {skipped_out_of_window})")
    print(f"  * Tomoko Confluent Matches:          {matched_zones} ({recall_pct:.1f}%)")
    print(f"  * Role Classification Accuracy:      {category_matches}/{matched_zones} ({cat_pct:.1f}%)")
    print(f"  * Average 50% CE Midline Offset:     {avg_ce_error:.2f} {unit_str}")

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
    # Offline ML Calibration (--tune)
    # -------------------------------------------------------------
    if tune:
        print("\n" + "=" * 76)
        print(f"  ML BAYESIAN & DNA CALIBRATION ({n_trials} Optuna trials){' [DRY RUN]' if dry_run else ''}")
        print("=" * 76)

        cal = run_ml_calibration(
            snapshots=snapshots,
            df_m15=df_m15,
            df_h1=df_h1,
            df_h4=df_h4,
            symbol=symbol,
            n_trials=n_trials,
            dry_run=dry_run,
        )
        b = cal["baseline_metrics"]
        t = cal["tuned_metrics"]
        print(f"  * ML Stage        : {cal['ml_stage']}")
        print(f"  * Progression     : {cal['ml_status_note']}")
        print(f"  * Covered Window  : {cal['snapshots_in_cache_window']}/{cal['snapshots_total']} snapshots")
        print("\n  [Before vs. After Scorecard]")
        print(f"      - Match Recall   : {b['recall_pct']:.1f}% ({b['matched_zones']}/{b['total_zones']})  -->  {t['recall_pct']:.1f}% ({t['matched_zones']}/{t['total_zones']})")
        print(f"      - Mean Zone IoU  : {b['mean_iou']:.3f}  -->  {t['mean_iou']:.3f}")
        print(f"      - 50% CE Offset  : {b['avg_ce_error']:.2f} {unit_str}  -->  {t['avg_ce_error']:.2f} {unit_str}")
        print("")
        _print_param_diffs(cal["m15_params"], cal["htf_params"])
        if dry_run:
            print("\n  [DRY RUN] No files written. Re-run without `--dry-run` to apply calibration.")
        else:
            print(f"\n  [SAVED] Calibrated weights persisted to `{TUNED_PARAMS_PATH}`.")
            print("          Daily live scans will load these tuned parameters automatically.")

    print("\n  [i] Ingested numerical markups remain safely stored offline.")
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tomoko Weekend Benchmark & ML Calibration")
    parser.add_argument("--symbol", default="XAUUSD", help="Symbol to compare (default: XAUUSD)")
    parser.add_argument("--verbose", action="store_true", help="Verbose telemetry")
    parser.add_argument("--tune", action="store_true", help="Run Optuna/ML parameter calibration")
    parser.add_argument("--trials", type=int, default=160, help="Number of Optuna trials per timeframe (default: 160)")
    parser.add_argument("--dry-run", action="store_true", help="Evaluate ML tuning without saving tuned_zone_params.json")
    parser.add_argument("--reset-defaults", action="store_true", help="Delete tuned_zone_params.json and restore factory defaults")
    parser.add_argument("--purge-archive", action="store_true", help="Purge the weekly raw screenshot images after running fine-tuning comparison")
    args = parser.parse_args()

    if args.reset_defaults:
        removed = reset_tuned_params()
        print("[RESET] Removed calibrated ML parameters; factory defaults active." if removed else "[RESET] Already using factory defaults.")
    else:
        run_weekend_comparison(
            args.symbol,
            args.verbose,
            tune=args.tune,
            purge_archive=args.purge_archive,
            n_trials=args.trials,
            dry_run=args.dry_run,
        )
