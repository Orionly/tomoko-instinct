"""Unit tests for core/dna_ml_tuner.py and ML-calibrated zone_engine integration."""

import os
import sys
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.dna_ml_tuner import (
    load_unified_snapshots,
    parse_snapshot_utc,
    slice_candles_at_timestamp,
    train_zone_preference_model,
)
from core.zone_engine import (
    DEFAULT_TF_PARAMS,
    build_zones,
    extract_zone_features,
    get_tf_params,
)


def _make_df(start="2026-11-01 00:00", periods=160, base=4000.0):
    rng = np.random.default_rng(7)
    times = pd.date_range(start, periods=periods, freq="15min")
    closes = base + np.cumsum(rng.normal(0, 1.8, periods))
    # Inject clear swing high & low
    closes[50] = base + 35.0
    closes[51:56] = np.linspace(base + 25.0, base - 5.0, 5)
    closes[90] = base - 40.0
    closes[91:96] = np.linspace(base - 30.0, base + 5.0, 5)
    df = pd.DataFrame({
        "time": times,
        "open": closes - rng.uniform(-1, 1, periods),
        "high": closes + rng.uniform(1, 3, periods),
        "low": closes - rng.uniform(1, 3, periods),
        "close": closes,
        "volume": rng.uniform(100, 400, periods),
    })
    df.loc[50, "high"] = base + 42.0
    df.loc[90, "low"] = base - 46.0
    return df


def test_snapshot_loading_and_timezone_slicing():
    snaps, trades = load_unified_snapshots("XAUUSD")
    assert len(snaps) >= 10, f"Expected unified snapshots >= 10, got {len(snaps)}"

    # Verify MYT -> UTC conversion (-8h)
    utc_ts = parse_snapshot_utc({"file_timestamp": "2026-11-09 08:20:37 MYT"})
    assert utc_ts == pd.Timestamp("2026-11-09 00:20:37")

    df = _make_df("2026-11-08 00:00", periods=160)
    sliced, ok = slice_candles_at_timestamp(df, utc_ts, min_bars=20)
    assert ok and sliced is not None
    assert sliced["time"].max() <= utc_ts, "Lookahead leak detected in slice_candles_at_timestamp!"

    # Timestamp before df start must return out-of-window False
    early_ts = pd.Timestamp("2026-10-20 00:00:00")
    sliced_early, ok_early = slice_candles_at_timestamp(df, early_ts)
    assert not ok_early and sliced_early is None


def test_tf_params_override_isolation():
    base = get_tf_params("M15", use_tuned=False)
    ov = get_tf_params("M15", override={"min_departure": 1.3}, use_tuned=False)
    assert ov["min_departure"] == 1.3
    assert DEFAULT_TF_PARAMS["M15"]["min_departure"] == 2.0
    assert base["min_departure"] == 2.0


def test_tiered_ml_stages_logreg_and_lightgbm():
    df = _make_df()
    res = build_zones(df, "M15", "XAUUSD", use_tuned=False)
    zones = res["zones"]
    assert len(zones) >= 2

    # Pick one zone as trader-marked ground truth so positive and negative labels exist
    target_z = zones[0]
    m15_items = []
    for _ in range(5):
        m15_items.append({
            "df": df,
            "htf_levels": [],
            "live_p": float(df["close"].iloc[-1]),
            "snap": {"zones": [{"low": target_z.bottom, "high": target_z.top}]},
        })

    # Stage 1 (< 30)
    s1 = train_zone_preference_model(m15_items, total_snapshots=15, symbol="XAUUSD", m15_params=DEFAULT_TF_PARAMS["M15"], dry_run=True)
    assert s1["stage"] == "STAGE_1_OPTUNA"

    # Stage 1.5 (30-79 -> L2 Logistic Regression)
    s15 = train_zone_preference_model(m15_items, total_snapshots=35, symbol="XAUUSD", m15_params=DEFAULT_TF_PARAMS["M15"], dry_run=True)
    assert s15["stage"] == "STAGE_1_5_LOGREG"
    assert s15["priority_weights"] is not None
    assert len(s15["priority_weights"]["coef"]) == len(extract_zone_features(target_z))

    # Stage 2 (80+ -> Shallow LightGBM)
    s2 = train_zone_preference_model(m15_items, total_snapshots=85, symbol="XAUUSD", m15_params=DEFAULT_TF_PARAMS["M15"], dry_run=True)
    assert s2["stage"] == "STAGE_2_LIGHTGBM"
    assert s2["scorer_model_path"] is not None


if __name__ == "__main__":
    test_snapshot_loading_and_timezone_slicing()
    test_tf_params_override_isolation()
    test_tiered_ml_stages_logreg_and_lightgbm()
    print("ALL DNA ML TUNER UNIT TESTS PASSED.")

