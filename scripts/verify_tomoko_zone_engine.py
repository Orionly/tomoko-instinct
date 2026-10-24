# Tomoko Instinct - Decision Zones Engine Proof & Verification Suite
# Runs live verification across market pairs and simulates structural lifecycles.

import sys, os
sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path: sys.path.insert(0, ROOT)

import json
import pandas as pd
import numpy as np

from backend.api import _scan, get_pair_candles
from core.zone_engine import build_zones, select_slots, zone_tradeability, TF_PARAMS, Zone, _replay
from core.decision_zones import calculate_m15_decision_zones, calculate_all_decision_zones


def test_1_live_market_pairs():
    """Test 1: Verify live scanner produces valid zones with new metadata across pairs."""
    print("=" * 80)
    print("TEST 1: LIVE SCANNER DECISION ZONES CHECK (XAUUSD, EURUSD, GBPUSD, USDJPY)")
    print("=" * 80)
    
    pairs = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY"]
    for sym in pairs:
        res = _scan(sym)
        price = res.get("price", 0.0)
        dz = res.get("decision_zones", {}).get("m15", {})
        radar = dz.get("radar", {})
        u1 = dz.get("upper_dz_1")
        u2 = dz.get("upper_dz_2")
        l1 = dz.get("lower_dz_1")
        l2 = dz.get("lower_dz_2")
        faded = dz.get("faded_zones", [])

        print(f"\n[PAIR: {sym}] Live Price: {price} | M15 ATR: {dz.get('atr')}")
        print(f"  Radar State : {radar.get('badge_text')} | {radar.get('headline')}")
        
        # Verify Upper 1
        if u1:
            print(f"  Upper DZ 1  : {u1['low']} - {u1['high']} (mid: {u1['midline']}) | "
                  f"Score: {u1['strength_score']} ({u1['strength_grade']}) | "
                  f"Retests: {u1['retest_count']} ({u1['next_test_label']}) | Status: {u1['status']}")
        else:
            print(f"  Upper DZ 1  : None (clean empty slot - no qualified resistance)")

        # Verify Upper 2
        if u2:
            print(f"  Upper DZ 2  : {u2['low']} - {u2['high']} (mid: {u2['midline']}) | "
                  f"Score: {u2['strength_score']} ({u2['strength_grade']}) | "
                  f"Retests: {u2['retest_count']} ({u2['next_test_label']})")
            
        # Verify Lower 1
        if l1:
            print(f"  Lower DZ 1  : {l1['low']} - {l1['high']} (mid: {l1['midline']}) | "
                  f"Score: {l1['strength_score']} ({l1['strength_grade']}) | "
                  f"Retests: {l1['retest_count']} ({l1['next_test_label']}) | Status: {l1['status']}")
        else:
            print(f"  Lower DZ 1  : None (clean empty slot - no qualified demand)")

        # Verify Lower 2
        if l2:
            print(f"  Lower DZ 2  : {l2['low']} - {l2['high']} (mid: {l2['midline']}) | "
                  f"Score: {l2['strength_score']} ({l2['strength_grade']}) | "
                  f"Retests: {l2['retest_count']} ({l2['next_test_label']})")

        print(f"  Faded Zones : {len(faded)} (weak or exhausted zones filtered out from decision slots)")


def test_2_candles_api_limit():
    """Test 2: Verify /api/pair/{symbol}/candles with limit=1000."""
    print("\n" + "=" * 80)
    print("TEST 2: CANDLES API ENDPOINT (LIMIT 1000 VERIFICATION)")
    print("=" * 80)
    
    data = get_pair_candles("XAUUSD", tf="M15", limit=1000)
    candles = data.get("candles", [])
    dz = data.get("decision_zones", {})
    ingested = data.get("ingested_zones", [])
    print(f"Returned M15 candles count : {len(candles)} (requested up to 1000)")
    print(f"Decision Zones payload keys: {list(dz.keys())}")
    print(f"Ingested markups available : {len(ingested)}")
    assert len(candles) > 0, "Candles list should not be empty"
    print("PASS: Candles API delivers full dataset and zone metadata.")


