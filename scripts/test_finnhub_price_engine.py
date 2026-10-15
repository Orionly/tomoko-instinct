"""
Standalone Test Script for Finnhub Live Price Feeds.
Tests:
  1. REST Authentication & Forex Exchange Symbols
  2. Live WebSocket Connection & Subscriptions for all 7 pairs (EURUSD, GBPUSD, USDJPY, GBPJPY, EURJPY, EURGBP, XAUUSD)
  3. Real-time tick reception and parsing
"""

import json
import time
import asyncio
import urllib.request
import websockets

API_KEY = "db8aja9r01qmvbh6ojdgdb8aja9r01qmvbh6oje0"

SYMBOLS_MAP = {
    "EURUSD": "OANDA:EUR_USD",
    "GBPUSD": "OANDA:GBP_USD",
    "USDJPY": "OANDA:USD_JPY",
    "GBPJPY": "OANDA:GBP_JPY",
    "EURJPY": "OANDA:EUR_JPY",
    "EURGBP": "OANDA:EUR_GBP",
    "XAUUSD": "OANDA:XAU_USD",
}


def test_rest_api():
    print("=" * 60)
    print("1. TESTING FINNHUB REST API ENDPOINTS")
    print("=" * 60)

    # 1. Test basic rate check
    try:
        url = f"https://finnhub.io/api/v1/forex/rates?base=USD&token={API_KEY}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f"[REST] /forex/rates?base=USD -> Status: {resp.status}")
            rates = data.get("quote", {})
            print(f"[REST] Total rates returned: {len(rates)}")
            for ccy in ["EUR", "GBP", "JPY"]:
                if ccy in rates:
                    print(f"       USD/{ccy}: {rates[ccy]}")
    except Exception as e:
        print(f"[REST] Error testing /forex/rates: {e}")

    # 2. Test OANDA symbols list
    try:
        url = f"https://finnhub.io/api/v1/forex/symbol?exchange=oanda&token={API_KEY}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            symbols_list = json.loads(resp.read().decode("utf-8"))
            print(f"[REST] /forex/symbol?exchange=oanda -> Returned {len(symbols_list)} symbols")
            found = [s["symbol"] for s in symbols_list if s.get("symbol") in SYMBOLS_MAP.values()]
            print(f"[REST] Target symbols verified on OANDA: {found}")
    except Exception as e:
        print(f"[REST] Error testing /forex/symbol: {e}")


async def test_websocket():
    print("\n" + "=" * 60)
    print("2. TESTING FINNHUB LIVE WEBSOCKET")
    print("=" * 60)

    ws_url = f"wss://ws.finnhub.io?token={API_KEY}"
    print(f"Connecting to: wss://ws.finnhub.io?token={API_KEY[:8]}...")

    received_ticks = {}

    try:
        async with websockets.connect(ws_url, ping_interval=20, ping_timeout=10) as ws:
            print("[WebSocket] Connected successfully! (Auth OK)")

            # Subscribe to all 7 pairs
            for pair, finnhub_sym in SYMBOLS_MAP.items():
                msg = json.dumps({"type": "subscribe", "symbol": finnhub_sym})
                await ws.send(msg)
                print(f"[WebSocket] Subscribed to {pair} ({finnhub_sym})")

            # Also subscribe to a high-volume symbol (e.g. BINANCE:BTCUSDT) to confirm stream flow
            await ws.send(json.dumps({"type": "subscribe", "symbol": "BINANCE:BTCUSDT"}))
            print("[WebSocket] Subscribed to test canary (BINANCE:BTCUSDT)")

            print("\nListening for live stream messages (10 seconds)...")
            start_time = time.time()
            message_count = 0

            while time.time() - start_time < 10:
                try:
                    raw_msg = await asyncio.wait_for(ws.recv(), timeout=3.0)
                    message_count += 1
                    data = json.loads(raw_msg)
                    msg_type = data.get("type")

                    if msg_type == "trade":
                        trades = data.get("data", [])
                        for t in trades:
                            s = t.get("s")
                            p = t.get("p")
                            ts = t.get("t")
                            received_ticks[s] = {"price": p, "timestamp": ts}
                            print(f"  [TICK] {s:<18} -> Price: {p:<10} Time: {ts}")

                    elif msg_type == "ping":
                        await ws.send(json.dumps({"type": "pong"}))
                    else:
                        print(f"  [MSG] {raw_msg}")

                except asyncio.TimeoutError:
                    print("  ... waiting for ticks ...")
                    continue

            print("\n" + "-" * 60)
            print(f"Summary: Received {message_count} messages in 10 seconds.")
            print(f"Unique symbols with received ticks: {list(received_ticks.keys())}")
            print("-" * 60)

    except Exception as e:
        print(f"[WebSocket] Error during test: {e}")


if __name__ == "__main__":
    test_rest_api()
    asyncio.run(test_websocket())
