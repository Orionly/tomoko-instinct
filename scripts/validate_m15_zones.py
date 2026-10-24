# Tomoko Instinct - M15 Zone Validation Script
# Compares algorithmic zones from core.zone_engine against hand-drawn trader markups
# saved in data/trader_profile.json.

import os
import sys
import json
import pickle
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.zone_engine import build_zones, select_slots, zone_tradeability, htf_swing_levels


def load_candles():
    cache_path = os.path.join(ROOT, "logs", "candles_cache.pkl")
    if not os.path.exists(cache_path):
        print(f"Candles cache not found at {cache_path}")
        return None, None
    with open(cache_path, "rb") as f:
        data = pickle.load(f)
    candles = data.get("candles", {})
    df_m15 = candles.get("XAUUSD", {}).get("M15")
    df_h1 = candles.get("XAUUSD", {}).get("H1")
    return df_m15, df_h1


def load_profile_m15_snapshots():
    profile_path = os.path.join(ROOT, "data", "trader_profile.json")
    if not os.path.exists(profile_path):
        print(f"Profile not found at {profile_path}")
        return []
    with open(profile_path, "r", encoding="utf-8") as f:
        profile = json.load(f)

    snapshots = []
    # Check history
    for entry in profile.get("history", []):
        if entry.get("timeframe") == "M15" and entry.get("symbol") == "XAUUSD":
            snapshots.append(entry)
    # Check active_session
    active = profile.get("active_session", {}).get("M15")
    if active and active not in snapshots:
        snapshots.append(active)

    # Deduplicate by file_timestamp
    seen = set()
    unique = []
    for s in snapshots:
        ts = s.get("file_timestamp")
        if ts not in seen:
            seen.add(ts)
            unique.append(s)
    return unique


def parse_timestamp(ts_str):
    # e.g. "2026-10-21 14:02:28 MYT" -> Timestamp
    clean = ts_str.replace(" MYT", "").strip()
    return pd.Timestamp(clean, tz="Asia/Kuala_Lumpur")


def run_validation():
    df_m15_all, df_h1_all = load_candles()
    if df_m15_all is None:
        return

    snapshots = load_profile_m15_snapshots()
    print(f"Loaded {len(snapshots)} M15 trader markup snapshots.\n" + "=" * 80)

    for snap in snapshots:
        ts_str = snap.get("file_timestamp", "")
        ts = parse_timestamp(ts_str)
        chart_price = snap.get("live_price_on_chart")
        trader_zones = snap.get("zones", [])

        # Slice historical data up to this timestamp
        sub_m15 = df_m15_all[df_m15_all.index <= ts].copy()
        sub_h1 = df_h1_all[df_h1_all.index <= ts].copy() if df_h1_all is not None else None

        if len(sub_m15) < 30:
            print(f"Skipping {ts_str} (insufficient candles: {len(sub_m15)})")
            continue

        price = chart_price if chart_price else float(sub_m15["close"].iloc[-1])
        htf_levels = htf_swing_levels(sub_h1)

        result = build_zones(sub_m15, timeframe="M15", symbol="XAUUSD", htf_levels=htf_levels)
        slots = select_slots(result["zones"], price, result["unit"], result["params"])

        print(f"\nSNAPSHOT: {ts_str} | Live Price: {price:.2f} | Unit (TR): {result['unit']:.2f}")
        print("-" * 80)
        print("HAND-DRAWN TRADER ZONES:")
        for tz in trader_zones:
            name = tz.get("name", "Zone")
            z_type = tz.get("type", "")
            lo = tz.get("low", 0.0)
            hi = tz.get("high", 0.0)
            mid = tz.get("midline", (lo + hi) / 2.0)
            h = hi - lo
            print(f"  * {name:<16} [{z_type:<10}] {lo:.2f} - {hi:.2f} (mid: {mid:.2f}, h: {h:.1f}) | {tz.get('notes', '')}")

        print("\nALGORITHMIC DECISION ZONES (zone_engine):")
        all_selected = []
        for i, z in enumerate(slots.get("upper", []), 1):
            t_info = zone_tradeability(z, "UP")
            all_selected.append((f"Upper DZ {i}", z, t_info))
        for i, z in enumerate(slots.get("lower", []), 1):
            t_info = zone_tradeability(z, "UP")
            all_selected.append((f"Lower DZ {i}", z, t_info))

        if not all_selected:
            print("  (None selected - open range or no eligible zones)")

        for title, z, t_info in all_selected:
            h = z.top - z.bottom
            swap_tag = " [SWAP]" if z.is_swap else ""
            status_tag = f"[{z.status}{swap_tag}]"
            print(f"  * {title:<12} {status_tag:<18} {z.bottom:.2f} - {z.top:.2f} (mid: {z.mid:.2f}, h: {h:.1f}) | "
                  f"Score: {z.score} ({z.grade}) | Retests: {z.retest_count} ({t_info['label']})")
            if z.confluences:
                print(f"      Confluences: {', '.join(z.confluences)}")

        faded = slots.get("faded", [])
        if faded:
            print(f"\n  Faded/Filtered candidate zones ({len(faded)}):")
            for fz in faded[:3]:
                print(f"    - {fz.role:<10} {fz.bottom:.2f} - {fz.top:.2f} | Status: {fz.status} | Score: {fz.score} ({fz.grade}) | Retests: {fz.retest_count}")
        print("=" * 80)


if __name__ == "__main__":
    run_validation()