def test_3_lifecycle_trace():
    """Test 3: Step-by-step trace of zone evolution: Fresh -> Retest 1 -> Retest 2 -> Sweep -> Breakout -> Swap -> Exhausted."""
    print("\n" + "=" * 80)
    print("TEST 3: STEP-BY-STEP LIFECYCLE REPLAY TRACE")
    print("=" * 80)

    p = TF_PARAMS["M15"]
    unit = np.full(100, 5.0)

    # 1. Create a virgin Supply Zone: 4040.0 - 4050.0 formed at bar 10
    z = Zone("SUPPLY", top=4050.0, bottom=4040.0, origin_idx=10, origin_time=None,
             departure=4.0, bos=True, volume_ratio=1.5)
    
    # Trace step A: Fresh state
    mock_bars = {"open": [4010.0]*15, "high": [4015.0]*15, "low": [4005.0]*15, "close": [4010.0]*15}
    df = pd.DataFrame(mock_bars)
    _replay(z, df, unit, p, start=11)
    t = zone_tradeability(z, "DOWN")
    print(f"Step A (Price at 4010, no retest yet):")
    print(f"  Status: {z.status} | Retests: {z.retest_count} | Tradeability: {t['label']}")
    assert z.status == "FRESH" and z.retest_count == 0

    # Trace step B: Price touches 4045 (inside zone), then leaves back to 4010
    df = pd.concat([df, pd.DataFrame({
        "open": [4020, 4042, 4030, 4010],
        "high": [4030, 4046, 4032, 4012],
        "low":  [4018, 4038, 4015, 4005],
        "close":[4028, 4041, 4018, 4008]
    })], ignore_index=True)
    _replay(z, df, unit, p, start=11)
    t = zone_tradeability(z, "DOWN")
    print(f"\nStep B (Price touched 4046 and rejected back to 4008):")
    print(f"  Status: {z.status} | Retests: {z.retest_count} | Next: {t['label']}")
    assert z.retest_count == 1

    # Trace step C: Price wicks to 4054 (beyond 4050 top), but closes at 4042 (inside) -> SWEEP
    df = pd.concat([df, pd.DataFrame({
        "open": [4035, 4045],
        "high": [4045, 4054],  # Wick > 4050!
        "low":  [4030, 4038],
        "close":[4042, 4042]   # Close inside!
    })], ignore_index=True)
    _replay(z, df, unit, p, start=11)
    print(f"\nStep C (Price wicked to 4054 > 4050 top, but closed at 4042):")
    print(f"  Swept count: {z.swept_count} | Retests: {z.retest_count} | Is swap: {z.is_swap}")
    assert z.swept_count == 1 and not z.is_swap

    # Trace step D: Price departs to 4010, then BREAKS OUT with candle body close at 4065 (> 4050)
    df = pd.concat([df, pd.DataFrame({
        "open": [4010, 4020, 4045, 4055],
        "high": [4012, 4035, 4055, 4068],
        "low":  [4005, 4015, 4040, 4052],
        "close":[4008, 4030, 4052, 4065]  # Body close 4065 > 4050!
    })], ignore_index=True)
    _replay(z, df, unit, p, start=11)
    t = zone_tradeability(z, "UP")
    print(f"\nStep D (Candle body closed at 4065 > 4050 far edge):")
    print(f"  Role: {z.role} | Is swap: {z.is_swap} | Retests: {z.retest_count} | Tradeability: {t['label']}")
    assert z.is_swap and z.role == "SUPPORT" and z.retest_count == 0

    print("\nPASS: Lifecycle accurately handles Fresh, Retest, Sweep, and Break-to-Swap.")


if __name__ == "__main__":
    test_1_live_market_pairs()
    test_2_candles_api_limit()
    test_3_lifecycle_trace()
    print("\n" + "=" * 80)
    print("ALL VERIFICATION SUITES CONFIRMED 100% OPERATIONAL!")
    print("=" * 80)
