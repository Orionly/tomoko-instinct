# Tomoko Instinct - Zone Lifecycle Engine
# Decision-support zones with strength grading, retest tracking and swap (S/R flip) lifecycle.
#
# Trader rules encoded here (see implementation_plan.md):
#   * Fresh / untested zone is best. Tradeable retests: 1st, 2nd, (3rd only if HTF-aligned).
#     A 4th retest is NEVER valid -> zone becomes EXHAUSTED.
#   * SWEEP  = candle wicks beyond the zone but its body closes back inside -> counts as a test, zone holds.
#   * BREAK  = candle body closes beyond the far edge -> zone flips role and becomes a SWAP zone.
#   * SWAP zones stay until they are tested (no time expiry). Retest count restarts at 0 in the new role.
#   * A SWAP zone that is broken again (back through) is INVALID and discarded.
#
# The engine is stateless: every call replays the supplied candles so zone state is deterministic
# and survives restarts. It never emits entry signals - only where, how strong, which retest, what state.

import copy
import json
import math
import os
import pickle
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TUNED_PARAMS_PATH = os.path.join(ROOT_DIR, "data", "tuned_zone_params.json")
SCORER_MODEL_PATH = os.path.join(ROOT_DIR, "data", "models", "dna_zone_scorer.pkl")

# --------------------------------------------------------------------------- #
# Tunable parameters per timeframe. "unit" = rolling mean True Range over
# `unit_period` bars (a full-day average on M15, so Asian-session ATR collapse
# does not shrink zones).
# --------------------------------------------------------------------------- #
DEFAULT_TF_PARAMS: Dict[str, Dict[str, float]] = {
    "M15": {
        "pivot_window": 3,        # bars each side for a swing pivot
        "departure_bars": 8,      # bars after origin to measure departure
        "min_departure": 2.0,     # min departure (units) to qualify as a zone
        "cluster_bars": 4,        # bars each side to collect the wick/body cluster
        "cluster_tol": 0.6,       # wick proximity (units) to join the cluster
        "min_height": 0.9,        # zone height clamp (units)
        "max_height": 2.6,
        "retest_exit": 0.6,       # price must leave by this (units) before a new touch counts
        "min_spacing": 1.5,       # min gap between kept zones on the same side (units)
        "range_gap": 1.2,         # U1/L1 gap below this (units) -> RANGE
        "max_distance": 14.0,     # ignore zones further than this from price (units)
        "unit_period": 96,        # one trading day of M15 bars
        "bos_bars": 24,           # bars after origin to look for a break of structure
        "proximity": 1.0,         # APPROACHING radius (units)
    },
}

TF_PARAMS: Dict[str, Dict[str, float]] = copy.deepcopy(DEFAULT_TF_PARAMS)

_TUNED_CACHE: Dict[str, Any] = {"mtime": None, "data": None}
_SCORER_CACHE: Dict[str, Any] = {"mtime": None, "model": None}


def load_tuned_config() -> Optional[Dict[str, Any]]:
    """Load calibrated weekend ML config from data/tuned_zone_params.json (mtime-cached)."""
    try:
        if not os.path.isfile(TUNED_PARAMS_PATH):
            _TUNED_CACHE["mtime"] = None
            _TUNED_CACHE["data"] = None
            return None
        mtime = os.path.getmtime(TUNED_PARAMS_PATH)
        if _TUNED_CACHE["mtime"] != mtime:
            with open(TUNED_PARAMS_PATH, "r", encoding="utf-8") as f:
                _TUNED_CACHE["data"] = json.load(f)
            _TUNED_CACHE["mtime"] = mtime
        return _TUNED_CACHE["data"]
    except Exception:
        return None


def _load_scorer_model():
    """Load frozen LightGBM / sklearn zone preference scorer if present (mtime-cached)."""
    try:
        if not os.path.isfile(SCORER_MODEL_PATH):
            _SCORER_CACHE["mtime"] = None
            _SCORER_CACHE["model"] = None
            return None
        mtime = os.path.getmtime(SCORER_MODEL_PATH)
        if _SCORER_CACHE["mtime"] != mtime:
            with open(SCORER_MODEL_PATH, "rb") as f:
                _SCORER_CACHE["model"] = pickle.load(f)
            _SCORER_CACHE["mtime"] = mtime
        return _SCORER_CACHE["model"]
    except Exception:
        return None


