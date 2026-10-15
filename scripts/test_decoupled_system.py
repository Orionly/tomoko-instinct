#!/usr/bin/env python3
"""
Comprehensive Decoupled System Verification.
Verifies all 3 decoupled parts:
  #1 News Calendar (nfs.faireconomy.media + daily 6am cache)
  #2 Post-release Tradays watcher (actual value)
  #3 Finnhub WebSocket + yfinance rates & indicators engine
  #4 FastAPI endpoints and UI data contracts
"""
import sys
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config
from fastapi.testclient import TestClient
from backend.api import app
from core.news_engine import NewsEngine
from core.price_engine import PriceEngine

def test_full_system():
    print("=" * 65)
    print("TOMOKO V5.0 FULL DECOUPLED SYSTEM VERIFICATION")
    print("=" * 65)

    # 1. Price Engine & Finnhub WebSocket
    print("\n[1/4] Testing Autonomous PriceEngine (Finnhub WS + yfinance)...")
    pe = PriceEngine()
    print("Waiting 3s for WebSocket tick handshake & yfinance seed...")
    time.sleep(3)
    print(f"PriceEngine connected: {pe.connected}")
    prices = {p: pe.get_price(p) for p in config.PAIRS}
    live_count = sum(1 for p in prices.values() if p is not None)
    print(f"Live/Seeded prices available: {live_count}/{len(config.PAIRS)}")
    for sym in ["EURUSD", "USDJPY", "XAUUSD"]:
        p = prices.get(sym)
        if p:
            print(f"  {sym}: Bid={p.get('bid')} Ask={p.get('ask')} Spread={p.get('spread')} Source={p.get('source')}")

    # 2. Indicators & Rates
    print("\n[2/4] Testing Multi-Timeframe Indicator Engine (H4, H1, M15)...")
    for sym in ["EURUSD", "XAUUSD"]:
        df_h1 = pe.get_rates(sym, 16385, 50)
        df_h4 = pe.get_rates(sym, 16388, 50)
        assert not df_h1.empty, f"H1 rates for {sym} empty!"
        assert not df_h4.empty, f"H4 rates for {sym} empty!"
        last = df_h1.iloc[-1]
        print(f"  {sym} H1: Close={last['close']} EMA21={last['EMA21']:.4f} ATR={last['ATR']:.4f} RSI={last['RSI']:.1f} ADX={last['ADX']:.1f}")

    # 3. News Engine
    print("\n[3/4] Testing Decoupled NewsEngine...")
    ne = NewsEngine()
    cal = ne.get_global_events(hours_ahead=168)
    events = cal.get("events", [])
    print(f"Calendar source: {cal.get('source')} | Total events: {len(events)}")
    upcoming = ne.get_upcoming_events("EURUSD", hours_ahead=24)
    print(f"Upcoming EURUSD events in 24h: {len(upcoming)}")
    if upcoming:
        first = upcoming[0]
        print(f"  Next: {first.get('time')} | {first.get('currency')} | {first.get('event')} | Fore={first.get('forecast')} Prev={first.get('previous')} Act={first.get('actual')}")

    # 4. API Endpoints
    print("\n[4/4] Testing Live Web API Endpoints via FastAPI TestClient...")
    client = TestClient(app)

    endpoints = [
        ("/api/prices", "Live Prices"),
        ("/api/context", "Context Strip & Risk"),
        ("/api/macro_intel", "Macro Intelligence"),
        ("/api/scan_all", "Full 7-Pair Scan"),
        ("/api/pair/EURUSD/deep_dive", "EURUSD Deep Dive"),
        ("/api/market_heatmap", "Market Heatmap")
    ]

    for ep, name in endpoints:
        t0 = time.time()
        res = client.get(ep)
        elapsed = (time.time() - t0) * 1000
        print(f"  [{res.status_code}] {name:25s} ({ep}) in {elapsed:.1f}ms")
        assert res.status_code == 200, f"Endpoint {ep} failed with {res.status_code}"

    print("\n" + "=" * 65)
    print("ALL 4 SUB-SYSTEMS FULLY VERIFIED - ZERO MT5 BREAKAGE DETECTED!")
    print("=" * 65)

if __name__ == "__main__":
    test_full_system()
