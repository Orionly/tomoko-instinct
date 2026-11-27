"""
Tomoko Instinct — Offline Weekend Trader DNA ML Calibration Engine
--------------------------------------------------------------------
Strictly offline calibration module executed only on weekends via
`python scripts/weekend_comparison.py --tune`.

Tiered ML Architecture (Zero Live Training):
  * Stage 1   (< 30 snapshots) : Optuna TPE Bayesian Optimization of
                                 M15 TF_PARAMS and H1/H4 decision zone widths.
  * Stage 1.5 (30-79 snapshots): Optuna + L2 Regularized Logistic Regression
                                 for linear priority score weights.
  * Stage 2   (80+ snapshots)  : Optuna + Shallow LightGBM Classifier saved to
                                 `data/models/dna_zone_scorer.pkl`.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.decision_zones import (
    DEFAULT_HTF_PARAMS,
    calculate_tf_decision_zones,
)
from core.trader_dna_engine import (
    DNA_PROFILE_FILE,
    load_trader_dna_profile,
)
from core.zone_engine import (
    DEFAULT_TF_PARAMS,
    SCORER_MODEL_PATH,
    TUNED_PARAMS_PATH,
    Zone,
    build_zones,
    extract_zone_features,
    htf_swing_levels,
    select_slots,
)

MYT = timezone(timedelta(hours=8))

STAGE_1_THRESHOLD_LOGREG = 30
STAGE_2_THRESHOLD_LGBM = 80

# Entry Execution DNA Calibration (Battle-Plan Entry Logic)
TUNED_ENTRY_DNA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "tuned_entry_dna.json",
)
ENTRY_STAGE_BLENDED_MIN = 5
ENTRY_STAGE_FULL_MIN = 15

DEFAULT_ENTRY_PARAMS: Dict[str, Any] = {
    "default_sl_buf_atr": 0.85,
    "max_sl_dist_atr": 2.20,
    "min_tp1_rr": 0.80,
    "tp1_rr": 1.00,
    "tp2_rr": 2.00,
    "wick_rejection_ratio": 0.55,
    "require_displacement": True,
}


# --------------------------------------------------------------------------- #
# Unified Snapshot Loader & Point-in-Time Candle Slicer
# --------------------------------------------------------------------------- #

def parse_snapshot_utc(snap: Dict[str, Any]) -> Optional[pd.Timestamp]:
    """Convert snapshot MYT timestamp (or chart_date) into naive UTC Timestamp matching candles_cache."""
    ts_str = snap.get("file_timestamp") or ""
    if ts_str:
        clean = ts_str.replace(" MYT", "").strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt_myt = datetime.strptime(clean, fmt).replace(tzinfo=MYT)
                dt_utc = dt_myt.astimezone(timezone.utc).replace(tzinfo=None)
                return pd.Timestamp(dt_utc)
            except ValueError:
                continue
    chart_date = snap.get("chart_date")
    if chart_date:
        try:
            dt_myt = datetime.strptime(f"{chart_date} 23:59:59", "%Y-%m-%d %H:%M:%S").replace(tzinfo=MYT)
            dt_utc = dt_myt.astimezone(timezone.utc).replace(tzinfo=None)
            return pd.Timestamp(dt_utc)
        except ValueError:
            pass
    return None


def _read_json_safe(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_unified_snapshots(symbol: str = "XAUUSD") -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Load and deduplicate all benchmark zone snapshots and executed trades from
    `trader_dna/data/trader_dna_profile.json` (/dna).
    """
    sym_up = symbol.upper()
    sources = [
        _read_json_safe(DNA_PROFILE_FILE),
        load_trader_dna_profile(),
    ]

    raw_zone_snaps: List[Dict[str, Any]] = []
    raw_trades: List[Dict[str, Any]] = []

    for src in sources:
        if not isinstance(src, dict):
            continue
        for entry in src.get("history", []):
            if str(entry.get("symbol", "")).upper() != sym_up:
                continue
            if entry.get("mode") == "TRADE_EXECUTION" or ("entry_price" in entry and not entry.get("zones")):
                raw_trades.append(entry)
            elif entry.get("zones"):
                raw_zone_snaps.append(entry)

        for tf_key, tf_sess in (src.get("active_session") or {}).items():
            if not isinstance(tf_sess, dict):
                continue
            if str(tf_sess.get("symbol", "")).upper() == sym_up and tf_sess.get("zones"):
                copy_sess = dict(tf_sess)
                copy_sess.setdefault("timeframe", tf_key.upper())
                raw_zone_snaps.append(copy_sess)

        for tr in src.get("executed_trades", []):
            if str(tr.get("symbol", "")).upper() == sym_up:
                raw_trades.append(tr)

    # Deduplicate zone snapshots by (timeframe, timestamp_or_date, first_zone_bounds)
    seen_snaps = set()
    dedup_snaps: List[Dict[str, Any]] = []
    for s in raw_zone_snaps:
        tf = str(s.get("timeframe", "M15")).upper()
        ts = s.get("file_timestamp") or f"{s.get('chart_date')}_{s.get('session')}"
        z0 = s["zones"][0] if s.get("zones") else {}
        sig = (tf, ts, round(float(z0.get("low", 0)), 3), round(float(z0.get("high", 0)), 3), len(s.get("zones", [])))
        if sig in seen_snaps:
            continue
        seen_snaps.add(sig)
        snap_copy = dict(s)
        snap_copy["timeframe"] = tf
        snap_copy["_utc_ts"] = parse_snapshot_utc(snap_copy)
        dedup_snaps.append(snap_copy)

    # Sort chronologically
    dedup_snaps.sort(key=lambda x: str(x.get("file_timestamp") or x.get("chart_date") or ""))

    # Deduplicate trades (preserve distinct intraday trades by timestamp or telegram_msg_id)
    seen_trades = set()
    dedup_trades: List[Dict[str, Any]] = []
    for tr in raw_trades:
        sig = (
            str(tr.get("direction", "")),
            round(float(tr.get("entry_price", 0.0)), 3),
            str(tr.get("file_timestamp") or tr.get("chart_date", "")),
            tr.get("telegram_msg_id"),
        )
        if sig in seen_trades:
            continue
        seen_trades.add(sig)
        dedup_trades.append(tr)

    # Promote verified winning (TP_HIT) signal entry zones into M15 zone snapshots for ML tuning
    for tr in dedup_trades:
        if (
            tr.get("eod_outcome") == "TP_HIT"
            and tr.get("entry_zone_low") is not None
            and tr.get("entry_zone_high") is not None
        ):
            z_low = float(tr["entry_zone_low"])
            z_high = float(tr["entry_zone_high"])
            if z_high > z_low:
                z_mid = round((z_high + z_low) / 2.0, 2)
                z_type = "SUPPORT" if str(tr.get("direction", "LONG")).upper() == "LONG" else "RESISTANCE"
                tf = str(tr.get("timeframe", "M15")).upper()
                ts = tr.get("file_timestamp") or str(tr.get("chart_date", ""))
                sig = (tf, ts, round(z_low, 3), round(z_high, 3), 1)
                if sig not in seen_snaps:
                    seen_snaps.add(sig)
                    synth = {
                        "symbol": sym_up,
                        "timeframe": tf,
                        "file_timestamp": ts,
                        "chart_date": tr.get("chart_date"),
                        "live_price_on_chart": tr.get("live_price_on_chart"),
                        "source": tr.get("source", "telegram"),
                        "_from_trade_signal": True,
                        "zones": [{
                            "name": f"TG {tr.get('direction', 'LONG')} Entry Zone",
                            "type": z_type,
                            "low": z_low,
                            "high": z_high,
                            "midline": z_mid,
                            "why_marked": tr.get("why_entered", ""),
                        }],
                    }
                    synth["_utc_ts"] = parse_snapshot_utc(synth)
                    dedup_snaps.append(synth)

    dedup_snaps.sort(key=lambda x: str(x.get("file_timestamp") or x.get("chart_date") or ""))
    return dedup_snaps, dedup_trades


