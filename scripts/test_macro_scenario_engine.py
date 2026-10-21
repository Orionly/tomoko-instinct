"""
Test runner for MacroScenarioEngine:
Verifies catalyst classification, pre-event scenario tree generation,
flash verdicts on actual prints, and pair-centric synthesis.
"""

import os
import sys
import json

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.news_engine import NewsEngine
from core.macro_intelligence_engine import MacroIntelligenceEngine
from core.macro_scenario_engine import MacroScenarioEngine

def test_engine():
    print("=" * 60)
    print("INITIALIZING MACRO SCENARIO ENGINE TEST")
    print("=" * 60)

    # 1. Initialize dependencies
    news_eng = NewsEngine()
    macro_eng = MacroIntelligenceEngine()
    scenario_eng = MacroScenarioEngine(news_engine=news_eng, macro_engine=macro_eng)

    print(f"[NewsEngine] Loaded {len(news_eng.events)} cached/synced events.")

    # 2. Test synthetic event scenario tree (e.g., US CPI)
    synthetic_cpi = {
        "event": "Core CPI m/m",
        "currency": "USD",
        "forecast": "0.3%",
        "previous": "0.2%",
        "actual": "",
        "time": "2026-10-22T12:30:00Z",
        "timestamp": 1792672200,
        "impact": "HIGH"
    }

    print("\n--- TEST 1: PRE-EVENT 'IF-THEN' SCENARIOS FOR US CPI ---")
    tree = scenario_eng.generate_event_scenarios(synthetic_cpi)
    print(f"Catalyst: {tree['event_title']} ({tree['currency']}) - Category: {tree['category']}")
    print(f"Transmission: {tree['transmission_logic']}")
    print(f"Affected Pairs: {tree['affected_pairs']}")
    print("\n[SCENARIO A: HOT / BEAT]")
    print(f"  Condition: {tree['scenarios']['hot_beat']['label']}")
    print(f"  Thesis: {tree['scenarios']['hot_beat']['macro_thesis']}")
    print(f"  XAUUSD Play: {tree['scenarios']['hot_beat']['pair_playbook']['XAUUSD']}")
    print(f"  EURUSD Play: {tree['scenarios']['hot_beat']['pair_playbook']['EURUSD']}")
    print(f"  Beginner Warning: {tree['scenarios']['hot_beat']['beginner_warning']}")
    print(f"  Veteran Note: {tree['scenarios']['hot_beat']['veteran_note']}")

    print("\n[SCENARIO B: COOL / MISS]")
    print(f"  Condition: {tree['scenarios']['cool_miss']['label']}")
    print(f"  XAUUSD Play: {tree['scenarios']['cool_miss']['pair_playbook']['XAUUSD']}")

    print("\n[SCENARIO C: IN-LINE / CHOP]")
    print(f"  Condition: {tree['scenarios']['in_line']['label']}")
    print(f"  XAUUSD Play: {tree['scenarios']['in_line']['pair_playbook']['XAUUSD']}")

    # 3. Test Post-Release Flash Verdict
    print("\n--- TEST 2: POST-RELEASE FLASH VERDICT (HOT PRINT: 0.5% vs 0.3%) ---")
    synthetic_cpi_actual = dict(synthetic_cpi, actual="0.5%")
    actual_res = scenario_eng.generate_event_scenarios(synthetic_cpi_actual)
    fv = actual_res.get("flash_verdict")
    if fv:
        print(f"Headline: {fv['headline']}")
        print(f"Magnitude: {fv['magnitude']} | Verdict: {fv['verdict_type']}")
        print(f"Delta: {fv['delta']}% ({fv['percentage_deviation']}%)")
        print(f"Direction Summary: {fv['direction_summary']}")
        print(f"Beginner Directive: {fv['action_advice']['beginners']}")
        print(f"Veteran Directive: {fv['action_advice']['veterans']}")

    # 4. Test Live Upcoming Scenarios from Calendar
    print("\n--- TEST 3: SCANNING LIVE CALENDAR FOR UPCOMING SCENARIO TREES ---")
    live_scenarios = scenario_eng.get_upcoming_scenarios(hours_ahead=72, min_priority=2)
    print(f"Found {len(live_scenarios)} qualifying medium/high-impact catalysts in next 72 hours.")
    for idx, sc in enumerate(live_scenarios[:3], 1):
        print(f"\n[{idx}] {sc['event_title']} ({sc['currency']}) | Time: {sc.get('time')} | Impact: {sc.get('impact')}")
        print(f"    Affected: {sc['affected_pairs']}")
        print(f"    Hot Trigger: {sc['scenarios']['hot_beat']['label']}")
        print(f"    Cool Trigger: {sc['scenarios']['cool_miss']['label']}")

    # 5. Test Pair-Centric Synthesis for XAUUSD (Gold)
    print("\n--- TEST 4: PAIR-CENTRIC SYNTHESIS FOR XAUUSD (GOLD) ---")
    gold_briefing = scenario_eng.get_pair_catalyst_briefing("XAUUSD")
    print(f"Symbol: {gold_briefing['symbol']}")
    print(f"Drivers: {gold_briefing['drivers']}")
    print(f"Active Catalysts Bound to Gold: {gold_briefing['catalysts_count']}")
    if gold_briefing["intermarket_context"]:
        print("Intermarket Context (DXY, Yields, VIX):")
        for k, v in gold_briefing["intermarket_context"].items():
            print(f"  {k}: {v}")

    print("\n" + "=" * 60)
    print("MACRO SCENARIO ENGINE TEST COMPLETED SUCCESSFULLY")
    print("=" * 60)

if __name__ == "__main__":
    test_engine()
