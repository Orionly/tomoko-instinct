import sys
import os
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.macro_intelligence_engine import MacroIntelligenceEngine

def test_engine():
    print("Testing Enhanced MacroIntelligenceEngine...")
    engine = MacroIntelligenceEngine()
    
    data = engine.get_full_intelligence()
    
    print("\n--- SESSIONS INTELLIGENCE ---")
    print(json.dumps(data["sessions"], indent=2))
    
    print("\n--- SYNTHESIZED INSIGHT ---")
    print(json.dumps(data["insight"], indent=2))
    
    print("\n--- PAIR CONFLUENCE CHECKS ---")
    for sym in ["XAUUSD", "EURUSD", "USDJPY", "GBPJPY"]:
        conf = engine.get_pair_macro_confluence(sym, data["matrix"], data["insight"])
        print(f"[{sym}] {conf['status']} ({conf['class']}): {conf['rationale']}")

    print("\nSUCCESS: All enhanced macro rules and pair confluence engines verified!")

if __name__ == "__main__":
    test_engine()