def slice_candles_at_timestamp(
    df: Optional[pd.DataFrame],
    utc_ts: Optional[pd.Timestamp],
    min_bars: int = 35,
) -> Tuple[Optional[pd.DataFrame], bool]:
    """
    Slice candle DataFrame strictly up to `utc_ts` (no future lookahead).
    Returns (sliced_df, in_cache_window).
    """
    if df is None or df.empty:
        return None, False
    if utc_ts is None or "time" not in df.columns:
        return df, True

    times = pd.to_datetime(df["time"], errors="coerce")
    if getattr(times.dt, "tz", None) is not None:
        times = times.dt.tz_convert("UTC").dt.tz_localize(None)

    first_t = times.iloc[0]
    if pd.notna(first_t) and utc_ts < first_t:
        return None, False

    mask = times <= utc_ts
    sliced = df.loc[mask]
    if len(sliced) < min_bars:
        return None, False
    return sliced, True


# --------------------------------------------------------------------------- #
# Overlap & Evaluation Metrics
# --------------------------------------------------------------------------- #

def compute_overlap(z1_low: float, z1_high: float, z2_low: float, z2_high: float) -> float:
    """Overlap fraction relative to the smaller zone height."""
    inter_low = max(z1_low, z2_low)
    inter_high = min(z1_high, z2_high)
    if inter_high <= inter_low:
        return 0.0
    inter_len = inter_high - inter_low
    min_len = min(z1_high - z1_low, z2_high - z2_low)
    if min_len <= 0:
        return 0.0
    return inter_len / min_len


def compute_iou(z1_low: float, z1_high: float, z2_low: float, z2_high: float) -> float:
    """Intersection over Union for smooth optimization gradient."""
    inter_low = max(z1_low, z2_low)
    inter_high = min(z1_high, z2_high)
    if inter_high <= inter_low:
        return 0.0
    inter = inter_high - inter_low
    union = max(z1_high, z2_high) - min(z1_low, z2_low)
    return (inter / union) if union > 0 else 0.0


