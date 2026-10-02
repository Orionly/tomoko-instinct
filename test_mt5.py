# Vibe test - connect to local MT5 and print what the bridge sees.
# Run: python test_mt5.py
import sys
sys.path.append(".")

import MetaTrader5 as mt5
from config import PAIRS, XAU_CONFIG
from core.mt5_bridge import MT5Bridge


def main():
    print("== Tomoko MT5 Bridge vibe test ==")
    bridge = MT5Bridge()

    if not bridge.connect():
        print("Could not connect. Make sure the MT5 terminal is open and you're logged in.")
        return

    print(f"Balance: {bridge.get_balance()} USC")
    print(f"Symbol map: {bridge.symbol_map}")
    print(f"XAU_CONFIG: {XAU_CONFIG}")

    sample = PAIRS[0]
    print(f"\nPulling rates for {sample} (H1, last 100)...")
    df = bridge.get_rates(sample, mt5.TIMEFRAME_H1, 100)
    if df.empty:
        print("No data returned (market closed or symbol not mapped).")
        return

    last = df.iloc[-1]
    print(f"Last time : {last['time']}")
    print(f"Close     : {last['close']}")
    print(f"EMA20/50/21: {last['EMA20']:.5f} / {last['EMA50']:.5f} / {last['EMA21']:.5f}")
    print(f"ATR       : {last['ATR']:.5f}")
    print(f"RSI(14)   : {last['RSI']:.2f}")
    print(f"ADX(14)   : {last['ADX']:.2f}")
    print("\nBridge foundation looks solid.")


if __name__ == "__main__":
    main()
