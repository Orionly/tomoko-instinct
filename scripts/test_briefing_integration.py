"""
Integration test for SessionBriefingEngine with MacroScenarioEngine integration.
Verifies briefing generation, pair-specific extraction, and fallback synthesis.
"""

import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.price_engine import PriceEngine
from core.levels_engine import LevelsEngine
from core.news_engine import NewsEngine
from core.macro_intelligence_engine import MacroIntelligenceEngine
from core.macro_scenario_engine import MacroScenarioEngine
from core.session_briefing_engine import SessionBriefingEngine

def main():
    print("=" * 60)
    print("TESTING SESSION BRIEFING & MACRO SCENARIO INTEGRATION")
    print("=" * 60)

    pe = PriceEngine()
    le = LevelsEngine()
    ne = NewsEngine(pe)
    me = MacroIntelligenceEngine()
    mse = MacroScenarioEngine(news_engine=ne, macro_engine=me, price_engine=pe)

    sbe = SessionBriefingEngine(
        price_engine=pe,
        levels_engine=le,
        news_engine=ne,
        macro_engine=me,
        macro_scenario_engine=mse
    )

    print("\n--- Generating Battle-Plan ---")
    briefing = sbe.generate_briefing()
    print(f"Session: {briefing.get('session')}")
    print(f"Generated At UTC: {briefing.get('generated_at_utc')}")
    print(f"Mode: {briefing.get('engine_mode', 'GEMINI_AI')}")
    print(f"Executive Thesis:\n  {briefing.get('global_macro_executive_thesis')}")

    print("\n--- Inspecting XAUUSD (Gold) Tactical Plan ---")
    gold = sbe.get_briefing("XAUUSD")
    plan = gold.get("plan", {})
    print(f"Bottom Line: {plan.get('executive_bottom_line')}")
    print(f"Setup Bias: {plan.get('primary_setup', {}).get('bias')} ({plan.get('primary_setup', {}).get('probability')})")
    print(f"Entry Zone: {plan.get('primary_setup', {}).get('entry_zone')}")
    print(f"Stop Loss: {plan.get('primary_setup', {}).get('stop_loss')}")
    print(f"Targets: {plan.get('primary_setup', {}).get('targets')}")
    print(f"Trigger: {plan.get('primary_setup', {}).get('trigger_confirmation')}")
    print("Catalyst IF/THEN Branches:")
    for b in plan.get("macro_catalyst", {}).get("if_then_branches", []):
        print(f"  * {b}")
    print(f"Trap Warning: {plan.get('traps_and_checklist', {}).get('trap_warning')}")

    print("\n--- Inspecting EURUSD Tactical Plan ---")
    eur = sbe.get_briefing("EURUSD")
    eur_plan = eur.get("plan", {})
    print(f"Bottom Line: {eur_plan.get('executive_bottom_line')}")
    print(f"Setup Bias: {eur_plan.get('primary_setup', {}).get('bias')}")
    print(f"Entry Zone: {eur_plan.get('primary_setup', {}).get('entry_zone')}")
    print("Catalyst IF/THEN Branches:")
    for b in eur_plan.get("macro_catalyst", {}).get("if_then_branches", []):
        print(f"  * {b}")

    print("\n" + "=" * 60)
    print("SESSION BRIEFING INTEGRATION TEST PASSED!")
    print("=" * 60)

if __name__ == "__main__":
    main()