def _prepare_sliced_dataset(
    snapshots: List[Dict[str, Any]],
    df_m15: Optional[pd.DataFrame],
    df_h1: Optional[pd.DataFrame],
    df_h4: Optional[pd.DataFrame],
) -> List[Dict[str, Any]]:
    """Pre-slice all snapshots once before running hundreds of Optuna trials."""
    prepared = []
    for snap in snapshots:
        tf = snap.get("timeframe", "M15").upper()
        if tf not in ("M15", "H1", "H4"):
            continue
        utc_ts = snap.get("_utc_ts")
        if tf == "M15":
            df_slice, ok = slice_candles_at_timestamp(df_m15, utc_ts)
            h1_slice, _ = slice_candles_at_timestamp(df_h1, utc_ts, min_bars=15)
            htf_lvls = htf_swing_levels(h1_slice) if h1_slice is not None else []
        elif tf == "H1":
            df_slice, ok = slice_candles_at_timestamp(df_h1, utc_ts)
            htf_lvls = []
        else:
            df_slice, ok = slice_candles_at_timestamp(df_h4, utc_ts)
            htf_lvls = []

        if not ok or df_slice is None:
            continue

        live_p = float(snap.get("live_price_on_chart") or df_slice["close"].iloc[-1])
        prepared.append({
            "snap": snap,
            "tf": tf,
            "df": df_slice,
            "htf_levels": htf_lvls,
            "live_p": live_p,
        })
    return prepared


def _evaluate_tf_items(
    items: List[Dict[str, Any]],
    tf: str,
    symbol: str,
    m15_params: Optional[Dict[str, float]] = None,
    htf_params: Optional[Dict[str, float]] = None,
    use_ml: bool = False,
) -> Dict[str, Any]:
    """Evaluate a set of parameters on pre-sliced snapshots for a single timeframe."""
    is_gold = "XAU" in symbol.upper()
    is_jpy = "JPY" in symbol.upper()
    pip_scale = 1.0 if is_gold else (100.0 if is_jpy else 10000.0)

    total_zones = 0
    matched_zones = 0
    strict_matched = 0
    iou_sum = 0.0
    ce_errors: List[float] = []
    role_matches = 0
    cand_matched = 0

    for item in items:
        snap = item["snap"]
        df_slice = item["df"]
        live_p = item["live_p"]

        if tf == "M15":
            res = build_zones(
                df_slice, timeframe="M15", symbol=symbol,
                htf_levels=item["htf_levels"],
                params_override=m15_params, use_tuned=use_ml,
            )
            slots = select_slots(
                res["zones"], live_p, res["unit"], res["params"],
                symbol=symbol, use_ml=use_ml,
            )
            active_zones = [
                (z.bottom, z.top, z.mid, z.role)
                for z in (slots["upper"] + slots["lower"])
            ]
            all_cands = [(z.bottom, z.top, z.mid, z.role) for z in res["zones"]]
        else:
            tf_dz = calculate_tf_decision_zones(
                df_slice, live_p, tf.lower(), symbol=symbol,
                htf_params_override=htf_params, use_tuned=False,
            )
            active_zones = []
            for k in ("upper_dz_1", "upper_dz_2", "lower_dz_1", "lower_dz_2"):
                z = tf_dz.get(k)
                if z:
                    active_zones.append((
                        float(z["low"]),
                        float(z["high"]),
                        float(z.get("midline", (z["low"] + z["high"]) / 2.0)),
                        "RESISTANCE" if z.get("is_upper") else "SUPPORT",
                    ))
            all_cands = active_zones

        for tz in snap.get("zones", []):
            total_zones += 1
            t_high = float(tz.get("high", 0.0))
            t_low = float(tz.get("low", 0.0))
            t_mid = float(tz.get("midline", (t_high + t_low) / 2.0))
            t_type = str(tz.get("type", "RESISTANCE")).upper()

            best_ov = 0.0
            best_iou = 0.0
            best_z = None
            for mz in active_zones:
                ov = compute_overlap(t_low, t_high, mz[0], mz[1])
                iou = compute_iou(t_low, t_high, mz[0], mz[1])
                if iou > best_iou or (iou == best_iou and ov > best_ov):
                    best_iou = iou
                    best_ov = ov
                    best_z = mz

            iou_sum += best_iou
            if best_z is not None and best_ov >= 0.35:
                matched_zones += 1
                if best_iou >= 0.25:
                    strict_matched += 1
                ce_errors.append(abs(best_z[2] - t_mid) * pip_scale)
                if best_z[3] == t_type:
                    role_matches += 1

            # Track raw candidate recall as smooth tie-breaker
            if any(compute_overlap(t_low, t_high, cz[0], cz[1]) >= 0.35 for cz in all_cands):
                cand_matched += 1

    recall = (matched_zones / total_zones * 100.0) if total_zones > 0 else 0.0
    strict_recall = (strict_matched / total_zones * 100.0) if total_zones > 0 else 0.0
    cand_recall = (cand_matched / total_zones * 100.0) if total_zones > 0 else 0.0
    mean_iou = (iou_sum / total_zones) if total_zones > 0 else 0.0
    avg_ce = (sum(ce_errors) / len(ce_errors)) if ce_errors else 15.0
    return {
        "total_zones": total_zones,
        "matched_zones": matched_zones,
        "strict_matched": strict_matched,
        "recall_pct": recall,
        "strict_recall_pct": strict_recall,
        "cand_recall_pct": cand_recall,
        "mean_iou": mean_iou,
        "avg_ce_error": avg_ce,
        "role_matches": role_matches,
    }


