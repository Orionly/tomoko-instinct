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

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- #
# Tunable parameters per timeframe. "unit" = rolling mean True Range over
# `unit_period` bars (a full-day average on M15, so Asian-session ATR collapse
# does not shrink zones).
# --------------------------------------------------------------------------- #
TF_PARAMS: Dict[str, Dict[str, float]] = {
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
    for a in anchors:
        price = _f(a.get("price"))
        if price > 0 and (z.bottom - pad) <= price <= (z.top + pad):
            c_pts += 12.0
            conf.append(a.get("label", a.get("type", "Key level")))
    step = _round_step(symbol)
    rn = round(z.mid / step) * step
    if (z.bottom - pad) <= rn <= (z.top + pad):
        c_pts += 8.0
        conf.append(f"Round number {rn:g}")
    if any((z.bottom - pad) <= lv <= (z.top + pad) for lv in htf_levels):
        c_pts += 10.0
        conf.append("HTF swing confluence")
    c_pts = min(25.0, c_pts)

    if z.volume_ratio is not None:
        v_pts = max(0.0, min(1.0, (z.volume_ratio - 0.8) / 1.2)) * 15.0
        if z.volume_ratio >= 1.3:
            conf.append(f"Volume {z.volume_ratio:.1f}x avg")
        total = dep_pts + bos_pts + c_pts + v_pts
    else:
        total = (dep_pts + bos_pts + c_pts) * (100.0 / 85.0)

    z.score = int(round(max(0.0, min(100.0, total))))
    z.grade = "STRONG" if z.score >= GRADE_STRONG else ("MEDIUM" if z.score >= GRADE_MEDIUM else "WEAK")
    z.confluences = conf


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
) -> Dict[str, Any]:
    p = TF_PARAMS.get(timeframe.upper(), TF_PARAMS["M15"])
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
    live.sort(key=lambda z: _priority(z), reverse=True)
    kept: List[Zone] = []
    for z in live:
        if any(k.role == z.role and _gap(k, z) < 0 for k in kept):
            continue
        kept.append(z)

    return {"zones": kept, "unit": unit_now, "params": p}


def _priority(z: Zone) -> float:
    # Freshness matters to the trader: each prior retest costs priority.
    return z.score - 8.0 * z.retest_count + (5.0 if z.is_swap and z.retest_count == 0 else 0.0)


def _gap(a: Zone, b: Zone) -> float:
    """Distance between two boxes (negative if they overlap)."""
    return max(a.bottom, b.bottom) - min(a.top, b.top)


def select_slots(
    zones: List[Zone],
    price: float,
    unit: float,
    p: Dict[str, float],
) -> Dict[str, Any]:
    """Pick Upper/Lower 1 & 2 using strength, freshness and spacing. Slots may be None."""
    max_d = p["max_distance"] * unit
    spacing = p["min_spacing"] * unit

    def eligible(z: Zone) -> bool:
        return z.status not in ("EXHAUSTED", "INVALID") and z.grade != "WEAK"

    upper_pool, lower_pool, faded = [], [], []
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
            if z.bottom > price:
                side = "upper"
            else:
                side = "lower"
        dist = 0.0 if inside else (z.bottom - price if side == "upper" and z.bottom > price else
                                   price - z.top if z.top < price else 0.0)
        if dist > max_d:
            continue
        # A zone's role must match its side unless price is inside it
        if not inside and ((side == "upper" and z.role != "RESISTANCE") or (side == "lower" and z.role != "SUPPORT")):
            continue
        if eligible(z):
            (upper_pool if side == "upper" else lower_pool).append(z)
        else:
            faded.append(z)

    def pick(pool: List[Zone]) -> List[Zone]:
        pool = sorted(pool, key=_priority, reverse=True)
        chosen: List[Zone] = []
        for z in pool:
            if all(_gap(k, z) >= spacing for k in chosen):
                chosen.append(z)
        chosen.sort(key=lambda z: abs(z.mid - price))
        return chosen

    ups, lows = pick(upper_pool), pick(lower_pool)
    return {
        "upper": ups[:2],
        "lower": lows[:2],
        "faded": sorted(faded, key=lambda z: abs(z.mid - price))[:6],
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
