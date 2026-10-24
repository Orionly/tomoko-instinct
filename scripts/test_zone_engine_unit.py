# Tomoko Instinct - Zone Engine Unit Tests
import sys, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path: sys.path.insert(0, ROOT)

import pandas as pd
import numpy as np
from core.zone_engine import build_zones, select_slots, zone_tradeability
from core.decision_zones import calculate_m15_decision_zones, calculate_all_decision_zones


def make_mock_df(bars=150, base_price=4000.0, swing_type="both"):
    np.random.seed(42)
    dates = pd.date_range("2026-10-01 08:00", periods=bars, freq="15min", tz="Asia/Kuala_Lumpur")
    c = [base_price]
    for _ in range(bars - 1):
        c.append(c[-1] + np.random.normal(0, 2.0))
    c = np.array(c)
    
    # Inject an intentional strong swing high at bar 50
    c[48:53] = [base_price + 20, base_price + 35, base_price + 50, base_price + 30, base_price + 10]
    # And strong departure down
    c[53:65] = np.linspace(base_price + 10, base_price - 20, 12)

    # Inject an intentional strong swing low at bar 80
    c[78:83] = [base_price - 25, base_price - 40, base_price - 60, base_price - 35, base_price - 15]
    # And strong departure up
    c[83:95] = np.linspace(base_price - 15, base_price + 15, 12)

    df = pd.DataFrame({
        "time": dates,
        "open": c - np.random.uniform(-1, 1, bars),
        "high": c + np.random.uniform(1, 3, bars),
        "low": c - np.random.uniform(1, 3, bars),
        "close": c,
        "volume": np.random.uniform(100, 500, bars)
    })
    # Fix the high of the peak bar and low of trough bar
    df.loc[50, "high"] = base_price + 55.0
    df.loc[80, "low"] = base_price - 65.0
    return df


def test_zone_engine_basic():
    df = make_mock_df()
    res = build_zones(df, "M15", "XAUUSD")
    assert len(res["zones"]) > 0, "Should detect at least 1 zone"
    price = float(df["close"].iloc[-1])
    slots = select_slots(res["zones"], price, res["unit"], res["params"])
    assert "upper" in slots and "lower" in slots
    print(f"PASS: Basic zone detection. Total zones found: {len(res['zones'])}")


def test_sweep_vs_break():
    # Construct a synthetic zone and replay bars testing sweep vs break
    from core.zone_engine import Zone, _replay, TF_PARAMS
    p = TF_PARAMS["M15"]
    unit = np.full(50, 5.0)

    # Supply zone: top=4050, bottom=4040
    z = Zone("SUPPLY", top=4050.0, bottom=4040.0, origin_idx=5, origin_time=None,
             departure=4.0, bos=True, volume_ratio=1.5)

    # Bars 6 to 15: price moves away to 4010
    closes = np.full(30, 4010.0)
    highs = np.full(30, 4015.0)
    lows = np.full(30, 4005.0)
    opens = np.full(30, 4010.0)

    # Bar 16: SWEEP (high reaches 4055 > 4050, but close is 4038 < 4040)
    highs[16] = 4055.0
    closes[16] = 4038.0
    lows[16] = 4030.0

    # Bar 17-20: moves away
    closes[17:21] = 4010.0
    lows[17:21] = 4005.0
    highs[17:21] = 4015.0

    mock_df = pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes})
    _replay(z, mock_df, unit, p, start=6)

    assert z.retest_count == 1, f"Expected 1 retest, got {z.retest_count}"
    assert z.swept_count == 1, f"Expected 1 sweep, got {z.swept_count}"
    assert not z.is_swap, "Should not be swap after a sweep"
    print("PASS: Sweep correctly recorded (swept_count=1, retest_count=1, not broken)")

    # Now simulate a BREAK on bar 22 (body closes at 4060 > 4050)
    closes[22] = 4060.0
    highs[22] = 4065.0
    lows[22] = 4045.0
    opens[22] = 4045.0

    mock_df_break = pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes})
    _replay(z, mock_df_break, unit, p, start=6)
    assert z.is_swap, "Zone should flip to SWAP after body close beyond top"
    assert z.role == "SUPPORT", "Broken resistance should become SUPPORT"
    assert z.retest_count == 0, "Retest count should restart at 0 after flipping"
    print("PASS: Break correctly recorded (is_swap=True, role=SUPPORT, retests reset to 0)")


def test_exhaustion_at_fourth_retest():
    from core.zone_engine import Zone, zone_tradeability
    z = Zone("DEMAND", top=4010.0, bottom=4000.0, origin_idx=0, origin_time=None,
             departure=3.0, bos=False, volume_ratio=1.0)
    z.retest_count = 3
    t_info = zone_tradeability(z, "UP")
    assert t_info["tier"] == "EXHAUSTED", f"Expected EXHAUSTED for 4th test, got {t_info['tier']}"
    assert "EXHAUSTED — do not trade" in t_info["label"]
    print("PASS: 4th retest marked EXHAUSTED and forbidden from trading")


def test_m15_integration_returns_clean_dict():
    df = make_mock_df()
    price = float(df["close"].iloc[-1])
    m15 = calculate_m15_decision_zones(df, price, symbol="XAUUSD")
    assert "radar" in m15
    assert "timeframe" in m15 and m15["timeframe"] == "M15"
    assert "faded_zones" in m15
    print("PASS: calculate_m15_decision_zones returns clean specification dictionary")


if __name__ == "__main__":
    test_zone_engine_basic()
    test_sweep_vs_break()
    test_exhaustion_at_fourth_retest()
    test_m15_integration_returns_clean_dict()
    print("\nALL UNIT TESTS PASSED SUCCESSFULLY!")
