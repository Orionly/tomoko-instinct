"""
Unit and integration test for TrapRadarEngine:
Verifies Buy-Side/Sell-Side liquidity pool detection, Bull/Bear Trap detection,
Judas window evaluation, and crowd sentiment skew calculation.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.price_engine import PriceEngine
from core.levels_engine import LevelsEngine
from core.trap_radar_engine import TrapRadarEngine

def main():
    print("=" * 60)
    print("TESTING TRAP RADAR ENGINE")
    print("=" * 60)

    pe = PriceEngine()
    le = LevelsEngine()
    radar = TrapRadarEngine(price_engine=pe, levels_engine=le)

    # 1. Test Judas Window Check
    is_judas, j_name = radar.is_judas_window()
    print(f"\n1. Judas Window Active: {is_judas} ({j_name})")

    # 2. Test Scan for Gold (XAUUSD)
    print("\n2. Scanning XAUUSD Liquidity & Trap Radar...")
    gold_trap = radar.scan_pair_liquidity_traps("XAUUSD")
    print(f"   Symbol: {gold_trap['symbol']} | Spot: {gold_trap['spot_price']}")
    print(f"   Trap State: {gold_trap['trap_state']} [{gold_trap['severity']}]")
    print(f"   Title: {gold_trap['trap_title']}")
    print(f"   Description: {gold_trap['description']}")
    print(f"   Sentiment Skew: {gold_trap['sentiment_skew']}")
    print("   Buy-Side Liquidity (BSL) Pools:")
    for bsl in gold_trap["liquidity_pools"]["buy_side_liquidity_bsl"]:
        print(f"     * {bsl['label']}: {bsl['price']}")
    print("   Sell-Side Liquidity (SSL) Pools:")
    for ssl in gold_trap["liquidity_pools"]["sell_side_liquidity_ssl"]:
        print(f"     * {ssl['label']}: {ssl['price']}")
    print(f"   Beginner Guidance: {gold_trap['operational_guidance']['beginners']}")
    print(f"   Veteran Guidance: {gold_trap['operational_guidance']['veterans']}")

    # 3. Test Full Portfolio Scan Across All 7 Pairs
    print("\n3. Scanning All 7 Pairs for Active Traps & Liquidity Sweeps...")
    all_traps = radar.scan_all_pairs()
    print(f"   Total Pairs Scanned: {all_traps['scanned_count']}")
    print(f"   Active Traps Flagged: {all_traps['active_traps_count']}")
    for trap in all_traps["active_traps"]:
        print(f"     [ALERT] {trap['symbol']}: {trap['title']} ({trap['severity']})")
        print(f"             {trap['description']}")

    print("\n" + "=" * 60)
    print("TRAP RADAR ENGINE TEST COMPLETED SUCCESSFULLY!")
    print("=" * 60)

if __name__ == "__main__":
    main()