def _objective_score(metrics: Dict[str, Any], penalty: float = 0.0) -> float:
    return (
        0.45 * metrics["strict_recall_pct"]
        + 0.20 * metrics["recall_pct"]
        + 0.10 * metrics["cand_recall_pct"]
        + 45.0 * metrics["mean_iou"]
        - 0.80 * min(metrics["avg_ce_error"], 20.0)
        - penalty
    )


# --------------------------------------------------------------------------- #
# Stage 1: Optuna Bayesian Parameter Optimizer
# --------------------------------------------------------------------------- #

def _optimize_m15(items: List[Dict[str, Any]], symbol: str, n_trials: int) -> Tuple[Dict[str, float], Dict[str, Any], Dict[str, Any]]:
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    base_p = dict(DEFAULT_TF_PARAMS["M15"])
    base_metrics = _evaluate_tf_items(items, "M15", symbol, m15_params=base_p)
    base_score = _objective_score(base_metrics)

    if not items:
        return base_p, base_metrics, base_metrics

    def objective(trial: optuna.Trial) -> float:
        cand = dict(base_p)
        cand["pivot_window"] = trial.suggest_int("pivot_window", 2, 5)
        cand["departure_bars"] = trial.suggest_int("departure_bars", 4, 14)
        cand["min_departure"] = round(trial.suggest_float("min_departure", 1.0, 2.8, step=0.1), 4)
        cand["cluster_bars"] = trial.suggest_int("cluster_bars", 2, 6)
        cand["cluster_tol"] = round(trial.suggest_float("cluster_tol", 0.25, 1.1, step=0.05), 4)
        cand["min_height"] = round(trial.suggest_float("min_height", 0.6, 1.5, step=0.1), 4)
        cand["max_height"] = round(trial.suggest_float("max_height", 1.8, 3.2, step=0.1), 4)
        cand["min_spacing"] = round(trial.suggest_float("min_spacing", 0.8, 2.4, step=0.1), 4)
        cand["max_distance"] = round(trial.suggest_float("max_distance", 12.0, 32.0, step=1.0), 4)
        cand["bos_bars"] = trial.suggest_int("bos_bars", 12, 36, step=4)

        metrics = _evaluate_tf_items(items, "M15", symbol, m15_params=cand)
        l2_pen = sum(
            ((cand[k] - base_p[k]) / max(1.0, abs(base_p[k]))) ** 2
            for k in ("pivot_window", "min_departure", "cluster_tol", "min_height", "max_height", "max_distance")
        ) * 0.15
        return _objective_score(metrics, penalty=l2_pen)

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=42))
    study.enqueue_trial({k: base_p[k] for k in (
        "pivot_window", "departure_bars", "min_departure", "cluster_bars",
        "cluster_tol", "min_height", "max_height", "min_spacing", "max_distance", "bos_bars",
    )})
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)

    best_p = dict(base_p)
    for k, v in study.best_params.items():
        best_p[k] = round(v, 4) if isinstance(v, float) else v
    tuned_metrics = _evaluate_tf_items(items, "M15", symbol, m15_params=best_p)
    if _objective_score(tuned_metrics) <= base_score:
        return base_p, base_metrics, base_metrics
    return best_p, base_metrics, tuned_metrics