def get_tf_params(
    timeframe: str = "M15",
    override: Optional[Dict[str, float]] = None,
    use_tuned: bool = True,
) -> Dict[str, float]:
    """Return effective parameters for a timeframe, merging calibrated overrides if available."""
    tf = timeframe.upper()
    base = dict(DEFAULT_TF_PARAMS.get(tf, DEFAULT_TF_PARAMS["M15"]))
    if use_tuned:
        tuned = load_tuned_config()
        if tuned and isinstance(tuned.get("m15_params"), dict) and tf == "M15":
            for k, v in tuned["m15_params"].items():
                if k in base and v is not None:
                    base[k] = float(v)
    if override:
        for k, v in override.items():
            if k in base and v is not None:
                base[k] = float(v)
    return base

GRADE_STRONG = 70
GRADE_MEDIUM = 40


def _f(v, default=0.0) -> float:
    try:
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except (TypeError, ValueError):
        return default


def _ordinal(n: int) -> str:
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(n, f"{n}th")


@dataclass
class Zone:
    kind: str                 # origin type: "SUPPLY" or "DEMAND"
    top: float
    bottom: float
    origin_idx: int
    origin_time: Any
    departure: float          # in units
    bos: bool
    volume_ratio: Optional[float]
    # lifecycle
    role: str = ""            # RESISTANCE / SUPPORT (current role)
    is_swap: bool = False
    retest_count: int = 0
    swept_count: int = 0
    status: str = "FRESH"     # FRESH / ACTIVE / TESTING / EXHAUSTED / INVALID
    inside_now: bool = False
    broke_idx: Optional[int] = None
    last_touch_idx: Optional[int] = None
    # scoring
    score: int = 0
    grade: str = "WEAK"
    confluences: List[str] = field(default_factory=list)
    score_breakdown: List[Dict[str, Any]] = field(default_factory=list)
    score_rubric: str = ""
    # risk classification (3-tier execution hierarchy)
    risk_tier: str = "MED_RISK"       # HIGH_RISK / MED_RISK / LOW_RISK
    risk_badge: str = ""
    risk_desc: str = ""

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2.0


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #
def _unit_series(df: pd.DataFrame, period: int) -> np.ndarray:
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    prev_c = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum.reduce([h - l, np.abs(h - prev_c), np.abs(l - prev_c)])
    s = pd.Series(tr).rolling(period, min_periods=max(5, period // 4)).mean().bfill()
    return s.values


def _detect_candidates(df: pd.DataFrame, unit: np.ndarray, p: Dict[str, float]) -> List[Zone]:
    o, h, l, c = (df[k].values.astype(float) for k in ("open", "high", "low", "close"))
    vol = df["volume"].values.astype(float) if "volume" in df.columns else np.zeros(len(df))
    has_vol = np.count_nonzero(vol) > len(vol) * 0.5
    times = df["time"].values if "time" in df.columns else df.index.values
    n = len(df)
    w = int(p["pivot_window"])
    dep_bars = int(p["departure_bars"])
    cb = int(p["cluster_bars"])
    bos_bars = int(p["bos_bars"])
    body_top = np.maximum(o, c)
    body_bot = np.minimum(o, c)

    pivot_highs: List[int] = []
    pivot_lows: List[int] = []
    out: List[Zone] = []

    for i in range(w, n - 1):
        u = unit[i] if unit[i] > 0 else 1e-9
        right = min(n, i + w + 1)
        # A pivot needs `w` confirmed bars to its right, except we still allow
        # detection once the departure has clearly happened.
        left_ok_h = h[i] >= h[i - w:i].max()
        left_ok_l = l[i] <= l[i - w:i].min()
        right_ok_h = (right - i - 1) >= 1 and h[i] >= h[i + 1:right].max()
        right_ok_l = (right - i - 1) >= 1 and l[i] <= l[i + 1:right].min()

        # Volume ratio for the origin bar(s)
        vr = None
        if has_vol:
            base = vol[max(0, i - 96):i]
            base = base[base > 0]
            if len(base) > 10 and base.mean() > 0:
                vr = float(vol[max(0, i - 1):i + 2].mean() / base.mean())

        # ---- Supply (swing high + bearish departure) ----
        if left_ok_h and right_ok_h:
            end = min(n, i + 1 + dep_bars)
            if end > i + 1:
                post_low = l[i + 1:end].min()
                dep = (h[i] - post_low) / u
                if dep >= p["min_departure"]:
                    lo_i, hi_i = max(0, i - cb), min(n, i + cb + 1)
                    idxs = [j for j in range(lo_i, hi_i) if abs(h[j] - h[i]) <= p["cluster_tol"] * u]
                    top = max(h[j] for j in idxs)
                    bot = min(body_top[j] for j in idxs)
                    top, bot = _clamp_box(top, bot, u, p, keep="top")
                    prior = [k for k in pivot_lows if k < i]
                    bos = False
                    if prior:
                        bos_end = min(n, i + 1 + bos_bars)
                        bos = l[i + 1:bos_end].min() < l[prior[-1]]
                    out.append(Zone("SUPPLY", top, bot, i, times[i], dep, bos, vr))
            pivot_highs.append(i)

        # ---- Demand (swing low + bullish departure) ----
        if left_ok_l and right_ok_l:
            end = min(n, i + 1 + dep_bars)
            if end > i + 1:
                post_high = h[i + 1:end].max()
                dep = (post_high - l[i]) / u
                if dep >= p["min_departure"]:
                    lo_i, hi_i = max(0, i - cb), min(n, i + cb + 1)
                    idxs = [j for j in range(lo_i, hi_i) if abs(l[j] - l[i]) <= p["cluster_tol"] * u]
                    bot = min(l[j] for j in idxs)
                    top = max(body_bot[j] for j in idxs)
                    top, bot = _clamp_box(top, bot, u, p, keep="bottom")
                    prior = [k for k in pivot_highs if k < i]
                    bos = False
                    if prior:
                        bos_end = min(n, i + 1 + bos_bars)
                        bos = h[i + 1:bos_end].max() > h[prior[-1]]
                    out.append(Zone("DEMAND", top, bot, i, times[i], dep, bos, vr))
            pivot_lows.append(i)

    return out


def _clamp_box(top: float, bot: float, u: float, p: Dict[str, float], keep: str):
    """Clamp height while ALWAYS preserving the wick extreme (the invalidation edge)."""
    if bot > top:
        bot, top = top, bot
    hmin, hmax = p["min_height"] * u, p["max_height"] * u
    height = top - bot
    if keep == "top":            # supply: wick high is sacred, adjust body side (bottom)
        if height < hmin:
            bot = top - hmin
        elif height > hmax:
            bot = top - hmax
    else:                        # demand: wick low is sacred, adjust body side (top)
        if height < hmin:
            top = bot + hmin
        elif height > hmax:
            top = bot + hmax
    return top, bot


# --------------------------------------------------------------------------- #
# Lifecycle replay
# --------------------------------------------------------------------------- #
def _replay(z: Zone, df: pd.DataFrame, unit: np.ndarray, p: Dict[str, float], start: int) -> None:
    """Walk forward bar-by-bar from the zone origin and derive its lifecycle state.

    A 'visit' starts when price touches the zone after having departed by >= retest_exit units,
    and ends when price departs again. Each visit = one retest. A sweep (wick beyond the far
    edge, body back inside) is flagged once per visit. A body close beyond the far edge = BREAK.
    """
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    n = len(df)
    z.role = "RESISTANCE" if z.kind == "SUPPLY" else "SUPPORT"
    armed = False        # price has departed far enough for the next touch to count
    in_visit = False     # currently inside a counted visit
    visit_swept = False

    for k in range(start, n):
        exit_d = p["retest_exit"] * (unit[k] if unit[k] > 0 else 1e-9)
        if z.role == "RESISTANCE":
            if c[k] > z.top:                              # BREAK upward
                if z.is_swap:
                    z.status = "INVALID"
                    return
                z.is_swap, z.role, z.broke_idx = True, "SUPPORT", k
                z.retest_count, z.swept_count = 0, 0
                armed = in_visit = visit_swept = False
                continue
            touched = h[k] >= z.bottom
            beyond = h[k] > z.top
            departed = (c[k] if touched else l[k]) <= z.bottom - exit_d
        else:
            if c[k] < z.bottom:                           # BREAK downward
                if z.is_swap:
                    z.status = "INVALID"
                    return
                z.is_swap, z.role, z.broke_idx = True, "RESISTANCE", k
                z.retest_count, z.swept_count = 0, 0
                armed = in_visit = visit_swept = False
                continue
            touched = l[k] <= z.top
            beyond = l[k] < z.bottom
            departed = (c[k] if touched else h[k]) >= z.top + exit_d

        if touched and armed and not in_visit:
            z.retest_count += 1
            z.last_touch_idx = k
            in_visit, armed, visit_swept = True, False, False
        if touched and in_visit and beyond and not visit_swept:
            z.swept_count += 1
            visit_swept = True
        if departed:
            armed, in_visit = True, False

    last_c = c[n - 1]
    z.inside_now = z.bottom <= last_c <= z.top
    z.in_visit = in_visit
    if z.retest_count >= 4 or (z.retest_count >= 3 and not in_visit):
        z.status = "EXHAUSTED"
    elif z.inside_now:
        z.status = "TESTING"
    elif z.retest_count == 0:
        z.status = "FRESH"
    else:
        z.status = "ACTIVE"


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def _round_step(symbol: str) -> float:
    s = symbol.upper()
    if "XAU" in s:
        return 50.0
    if "JPY" in s:
        return 0.50
    return 0.0050


def _score(z: Zone, anchors: List[Dict[str, Any]], htf_levels: List[float], symbol: str, unit_now: float) -> None:
    conf: List[str] = []
    dep_pts = min(1.0, z.departure / 5.0) * 35.0
    conf.append(f"Departure {z.departure:.1f}x")
    bos_pts = 25.0 if z.bos else 0.0
    if z.bos:
        conf.append("Broke structure (BOS)")

    pad = 0.35 * unit_now
    c_pts = 0.0
    c_labels: List[str] = []
    for a in anchors:
        price = _f(a.get("price"))
        if price > 0 and (z.bottom - pad) <= price <= (z.top + pad):
            c_pts += 12.0
            lbl = a.get("label", a.get("type", "Key level"))
            conf.append(lbl)
            c_labels.append(lbl)
    step = _round_step(symbol)
    rn = round(z.mid / step) * step
    if (z.bottom - pad) <= rn <= (z.top + pad):
        c_pts += 8.0
        conf.append(f"Round number {rn:g}")
        c_labels.append(f"Round #{rn:g}")
    if any((z.bottom - pad) <= lv <= (z.top + pad) for lv in htf_levels):
        c_pts += 10.0
        conf.append("HTF swing confluence")
        c_labels.append("HTF Swing")
    c_pts = min(25.0, c_pts)

    if z.volume_ratio is not None:
        v_pts = max(0.0, min(1.0, (z.volume_ratio - 0.8) / 1.2)) * 15.0
        if z.volume_ratio >= 1.3:
            conf.append(f"Volume {z.volume_ratio:.1f}x avg")
        total = dep_pts + bos_pts + c_pts + v_pts
        scale = 1.0
    else:
        v_pts = 0.0
        scale = 100.0 / 85.0
        total = (dep_pts + bos_pts + c_pts) * scale

    z.score = int(round(max(0.0, min(100.0, total))))
    z.grade = "STRONG" if z.score >= GRADE_STRONG else ("MEDIUM" if z.score >= GRADE_MEDIUM else "WEAK")
    z.confluences = conf

    # Build human-explainable point breakdown for UI card
    bd: List[Dict[str, Any]] = [
        {
            "label": "Departure Impulse",
            "detail": f"{z.departure:.1f}x ATR move",
            "pts": f"+{round(dep_pts * scale):.0f}",
            "max": int(round(35 * scale)),
        },
        {
            "label": "Break of Structure",
            "detail": "Confirmed BOS" if z.bos else "No BOS",
            "pts": f"+{round(bos_pts * scale):.0f}",
            "max": int(round(25 * scale)),
        },
        {
            "label": "Key Confluences",
            "detail": ", ".join(c_labels[:2]) if c_labels else "Standalone shelf",
            "pts": f"+{round(c_pts * scale):.0f}",
            "max": int(round(25 * scale)),
        },
    ]
    if z.volume_ratio is not None:
        bd.append({
            "label": "Origin Volume",
            "detail": f"{z.volume_ratio:.1f}x avg vol",
            "pts": f"+{round(v_pts):.0f}",
            "max": 15,
        })
    z.score_breakdown = bd
    z.score_rubric = "STRONG ≥70 · MEDIUM 40–69 · WEAK <40 (Filtered) | Max 100: Departure (35) + BOS (25) + Confluence (25) + Vol (15)"


def htf_swing_levels(df: Optional[pd.DataFrame], window: int = 3, lookback: int = 200) -> List[float]:
    """Plain swing highs/lows from a higher-timeframe frame (used only as confluence)."""
    if df is None or len(df) < window * 2 + 1:
        return []
    sub = df.iloc[-lookback:]
    h, l = sub["high"].values, sub["low"].values
    out = []
    for i in range(window, len(sub) - window):
        if h[i] >= h[i - window:i + window + 1].max():
            out.append(float(h[i]))
        if l[i] <= l[i - window:i + window + 1].min():
            out.append(float(l[i]))
    return out


# --------------------------------------------------------------------------- #
# Public: build all zones with lifecycle + score
# --------------------------------------------------------------------------- #
def build_zones(
    df: pd.DataFrame,
    timeframe: str = "M15",
    symbol: str = "XAUUSD",
    anchors: Optional[List[Dict[str, Any]]] = None,
    htf_levels: Optional[List[float]] = None,
    params_override: Optional[Dict[str, float]] = None,
    use_tuned: bool = True,
) -> Dict[str, Any]:
    p = get_tf_params(timeframe, override=params_override, use_tuned=use_tuned)
    df = df.reset_index(drop=False) if "time" not in df.columns else df.reset_index(drop=True)
    unit = _unit_series(df, int(p["unit_period"]))
    unit_now = float(unit[-1]) if len(unit) else 0.0

    cands = _detect_candidates(df, unit, p)
    live: List[Zone] = []
    for z in cands:
        start = min(len(df), z.origin_idx + int(p["cluster_bars"]) + 1)
        _replay(z, df, unit, p, start)
        if z.status == "INVALID":
            continue
        _score(z, anchors or [], htf_levels or [], symbol, unit_now)
        live.append(z)

    # Dedupe overlapping zones with the same current role: keep the better one
    live.sort(key=lambda z: _priority(z, unit_now=unit_now, use_ml=use_tuned), reverse=True)
    kept: List[Zone] = []
    for z in live:
        if any(k.role == z.role and _gap(k, z) < 0 for k in kept):
            continue
        kept.append(z)

    return {"zones": kept, "unit": unit_now, "params": p}


def extract_zone_features(
    z: Zone,
    unit_now: float = 1.0,
    price: float = 0.0,
) -> List[float]:
    """Fixed-order numeric feature vector shared identically by training and live inference."""
    u = unit_now if unit_now > 0 else 1.0
    height_atr = (z.top - z.bottom) / u
    dist_atr = (abs(z.mid - price) / u) if price > 0 else 0.0
    confs = " ".join(z.confluences).lower()
    has_htf = 1.0 if "htf swing" in confs else 0.0
    has_round = 1.0 if "round number" in confs else 0.0
    vol_r = float(z.volume_ratio) if z.volume_ratio is not None else 1.0
    return [
        float(z.departure),
        1.0 if z.bos else 0.0,
        1.0 if z.is_swap else 0.0,
        float(z.retest_count),
        float(z.swept_count),
        float(height_atr),
        float(dist_atr),
        has_htf,
        has_round,
        vol_r,
        float(z.score),
    ]


def _priority(
    z: Zone,
    unit_now: float = 1.0,
    price: float = 0.0,
    use_ml: bool = True,
) -> float:
    """Rank candidate zones using baseline rubric, optionally blended with weekend-calibrated ML."""
    base_prio = z.score - 8.0 * z.retest_count + (5.0 if z.is_swap and z.retest_count == 0 else 0.0)
    if not use_ml:
        return base_prio

    # Stage 2: Frozen LightGBM / sklearn probability model
    model = _load_scorer_model()
    if model is not None:
        try:
            feats = np.asarray([extract_zone_features(z, unit_now=unit_now, price=price)], dtype=float)
            prob = float(model.predict_proba(feats)[0][1])
            return 0.50 * base_prio + 0.50 * (prob * 100.0)
        except Exception:
            pass

    # Stage 1.5: Tuned linear priority weights from data/tuned_zone_params.json
    tuned = load_tuned_config()
    weights = (tuned or {}).get("priority_weights")
    if isinstance(weights, dict):
        try:
            feats = extract_zone_features(z, unit_now=unit_now, price=price)
            coef = weights.get("coef", [])
            intercept = float(weights.get("intercept", 0.0))
            if len(coef) == len(feats):
                z_val = intercept + sum(float(c) * float(f) for c, f in zip(coef, feats))
                z_val = max(-20.0, min(20.0, z_val))
                prob = 1.0 / (1.0 + math.exp(-z_val))
                return 0.55 * base_prio + 0.45 * (prob * 100.0)
        except Exception:
            pass

    return base_prio


def _gap(a: Zone, b: Zone) -> float:
    """Distance between two boxes (negative if they overlap)."""
    return max(a.bottom, b.bottom) - min(a.top, b.top)


def select_slots(
    zones: List[Zone],
    price: float,
    unit: float,
    p: Dict[str, float],
    symbol: str = "EURUSD",
    use_ml: bool = True,
) -> Dict[str, Any]:
    """Pick Upper/Lower 1 & 2 using strength, freshness and spacing. Slots may be None."""
    max_d = p["max_distance"] * unit
    spacing = p["min_spacing"] * unit

    def eligible(z: Zone) -> bool:
        return z.status not in ("EXHAUSTED", "INVALID") and z.grade != "WEAK"

    upper_pool, lower_pool, faded = [], [], []
    breakout_buffer = 0.35 * unit  # Immediate breakout tolerance buffer

    for z in zones:
        inside = z.bottom <= price <= z.top
        if inside:
            z.inside_now = True
            if z.status not in ("EXHAUSTED", "INVALID"):
                z.status = "TESTING"
            side = "upper" if z.role == "RESISTANCE" else "lower"
        else:
            z.inside_now = False
            if z.status == "TESTING":
                z.status = "ACTIVE" if z.retest_count > 0 else "FRESH"

            # Institutional Order Flow & S/R Continuity:
            # - While within breakout_buffer, price is actively interacting with / breaking the boundary.
            # - When price clears beyond the buffer, the level cleanly flips to a SWAP zone on the opposite side.
            # - Marked zones NEVER vanish into thin air; they remain active until exhausted or invalidated.
            if z.role == "RESISTANCE":
                if price <= z.top + breakout_buffer:
                    side = "upper"
                else:
                    side = "lower"
                    z.is_swap = True
            else:  # SUPPORT
                if price >= z.bottom - breakout_buffer:
                    side = "lower"
                else:
                    side = "upper"
                    z.is_swap = True

        dist = 0.0 if inside else (z.bottom - price if side == "upper" and z.bottom > price else
                                   price - z.top if side == "lower" and price > z.top else 0.0)
        if dist > max_d:
            continue

        if eligible(z):
            (upper_pool if side == "upper" else lower_pool).append(z)
        else:
            faded.append(z)

    def pick(pool: List[Zone]) -> List[Zone]:
        pool = sorted(pool, key=lambda z: _priority(z, unit_now=unit, price=price, use_ml=use_ml), reverse=True)
        chosen: List[Zone] = []
        for z in pool:
            if all(_gap(k, z) >= spacing for k in chosen):
                chosen.append(z)
        chosen.sort(key=lambda z: abs(z.mid - price))
        return chosen

    ups, lows = pick(upper_pool), pick(lower_pool)

    # Enrich slotted zones with 3-tier risk profile & invalidation guidance across all pairs
    for i, z in enumerate(ups):
        classify_zone_risk(z, price, unit, is_immediate=(i == 0), symbol=symbol)
    for i, z in enumerate(lows):
        classify_zone_risk(z, price, unit, is_immediate=(i == 0), symbol=symbol)

    return {
        "upper": ups[:2],
        "lower": lows[:2],
        "faded": sorted(faded, key=lambda z: abs(z.mid - price))[:6],
    }


def classify_zone_risk(
    z: Zone,
    current_price: float,
    unit: float,
    is_immediate: bool = True,
    is_gold: bool = False,
    is_jpy: bool = False,
    pip_size: float = 0.0001,
    symbol: str = ""
) -> Dict[str, Any]:
    """Classify zone into 3-tier risk profile (High / Med / Low) with tailored SL guidance.
    Accurately supports all asset classes: Gold (pts, 2 decimals), JPY (p, 3 decimals), Forex (p, 5 decimals).
    """
    if symbol:
        s_up = symbol.upper()
        is_gold = is_gold or ("XAU" in s_up)
        is_jpy = is_jpy or ("JPY" in s_up)

    if is_gold:
        pip_size = 0.1
        digits = 2
        unit_abbr = "pts"
    elif is_jpy:
        pip_size = 0.01
        digits = 3
        unit_abbr = "p"
    else:
        pip_size = 0.0001
        digits = 5
        unit_abbr = "p"

    diff = z.mid - current_price
    dist_atr = abs(diff) / (unit if unit > 0 else 1.0)
    is_upper = z.role == "RESISTANCE"

    if is_upper:
        # Short zone
        if is_immediate and dist_atr <= 1.8:
            tier = "HIGH_RISK"
            badge = "🔴 HIGH RISK · MOMENTUM / CONTINUATION"
            desc = "Shallow breaker / near-price shelf. Tight execution, potential chop."
            sl = round(z.top + 0.8 * unit, digits)
            sl_note = f"SL above shelf (+{round(0.8 * unit / pip_size, 1)}{unit_abbr}): {sl:.{digits}f}"
        elif dist_atr <= 3.8:
            tier = "MED_RISK"
            badge = "🟡 MED RISK · STRUCTURAL RETEST"
            desc = "Intermediate breakdown origin. Balanced R:R, requires confirmation trigger."
            sl = round(z.top + 0.6 * unit, digits)
            sl_note = f"SL above local swing (+{round(0.6 * unit / pip_size, 1)}{unit_abbr}): {sl:.{digits}f}"
        else:
            tier = "LOW_RISK"
            badge = "🟢 LOW RISK · PREMIUM STALK"
            desc = "Deep premium / origin extreme. Maximum R:R, defended by higher timeframe ceiling."
            sl = round(z.top + 0.4 * unit, digits)
            sl_note = f"SL above extreme (+{round(0.4 * unit / pip_size, 1)}{unit_abbr}): {sl:.{digits}f}"
    else:
        # Long zone
        if is_immediate and dist_atr <= 1.8:
            tier = "HIGH_RISK"
            badge = "🔴 HIGH RISK · MOMENTUM / CONTINUATION"
            desc = "Shallow bounce shelf. Tight execution, potential chop."
            sl = round(z.bottom - 0.8 * unit, digits)
            sl_note = f"SL below shelf (-{round(0.8 * unit / pip_size, 1)}{unit_abbr}): {sl:.{digits}f}"
        elif dist_atr <= 3.8:
            tier = "MED_RISK"
            badge = "🟡 MED RISK · STRUCTURAL RETEST"
            desc = "Intermediate breakout origin. Balanced R:R, requires confirmation trigger."
            sl = round(z.bottom - 0.6 * unit, digits)
            sl_note = f"SL below local swing (-{round(0.6 * unit / pip_size, 1)}{unit_abbr}): {sl:.{digits}f}"
        else:
            tier = "LOW_RISK"
            badge = "🟢 LOW RISK · DISCOUNT STALK"
            desc = "Deep discount / demand extreme. Maximum R:R, defended by higher timeframe floor."
            sl = round(z.bottom - 0.4 * unit, digits)
            sl_note = f"SL below extreme (-{round(0.4 * unit / pip_size, 1)}{unit_abbr}): {sl:.{digits}f}"

    z.risk_tier = tier
    z.risk_badge = badge
    z.risk_desc = desc
    return {
        "risk_tier": tier,
        "risk_badge": badge,
        "risk_desc": desc,
        "recommended_sl": sl,
        "recommended_sl_note": sl_note
    }


def zone_tradeability(z: Zone, htf_direction: str) -> Dict[str, Any]:
    """Which retest is in progress / next, and whether it is inside the trader's rules."""
    test_no = z.retest_count if z.inside_now and z.retest_count > 0 else z.retest_count + 1
    aligned = (z.role == "SUPPORT" and htf_direction == "UP") or (z.role == "RESISTANCE" and htf_direction == "DOWN")
    if z.status == "EXHAUSTED" or test_no >= 4:
        tier, label = "EXHAUSTED", f"{_ordinal(test_no)} retest · EXHAUSTED — do not trade"
    elif test_no == 3:
        tier = "HTF_ONLY" if aligned else "COUNTER_HTF"
        label = "3rd retest · HTF-aligned" if aligned else "3rd retest · counter-HTF ⚠"
    elif z.retest_count == 0 and not z.inside_now:
        tier, label = "FRESH", ("SWAP · awaiting 1st pullback" if z.is_swap else "FRESH · next = 1st retest")
    else:
        tier = "OK"
        label = f"{_ordinal(test_no)} retest {'in progress' if z.inside_now else 'next'}"
    return {"test_no": test_no, "tier": tier, "label": label, "htf_aligned": aligned}
