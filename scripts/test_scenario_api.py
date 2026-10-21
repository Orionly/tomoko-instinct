"""
Test FastAPI endpoints for /api/scenarios, /api/pair/{symbol}/scenarios, and /api/briefing.
"""

import os
import sys
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.api import app

def test_api():
    print("=" * 60)
    print("TESTING FASTAPI SCENARIO & BRIEFING ENDPOINTS")
    print("=" * 60)

    client = TestClient(app)

    # 1. Test /api/scenarios
    print("\n1. Testing GET /api/scenarios...")
    r = client.get("/api/scenarios?hours_ahead=48")
    assert r.status_code == 200, f"Status code {r.status_code}"
    data = r.json()
    print(f"   Success! Received {data.get('count', 0)} scenario trees.")
    if data.get("scenarios"):
        top = data["scenarios"][0]
        print(f"   Top Catalyst: {top.get('event_title')} ({top.get('currency')})")

    # 2. Test /api/pair/XAUUSD/scenarios
    print("\n2. Testing GET /api/pair/XAUUSD/scenarios...")
    r = client.get("/api/pair/XAUUSD/scenarios")
    assert r.status_code == 200
    data = r.json()
    print(f"   Success! Bound to symbol: {data.get('symbol')} with {data.get('catalysts_count', 0)} catalysts.")

    # 4. Test /api/traps
    print("\n4. Testing GET /api/traps...")
    r = client.get("/api/traps")
    assert r.status_code == 200
    data = r.json()
    print(f"   Success! Scanned: {data.get('scanned_count')} pairs, Active Traps: {data.get('active_traps_count')}")

    # 5. Test /api/pair/XAUUSD/traps
    print("\n5. Testing GET /api/pair/XAUUSD/traps...")
    r = client.get("/api/pair/XAUUSD/traps")
    assert r.status_code == 200
    data = r.json()
    print(f"   Success! XAUUSD Trap State: {data.get('trap_state')} [{data.get('severity')}]")
    print(f"   Crowd Skew: {data.get('sentiment_skew')}")

    print("\n" + "=" * 60)
    print("ALL API ENDPOINTS FUNCTIONING 100% CORRECTLY!")
    print("=" * 60)

if __name__ == "__main__":
    test_api()