def _optimize_htf(items: List[Dict[str, Any]], tf: str, symbol: str, n_trials: int) -> Tuple[Dict[str, float], Dict[str, Any], Dict[str, Any]]:
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    base_p = dict(DEFAULT_HTF_PARAMS[tf])
    base_metrics = _evaluate_tf_items(items, tf, symbol, htf_params=base_p)
    base_score = _objective_score(base_metrics)

    if not items:
        return base_p, base_metrics, base_metrics

    def objective(trial: optuna.Trial) -> float:
        cand = {
            "lookback": trial.suggest_int("lookback", 35, 130, step=5),
            "window": trial.suggest_int("window", 1, 4),
            "zone_half_atr": round(trial.suggest_float("zone_half_atr", 0.20, 0.45, step=0.05), 4),
            "min_buffer_atr": round(trial.suggest_float("min_buffer_atr", 0.15, 0.50, step=0.05), 4),
        }
        metrics = _evaluate_tf_items(items, tf, symbol, htf_params=cand)
        l2_pen = sum(
            ((cand[k] - base_p[k]) / max(1.0, abs(base_p[k]))) ** 2
            for k in ("lookback", "window", "zone_half_atr")
        ) * 0.15
        return _objective_score(metrics, penalty=l2_pen)

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=42))
    study.enqueue_trial(base_p)
    study.optimize(objective, n_trials=max(60, n_trials // 2), show_progress_bar=False)

    best_p = dict(base_p)
    for k, v in study.best_params.items():
        best_p[k] = round(v, 4) if isinstance(v, float) else v
    tuned_metrics = _evaluate_tf_items(items, tf, symbol, htf_params=best_p)
    if _objective_score(tuned_metrics) <= base_score:
        return base_p, base_metrics, base_metrics
    return best_p, base_metrics, tuned_metrics


# --------------------------------------------------------------------------- #
# Stage 1.5 / Stage 2: Tabular Zone Preference Scorer
# --------------------------------------------------------------------------- #

def _build_zone_training_dataset(
    m15_items: List[Dict[str, Any]],
    symbol: str,
    m15_params: Dict[str, float],
) -> Tuple[np.ndarray, np.ndarray]:
    X_rows: List[List[float]] = []
    y_rows: List[int] = []

    for item in m15_items:
        res = build_zones(
            item["df"], timeframe="M15", symbol=symbol,
            htf_levels=item["htf_levels"],
            params_override=m15_params, use_tuned=False,
        )
        unit_now = res["unit"]
        live_p = item["live_p"]
        trader_zones = item["snap"].get("zones", [])
        for z in res["zones"]:
            matched = any(
                compute_overlap(float(tz.get("low", 0)), float(tz.get("high", 0)), z.bottom, z.top) >= 0.35
                for tz in trader_zones
            )
            X_rows.append(extract_zone_features(z, unit_now=unit_now, price=live_p))
            y_rows.append(1 if matched else 0)

    if not X_rows:
        return np.empty((0, 11)), np.empty((0,), dtype=int)
    return np.asarray(X_rows, dtype=float), np.asarray(y_rows, dtype=int)


def train_zone_preference_model(
    m15_items: List[Dict[str, Any]],
    total_snapshots: int,
    symbol: str,
    m15_params: Dict[str, float],
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Auto-tiered ML preference scorer:
      < 30 total snapshots : Stage 1 (Optuna only)
      30-79 snapshots      : Stage 1.5 (L2 Logistic Regression linear weights)
      >= 80 snapshots      : Stage 2 (Shallow LightGBM saved to dna_zone_scorer.pkl)
    """
    if total_snapshots < STAGE_1_THRESHOLD_LOGREG:
        return {
            "stage": "STAGE_1_OPTUNA",
            "priority_weights": None,
            "scorer_model_path": None,
            "samples": 0,
            "status_note": f"{total_snapshots}/{STAGE_1_THRESHOLD_LOGREG} snapshots toward Stage 1.5 (LogReg), {total_snapshots}/{STAGE_2_THRESHOLD_LGBM} toward Stage 2 (LightGBM)",
        }

    X, y = _build_zone_training_dataset(m15_items, symbol, m15_params)
    if len(y) < 20 or len(np.unique(y)) < 2:
        return {
            "stage": "STAGE_1_OPTUNA",
            "priority_weights": None,
            "scorer_model_path": None,
            "samples": int(len(y)),
            "status_note": "Insufficient positive/negative class balance yet for Stage 1.5/2 classifier.",
        }

    if total_snapshots < STAGE_2_THRESHOLD_LGBM:
        from sklearn.linear_model import LogisticRegression
        clf = LogisticRegression(C=0.5, class_weight="balanced", max_iter=1000, random_state=42)
        clf.fit(X, y)
        return {
            "stage": "STAGE_1_5_LOGREG",
            "priority_weights": {
                "coef": [round(float(v), 6) for v in clf.coef_[0]],
                "intercept": round(float(clf.intercept_[0]), 6),
            },
            "scorer_model_path": None,
            "samples": int(len(y)),
            "status_note": f"Trained L2 Logistic Regression on {len(y)} candidate zones ({total_snapshots}/{STAGE_2_THRESHOLD_LGBM} toward LightGBM).",
        }

    import pickle
    import lightgbm as lgb

    model = lgb.LGBMClassifier(
        max_depth=3,
        num_leaves=7,
        n_estimators=60,
        learning_rate=0.05,
        min_child_samples=max(5, len(y) // 40),
        class_weight="balanced",
        random_state=42,
        verbose=-1,
    )
    model.fit(X, y)
    rel_path = os.path.relpath(SCORER_MODEL_PATH, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if not dry_run:
        os.makedirs(os.path.dirname(SCORER_MODEL_PATH), exist_ok=True)
        with open(SCORER_MODEL_PATH, "wb") as f:
            pickle.dump(model, f)

    return {
        "stage": "STAGE_2_LIGHTGBM",
        "priority_weights": None,
        "scorer_model_path": rel_path.replace("\\", "/"),
        "samples": int(len(y)),
        "status_note": f"Trained shallow LightGBM (max_depth=3) on {len(y)} candidate zones.",
    }


# --------------------------------------------------------------------------- #
# Full Weekend Calibration Orchestrator
# --------------------------------------------------------------------------- #

def _aggregate_metrics(per_tf: Dict[str, Dict[str, Any]]) -> Dict[str, float]:
    total = sum(m["total_zones"] for m in per_tf.values())
    matched = sum(m["matched_zones"] for m in per_tf.values())
    iou_w = sum(m["mean_iou"] * m["total_zones"] for m in per_tf.values())
    ce_w = sum(m["avg_ce_error"] * m["matched_zones"] for m in per_tf.values())
    return {
        "total_zones": total,
        "matched_zones": matched,
        "recall_pct": round((matched / total * 100.0), 2) if total > 0 else 0.0,
        "mean_iou": round((iou_w / total), 4) if total > 0 else 0.0,
        "avg_ce_error": round((ce_w / matched), 2) if matched > 0 else 0.0,
    }


def run_ml_calibration(
    snapshots: List[Dict[str, Any]],
    df_m15: Optional[pd.DataFrame],
    df_h1: Optional[pd.DataFrame],
    df_h4: Optional[pd.DataFrame],
    symbol: str = "XAUUSD",
    n_trials: int = 160,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Run full offline Bayesian + Tabular ML calibration and optionally persist `data/tuned_zone_params.json`."""
    prepared = _prepare_sliced_dataset(snapshots, df_m15, df_h1, df_h4)
    by_tf = {
        "M15": [p for p in prepared if p["tf"] == "M15"],
        "H1": [p for p in prepared if p["tf"] == "H1"],
        "H4": [p for p in prepared if p["tf"] == "H4"],
    }

    best_m15, base_m15_m, tuned_m15_m = _optimize_m15(by_tf["M15"], symbol, n_trials=n_trials)
    best_h1, base_h1_m, tuned_h1_m = _optimize_htf(by_tf["H1"], "H1", symbol, n_trials=n_trials)
    best_h4, base_h4_m, tuned_h4_m = _optimize_htf(by_tf["H4"], "H4", symbol, n_trials=n_trials)

    base_all = _aggregate_metrics({"M15": base_m15_m, "H1": base_h1_m, "H4": base_h4_m})
    tuned_all = _aggregate_metrics({"M15": tuned_m15_m, "H1": tuned_h1_m, "H4": tuned_h4_m})

    scorer_info = train_zone_preference_model(
        by_tf["M15"], total_snapshots=len(snapshots), symbol=symbol, m15_params=best_m15, dry_run=dry_run
    )

    payload = {
        "version": "1.0",
        "tuned_at": datetime.now(MYT).isoformat(),
        "symbol": symbol.upper(),
        "snapshots_total": len(snapshots),
        "snapshots_in_cache_window": len(prepared),
        "ml_stage": scorer_info["stage"],
        "ml_status_note": scorer_info["status_note"],
        "baseline_metrics": base_all,
        "tuned_metrics": tuned_all,
        "per_tf_metrics": {
            "M15": {"baseline": base_m15_m, "tuned": tuned_m15_m},
            "H1": {"baseline": base_h1_m, "tuned": tuned_h1_m},
            "H4": {"baseline": base_h4_m, "tuned": tuned_h4_m},
        },
        "m15_params": best_m15,
        "htf_params": {
            "H1": best_h1,
            "H4": best_h4,
        },
        "priority_weights": scorer_info["priority_weights"],
        "scorer_model_path": scorer_info["scorer_model_path"],
    }

    if not dry_run:
        os.makedirs(os.path.dirname(TUNED_PARAMS_PATH), exist_ok=True)
        with open(TUNED_PARAMS_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    return payload


def load_all_executed_trades(symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Load and deduplicate all ingested Trade Execution reviews from `trader_dna/data/trader_dna_profile.json`.
    If `symbol` is None or 'ALL', returns trades across all symbols.
    """
    sym_filter = None if (not symbol or str(symbol).upper() == "ALL") else str(symbol).upper()
    prof = load_trader_dna_profile()
    raw: List[Dict[str, Any]] = []
    if isinstance(prof, dict):
        for tr in prof.get("executed_trades", []) or []:
            if isinstance(tr, dict):
                raw.append(tr)
        for entry in prof.get("history", []) or []:
            if isinstance(entry, dict) and (
                entry.get("mode") == "TRADE_EXECUTION"
                or ("entry_price" in entry and not entry.get("zones"))
            ):
                raw.append(entry)

    seen = set()
    dedup: List[Dict[str, Any]] = []
    for tr in raw:
        sym = str(tr.get("symbol", "XAUUSD")).upper()
        if sym_filter and sym != sym_filter:
            continue
        sig = (
            sym,
            str(tr.get("direction", "")).upper(),
            round(float(tr.get("entry_price") or 0.0), 4),
            str(tr.get("chart_date") or tr.get("file_timestamp") or ""),
        )
        if sig in seen:
            continue
        seen.add(sig)
        copy_tr = dict(tr)
        copy_tr["symbol"] = sym
        dedup.append(copy_tr)

    dedup.sort(key=lambda x: str(x.get("file_timestamp") or x.get("chart_date") or ""))
    return dedup


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(val)))


def _compute_h1_atr_at(df_h1: Optional[pd.DataFrame], ts_utc: Optional[pd.Timestamp]) -> Optional[float]:
    if df_h1 is None or df_h1.empty or ts_utc is None:
        return None
    try:
        sub = slice_candles_at_timestamp(df_h1, ts_utc)
        if sub is None or len(sub) < 15:
            return None
        hi = sub["high"].astype(float)
        lo = sub["low"].astype(float)
        cl = sub["close"].astype(float).shift(1)
        tr = pd.concat([(hi - lo), (hi - cl).abs(), (lo - cl).abs()], axis=1).max(axis=1)
        atr = float(tr.tail(14).mean())
        return atr if np.isfinite(atr) and atr > 0 else None
    except Exception:
        return None


def _calibrate_numeric_entry_params(
    trades: List[Dict[str, Any]],
    alpha: float,
    df_h1_by_symbol: Optional[Dict[str, pd.DataFrame]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Compute blended numeric entry/SL/TP/trigger params + empirical summary stats."""
    base = dict(DEFAULT_ENTRY_PARAMS)
    rr_vals: List[float] = []
    sl_atr_vals: List[float] = []
    wick_vals: List[float] = []
    disp_flags: List[bool] = []

    for tr in trades:
        rr = tr.get("risk_reward")
        if rr is not None:
            try:
                rr_f = float(rr)
                if 0.5 <= rr_f <= 12.0:
                    rr_vals.append(rr_f)
            except (TypeError, ValueError):
                pass

        tc = tr.get("trigger_candle") or {}
        if isinstance(tc, dict):
            wr = tc.get("wick_rejection_ratio")
            if wr is not None:
                try:
                    wr_f = float(wr)
                    if 0.15 <= wr_f <= 0.95:
                        wick_vals.append(wr_f)
                except (TypeError, ValueError):
                    pass
            if "displacement_confirmed" in tc:
                disp_flags.append(bool(tc["displacement_confirmed"]))

        # Estimate empirical SL distance in H1 ATR units when candle cache covers trade date
        ep = tr.get("entry_price")
        sl = tr.get("stop_loss")
        sym = str(tr.get("symbol", "XAUUSD")).upper()
        df_h1 = (df_h1_by_symbol or {}).get(sym)
        if ep is not None and sl is not None and df_h1 is not None:
            try:
                risk_dist = abs(float(ep) - float(sl))
                atr = _compute_h1_atr_at(df_h1, parse_snapshot_utc(tr))
                if atr and risk_dist > 0:
                    ratio = risk_dist / atr
                    if 0.25 <= ratio <= 4.0:
                        sl_atr_vals.append(ratio)
            except (TypeError, ValueError):
                pass

    emp_rr = round(float(np.median(rr_vals)), 2) if rr_vals else None
    emp_sl_atr = round(float(np.median(sl_atr_vals)), 3) if sl_atr_vals else None
    emp_wick = round(float(np.median(wick_vals)), 2) if wick_vals else None
    emp_disp_rate = round(sum(disp_flags) / len(disp_flags), 2) if disp_flags else None

    effective = dict(base)
    if alpha > 0.0:
        if emp_sl_atr is not None:
            blended_sl = (1.0 - alpha) * base["default_sl_buf_atr"] + alpha * _clamp(emp_sl_atr, 0.55, 1.50)
            effective["default_sl_buf_atr"] = round(_clamp(blended_sl, 0.60, 1.40), 3)
            effective["max_sl_dist_atr"] = round(_clamp(max(2.0, effective["default_sl_buf_atr"] * 2.15), 1.70, 2.80), 3)
        if emp_rr is not None:
            # Safety TP1 stays practical (45%-55% of full target R:R), TP2 tracks median R:R safely
            target_tp1 = _clamp(emp_rr * 0.45, 0.90, 1.80)
            target_tp2 = _clamp(emp_rr * 0.85, 1.60, 3.50)
            effective["tp1_rr"] = round((1.0 - alpha) * base["tp1_rr"] + alpha * target_tp1, 2)
            effective["tp2_rr"] = round(max(effective["tp1_rr"] + 0.65, (1.0 - alpha) * base["tp2_rr"] + alpha * target_tp2), 2)
            effective["min_tp1_rr"] = round(_clamp(effective["tp1_rr"] * 0.8, 0.75, 1.30), 2)
        if emp_wick is not None:
            effective["wick_rejection_ratio"] = round(
                _clamp((1.0 - alpha) * base["wick_rejection_ratio"] + alpha * emp_wick, 0.40, 0.75), 2
            )
        if emp_disp_rate is not None:
            effective["require_displacement"] = emp_disp_rate >= 0.55

    stats = {
        "median_rr": emp_rr,
        "median_sl_atr": emp_sl_atr,
        "median_wick_ratio": emp_wick,
        "displacement_rate": emp_disp_rate,
        "sl_atr_samples": len(sl_atr_vals),
    }
    return effective, stats


def calibrate_entry_dna(
    trades: Optional[List[Dict[str, Any]]] = None,
    df_h1_by_symbol: Optional[Dict[str, pd.DataFrame]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Offline weekend calibrator for Trader Entry Execution DNA -> `data/tuned_entry_dna.json`.
    Mirrors Zone ML calibration with safe sample-size scaling:
      * 0 trades    : STAGE_0_NONE (factory defaults)
      * 1-4 trades  : STAGE_1_PLAYBOOK_ONLY (AI Battle-Plan prompt Playbook active; factory SL/TP math)
      * 5-14 trades : STAGE_2_BLENDED (Playbook + shrunk empirical SL-ATR / TP-R:R / wick trigger math)
      * 15+ trades  : STAGE_3_EMPIRICAL (full empirical calibration + per-symbol overrides)
    """
    from collections import Counter

    all_trades = trades if trades is not None else load_all_executed_trades(symbol="ALL")
    n = len(all_trades)

    if n == 0:
        return {
            "version": "1.0",
            "tuned_at": datetime.now(MYT).isoformat(),
            "trades_total": 0,
            "calibration_stage": "STAGE_0_NONE",
            "blend_alpha": 0.0,
            "status_note": f"0/{ENTRY_STAGE_BLENDED_MIN} trades toward Stage 2 (Blended Math). Snap Trade reviews & run /dna.",
            "effective_params": dict(DEFAULT_ENTRY_PARAMS),
            "per_symbol_params": {},
            "empirical_stats": {},
            "playbook": {
                "dominant_patterns": [],
                "invalidation_rules": [],
                "tp_rules": [],
                "example_setups": [],
            },
        }

    if n < ENTRY_STAGE_BLENDED_MIN:
        stage = "STAGE_1_PLAYBOOK_ONLY"
        alpha = 0.0
        note = (
            f"Playbook prompt active ({n} trade{'s' if n != 1 else ''}); "
            f"{n}/{ENTRY_STAGE_BLENDED_MIN} toward Stage 2 (Blended SL/TP Math), "
            f"{n}/{ENTRY_STAGE_FULL_MIN} toward Stage 3 (Full Empirical)."
        )
    elif n < ENTRY_STAGE_FULL_MIN:
        stage = "STAGE_2_BLENDED"
        alpha = round(min(0.75, (n - (ENTRY_STAGE_BLENDED_MIN - 1)) / 10.0), 2)
        note = (
            f"Blended Entry Math active (alpha={alpha:.2f}, {n} trades); "
            f"{n}/{ENTRY_STAGE_FULL_MIN} toward Stage 3 (Full Empirical)."
        )
    else:
        stage = "STAGE_3_EMPIRICAL"
        alpha = 0.85
        note = f"Full Empirical Entry Calibration active (alpha={alpha:.2f}, {n} trades)."

    effective_params, emp_stats = _calibrate_numeric_entry_params(all_trades, alpha=alpha, df_h1_by_symbol=df_h1_by_symbol)

    # Per-symbol calibration overrides at Stage 3 when a pair has >= 5 trades
    per_symbol_params: Dict[str, Dict[str, Any]] = {}
    if stage == "STAGE_3_EMPIRICAL":
        by_sym: Dict[str, List[Dict[str, Any]]] = {}
        for tr in all_trades:
            by_sym.setdefault(str(tr.get("symbol", "XAUUSD")).upper(), []).append(tr)
        for s_key, s_trades in by_sym.items():
            if len(s_trades) >= ENTRY_STAGE_BLENDED_MIN:
                s_eff, _ = _calibrate_numeric_entry_params(s_trades, alpha=0.80, df_h1_by_symbol=df_h1_by_symbol)
                per_symbol_params[s_key] = s_eff

    # Distill qualitative playbook rules & top concise examples for Gemini Battle-Plan prompt
    pat_counter = Counter(str(tr.get("pattern_type") or "UNCLASSIFIED").upper() for tr in all_trades)
    dir_counter = Counter(str(tr.get("direction") or "UNKNOWN").upper() for tr in all_trades)
    tf_counter = Counter(str(tr.get("timeframe") or "M15").upper() for tr in all_trades)

    dominant_patterns = [
        {"pattern": p, "count": c, "share_pct": round(c / n * 100.0, 1)}
        for p, c in pat_counter.most_common(5)
    ]

    inv_rules: List[str] = []
    tp_rules: List[str] = []
    for tr in reversed(all_trades):
        inv = str(tr.get("invalidation_logic") or "").strip()
        if inv and inv not in inv_rules and len(inv_rules) < 4:
            inv_rules.append(inv)
        tpl = str(tr.get("tp_logic") or "").strip()
        if tpl and tpl not in tp_rules and len(tp_rules) < 4:
            tp_rules.append(tpl)

    # Select up to 4 most recent / clean examples
    example_setups: List[Dict[str, Any]] = []
    for tr in list(reversed(all_trades))[:4]:
        tc = tr.get("trigger_candle") or {}
        example_setups.append({
            "symbol": str(tr.get("symbol", "XAUUSD")).upper(),
            "timeframe": str(tr.get("timeframe", "M15")).upper(),
            "direction": str(tr.get("direction", "")).upper(),
            "pattern": str(tr.get("pattern_type", "")),
            "risk_reward": tr.get("risk_reward"),
            "wick_rejection_type": tc.get("wick_rejection_type") if isinstance(tc, dict) else None,
            "wick_rejection_ratio": tc.get("wick_rejection_ratio") if isinstance(tc, dict) else None,
            "invalidation_logic": tr.get("invalidation_logic"),
            "tp_logic": tr.get("tp_logic"),
            "why_entered": tr.get("why_entered") or tr.get("setup_description"),
        })

    payload = {
        "version": "1.0",
        "tuned_at": datetime.now(MYT).isoformat(),
        "trades_total": n,
        "calibration_stage": stage,
        "blend_alpha": alpha,
        "status_note": note,
        "direction_counts": dict(dir_counter),
        "timeframe_counts": dict(tf_counter),
        "effective_params": effective_params,
        "per_symbol_params": per_symbol_params,
        "empirical_stats": emp_stats,
        "playbook": {
            "dominant_patterns": dominant_patterns,
            "invalidation_rules": inv_rules,
            "tp_rules": tp_rules,
            "example_setups": example_setups,
        },
    }

    if not dry_run:
        os.makedirs(os.path.dirname(TUNED_ENTRY_DNA_PATH), exist_ok=True)
        with open(TUNED_ENTRY_DNA_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

    return payload


def reset_tuned_params() -> bool:
    """Remove persisted tuned zone & entry DNA configs and scorer model to revert to factory defaults."""
    removed = False
    for p in (TUNED_PARAMS_PATH, SCORER_MODEL_PATH, TUNED_ENTRY_DNA_PATH):
        if os.path.isfile(p):
            os.remove(p)
            removed = True
    return removed

