#!/usr/bin/env python3
"""
Ingest morning TradingView chart markups from markups/ folder.
Uses Gemini Vision to extract trader-drawn zones and logs them into private data/trader_profile.json.
"""
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.markup_ingestion_engine import ingest_pending_markups, load_trader_profile

def main():
    print("=" * 60)
    print("TOMOKO PROPRIETARY MARKUP INGESTION PIPELINE")
    print("=" * 60)
    res = ingest_pending_markups()
    if not res:
        print("No new chart images found in markups/ folder.")
        print("Tip: Click Camera icon in TradingView -> Download image -> Save to markups/")
    else:
        print(f"\n[+] Successfully ingested {len(res)} chart markup(s)!")
        for r in res:
            sym = r.get("symbol", "XAUUSD")
            tf = r.get("timeframe", "M15")
            price = r.get("live_price_on_chart", "—")
            print(f"\n--- {sym} [{tf}] (Chart Price: {price}) ---")
            for z in r.get("zones", []):
                mid = f" (50% CE: {z.get('midline')})" if z.get("midline") else ""
                print(f"  [{z.get('type')}] {z.get('name')}: {z.get('low')} – {z.get('high')}{mid} | {z.get('notes')}")

    profile = load_trader_profile()
    active = profile.get("active_session", {})
    if active:
        print(f"\nActive Session Zones Mapped: {list(active.keys())}")
    print("=" * 60)

if __name__ == "__main__":
    main()
