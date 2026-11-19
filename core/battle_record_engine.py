# BattleRecord Engine — Persistent Entry Plan Ledger, M15 Candle Monitor & Permanent Archive
#
# Tracks every Battle-Plan entry setup across all 7 pairs even after newer plans
# replace the active briefing. Evaluates live & historical M15 candles to determine
# fills, TP1+ wins, M15 candle-close stop losses, MFE/MAE, wick sweeps survived,
# and permanently archives triggered (and MISSED_TP) battles with full market
# context for weekend fine-tuning.

import os
import re
import json
import uuid
import time
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER_FILE = os.path.join(ROOT, "logs", "battle_record.json")
ARCHIVE_DIR = os.path.join(ROOT, "data", "battle_archive")

# Configurable Evaluation Rules (Phase 1 Spec)
SL_CLOSE_TIMEFRAME = "M15"       # Switch to "M5" when ready
SL_CANDLE_MINUTES = 15           # Matches SL_CLOSE_TIMEFRAME
PENDING_EXPIRY_HOURS = 24.0      # Untouched PENDING plans expire after 24h
ACTIVE_TIMEOUT_HOURS = 48.0      # Cap sideways ACTIVE trades after 48h
HOT_LEDGER_MAX_CLOSED = 300      # Keep UI ledger fast; permanent archive holds all history
EVAL_INTERVAL_SECONDS = 60       # Background M15 evaluation cadence

VERDICT_TAGS = [
    "Good Zone",
    "SL Too Tight",
    "News Spike",
    "Missed by Hair",
    "Counter-Trend Trap",
    "Wick Sweep Saved",
]


def _parse_utc(ts_str: Optional[str]) -> Optional[datetime]:
    if not ts_str:
        return None
    try:
        cleaned = str(ts_str).replace(" UTC", "").replace("Z", "").strip()
        if "T" in cleaned:
            dt = datetime.fromisoformat(cleaned)
        elif len(cleaned) == 16:
            dt = datetime.strptime(cleaned, "%Y-%m-%d %H:%M")
        else:
            dt = datetime.strptime(cleaned[:19], "%Y-%m-%d %H:%M:%S")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except Exception:
        return None


def _fmt_utc(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _safe_float(val: Any) -> Optional[float]:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        f = float(val)
        return None if (f != f) else f
    m = re.search(r"-?[0-9]+\.?[0-9]*", str(val).replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(0))
    except Exception:
        return None


def _parse_entry_zone(entry_zone_str: Any) -> Tuple[Optional[float], Optional[float]]:
    if not entry_zone_str:
        return None, None
    s = str(entry_zone_str).replace(",", "")
    m = re.search(r"([0-9]+\.?[0-9]*)\s*[-–to]+\s*([0-9]+\.?[0-9]*)", s, re.IGNORECASE)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        return (min(a, b), max(a, b))
    single = _safe_float(s)
    if single is not None:
        return single, single
    return None, None


class BattleRecordEngine:
    """
    Persistent ledger + M15 candle monitor + monthly permanent archive for all
    Tomoko Battle-Plan entry setups.
    """

    def __init__(self, price_engine=None, start_monitor: bool = True):
        self.price_engine = price_engine
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self.battles: List[Dict[str, Any]] = []
        self._load_ledger()

        if start_monitor:
            self._thread = threading.Thread(
                target=self._monitor_loop,
                daemon=True,
                name="battle-record-monitor",
            )
            self._thread.start()

    # ------------------------------------------------------------------
    # Persistence: Hot Ledger + Permanent Monthly Archive
    # ------------------------------------------------------------------
    def _load_ledger(self):
        try:
            if os.path.isfile(LEDGER_FILE):
                with open(LEDGER_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and isinstance(data.get("battles"), list):
                        self.battles = data["battles"]
                    elif isinstance(data, list):
                        self.battles = data
                log.info("[BattleRecord] Loaded %d battles from ledger.", len(self.battles))
        except Exception as e:
            log.warning("[BattleRecord] Failed to load ledger: %s", e)
            self.battles = []

    def _save_ledger_locked(self):
        try:
            os.makedirs(os.path.dirname(LEDGER_FILE), exist_ok=True)
            # Prune only very old finalized battles from hot ledger (permanent archive keeps all)
            open_b = [b for b in self.battles if not b.get("finalized")]
            closed_b = [b for b in self.battles if b.get("finalized")]
            if len(closed_b) > HOT_LEDGER_MAX_CLOSED:
                closed_b = closed_b[-HOT_LEDGER_MAX_CLOSED:]
                self.battles = closed_b + open_b
                self.battles.sort(key=lambda x: x.get("created_at_utc", ""))

            payload = {
                "updated_at_utc": _fmt_utc(datetime.now(timezone.utc)),
                "config": {
                    "sl_close_timeframe": SL_CLOSE_TIMEFRAME,
                    "entry_trigger": "WICK_TOUCH",
                    "tp1_is_win": True,
                    "pending_expiry_hours": PENDING_EXPIRY_HOURS,
                    "active_timeout_hours": ACTIVE_TIMEOUT_HOURS,
                },
                "battles": self.battles,
            }
            tmp_path = LEDGER_FILE + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_path, LEDGER_FILE)
        except Exception as e:
            log.warning("[BattleRecord] Failed to save ledger: %s", e)

    def _archive_battle_locked(self, battle: Dict[str, Any]):
        """
        Permanently archives every triggered battle (and MISSED_TP) into
        data/battle_archive/YYYY-MM.json keyed by battle_id so verdict tags
        added later update cleanly in place.
        """
        if not battle.get("triggered") and battle.get("status") != "MISSED_TP":
            return
        try:
            os.makedirs(ARCHIVE_DIR, exist_ok=True)
            created_dt = _parse_utc(battle.get("created_at_utc")) or datetime.now(timezone.utc)
            month_key = created_dt.strftime("%Y-%m")
            archive_path = os.path.join(ARCHIVE_DIR, f"{month_key}.json")

            archive_data: Dict[str, Any] = {
                "month": month_key,
                "schema_version": 1,
                "sl_close_timeframe": SL_CLOSE_TIMEFRAME,
                "battles": {},
            }
            if os.path.isfile(archive_path):
                try:
                    with open(archive_path, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                        if isinstance(loaded, dict) and isinstance(loaded.get("battles"), dict):
                            archive_data = loaded
                except Exception:
                    pass

            bid = battle["battle_id"]
            archive_data["updated_at_utc"] = _fmt_utc(datetime.now(timezone.utc))
            archive_data["battles"][bid] = dict(battle)

            tmp_path = archive_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(archive_data, f, indent=2)
            os.replace(tmp_path, archive_path)
            battle["archived"] = True
            battle["archive_file"] = f"data/battle_archive/{month_key}.json"
        except Exception as e:
            log.warning("[BattleRecord] Archive write error for %s: %s", battle.get("battle_id"), e)

    # ------------------------------------------------------------------
    # Plan Ingestion (Called whenever SessionBriefingEngine stores a plan)
    # ------------------------------------------------------------------
    def record_briefing(self, briefing: Dict[str, Any], telemetry: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Records all directional primary_setups from a new SessionBriefing.
        Deduplicates against open battles with identical symbol, bias, and near-identical levels.
        """
        if not briefing or not isinstance(briefing, dict):
            return []

        pairs_map = briefing.get("pairs", {})
        if not isinstance(pairs_map, dict):
            return []

        session_title = briefing.get("session", "Unknown Session")
        engine_mode = briefing.get("engine_mode", "GEMINI_AI")
        macro_thesis = briefing.get("global_macro_executive_thesis", "")
        now_utc = datetime.now(timezone.utc)
        created_str = _fmt_utc(now_utc)

        macro_tel = (telemetry or {}).get("macro_telemetry", {})
        pairs_tel = (telemetry or {}).get("pairs", {})

        added: List[Dict[str, Any]] = []
        with self._lock:
            for sym, p_plan in pairs_map.items():
                if not isinstance(p_plan, dict):
                    continue
                primary = p_plan.get("primary_setup")
                if not isinstance(primary, dict):
                    continue

                raw_bias = str(primary.get("bias", "")).upper()
                if "LONG" in raw_bias or "BUY" in raw_bias:
                    bias = "LONG"
                elif "SHORT" in raw_bias or "SELL" in raw_bias:
                    bias = "SHORT"
                else:
                    continue  # Skip WAIT / NEUTRAL setups without directional execution

                e_low, e_high = _parse_entry_zone(primary.get("entry_zone"))
                sl = _safe_float(primary.get("stop_loss"))
                raw_targets = primary.get("targets") or []
                targets = [t for t in (_safe_float(x) for x in raw_targets) if t is not None]

                if e_low is None or e_high is None or sl is None or not targets:
                    continue

                # Sanity geometry check
                if bias == "LONG" and (sl >= e_low or targets[0] <= e_high):
                    continue
                if bias == "SHORT" and (sl <= e_high or targets[0] >= e_low):
                    continue

                digits = 3 if "JPY" in sym else (2 if sym == "XAUUSD" else 5)
                p_tel = pairs_tel.get(sym, {})
                spot = _safe_float(p_tel.get("spot_price"))
                if spot is None and self.price_engine:
                    try:
                        p_info = self.price_engine.get_price(sym) or {}
                        spot = _safe_float(p_info.get("price"))
                    except Exception:
                        spot = None

                h1_atr = _safe_float(p_tel.get("h1_atr"))
                if not h1_atr or h1_atr <= 0:
                    h1_atr = max(abs(e_high - e_low), (spot or e_low) * 0.0025)

                # Deduplicate against already open (non-finalized) battles for same pair & bias
                is_dup = False
                for ex in self.battles:
                    if ex.get("finalized"):
                        continue
                    if ex.get("symbol") == sym and ex.get("bias") == bias:
                        if (
                            abs(ex["entry_low"] - e_low) <= 0.15 * h1_atr
                            and abs(ex["entry_high"] - e_high) <= 0.15 * h1_atr
                            and abs(ex["stop_loss"] - sl) <= 0.20 * h1_atr
                        ):
                            is_dup = True
                            break
                if is_dup:
                    continue

                # Front-edge entry reference (reaction from zone)
                if spot is not None and e_low <= spot <= e_high:
                    entry_ref = round(spot, digits)
                else:
                    entry_ref = round(e_high if bias == "LONG" else e_low, digits)

                entry_mid = round((e_low + e_high) / 2.0, digits)
                zone_width = round(abs(e_high - e_low), digits)
                sl_dist = round(abs(entry_ref - sl), digits)
                tp1 = round(targets[0], digits)
                tp2 = round(targets[1], digits) if len(targets) > 1 else None
                tp3 = round(targets[2], digits) if len(targets) > 2 else None
                tp1_dist = round(abs(tp1 - entry_ref), digits)
                rr_tp1 = round(tp1_dist / sl_dist, 2) if sl_dist > 0 else 0.0
                rr_tp2 = round(abs(tp2 - entry_ref) / sl_dist, 2) if (tp2 is not None and sl_dist > 0) else None

                entry_dist_spot_atr = None
                if spot is not None and h1_atr > 0:
                    if e_low <= spot <= e_high:
                        entry_dist_spot_atr = 0.0
                    else:
                        nearest_edge = e_high if spot > e_high else e_low
                        entry_dist_spot_atr = round(abs(spot - nearest_edge) / h1_atr, 3)

                # Mandatory entry reason
                macro_cat = p_plan.get("macro_catalyst") or {}
                traps_blk = p_plan.get("traps_and_checklist") or {}
                entry_reason = (
                    str(primary.get("entry_reason") or "").strip()
                    or str(p_plan.get("executive_bottom_line") or "").strip()
                    or f"{bias} setup anchored at {e_low}-{e_high} ({session_title})."
                )

                h1_ema21 = _safe_float(p_tel.get("h1_ema21"))
                dist_ema21_atr = (
                    round((spot - h1_ema21) / h1_atr, 3)
                    if (spot is not None and h1_ema21 is not None and h1_atr > 0)
                    else None
                )
                trap_info = p_tel.get("trap_radar") or p_plan.get("trap_radar") or {}

                battle_id = f"BR-{now_utc.strftime('%Y%m%d-%H%M')}-{sym}-{uuid.uuid4().hex[:5].upper()}"
                battle: Dict[str, Any] = {
                    "battle_id": battle_id,
                    "symbol": sym,
                    "bias": bias,
                    "session": session_title,
                    "engine_mode": engine_mode,
                    "created_at_utc": created_str,
                    "sl_close_timeframe": SL_CLOSE_TIMEFRAME,
                    "digits": digits,

                    # Mandatory Rationale ("WHY this entry")
                    "entry_reason": entry_reason,
                    "executive_bottom_line": p_plan.get("executive_bottom_line", ""),
                    "trigger_confirmation": primary.get("trigger_confirmation", ""),
                    "probability": primary.get("probability", ""),
                    "macro_driver": macro_cat.get("primary_driver", ""),
                    "trap_warning": traps_blk.get("trap_warning", ""),
                    "global_macro_thesis": macro_thesis,

                    # Geometry & Normalized Ratios (Weekend-Tunable)
                    "entry_zone": f"{round(e_low, digits)} - {round(e_high, digits)}",
                    "entry_low": round(e_low, digits),
                    "entry_high": round(e_high, digits),
                    "entry_mid": entry_mid,
                    "entry_ref": entry_ref,
                    "zone_width": zone_width,
                    "zone_width_atr": round(zone_width / h1_atr, 3) if h1_atr > 0 else None,
                    "entry_dist_from_spot_atr": entry_dist_spot_atr,
                    "stop_loss": round(sl, digits),
                    "sl_distance": sl_dist,
                    "sl_distance_atr": round(sl_dist / h1_atr, 3) if h1_atr > 0 else None,
                    "targets": [round(t, digits) for t in targets[:4]],
                    "tp1": tp1,
                    "tp2": tp2,
                    "tp3": tp3,
                    "tp1_distance_atr": round(tp1_dist / h1_atr, 3) if h1_atr > 0 else None,
                    "rr_to_tp1": rr_tp1,
                    "rr_to_tp2": rr_tp2,

                    # Frozen Market Context at Creation (For Weekend Fine-Tuning)
                    "context_snapshot": {
                        "spot_at_creation": round(spot, digits) if spot is not None else None,
                        "h1_atr": round(h1_atr, digits + 1),
                        "h1_rsi": _safe_float(p_tel.get("h1_rsi")),
                        "h1_ema21": round(h1_ema21, digits) if h1_ema21 is not None else None,
                        "dist_ema21_atr": dist_ema21_atr,
                        "pdh": _safe_float(p_tel.get("pdh")),
                        "pdl": _safe_float(p_tel.get("pdl")),
                        "today_high": _safe_float(p_tel.get("today_high")),
                        "today_low": _safe_float(p_tel.get("today_low")),
                        "pivot": _safe_float(p_tel.get("pivot")),
                        "weekly_open": _safe_float(p_tel.get("weekly_open")),
                        "macro_snapshot": {
                            "dxy": macro_tel.get("dxy", {}),
                            "us10y": macro_tel.get("us10y", {}),
                            "vix": macro_tel.get("vix", {}),
                            "real_yield_10y_tips": macro_tel.get("real_yield_10y_tips"),
                            "real_yield_pressure": macro_tel.get("real_yield_pressure"),
                        },
                        "trap_state": trap_info.get("trap_state", "NONE"),
                        "trap_title": trap_info.get("trap_title", ""),
                        "upcoming_catalysts": p_tel.get("upcoming_events", [])[:3],
                        "risk_playbook": primary.get("risk_playbook", {}),
                    },

                    # Live & Final Execution Telemetry
                    "status": "PENDING",
                    "finalized": False,
                    "triggered": False,
                    "is_win": None,
                    "highest_tp_hit": "NONE",
                    "triggered_at_utc": None,
                    "tp1_hit_at_utc": None,
                    "closed_at_utc": None,
                    "time_to_fill_min": None,
                    "time_to_outcome_min": None,
                    "exit_price": None,
                    "exit_reason": None,
                    "realized_r": None,
                    "max_r_reached": 0.0,
                    "mfe_price": None,
                    "mfe_atr": 0.0,
                    "mae_price": None,
                    "mae_atr": 0.0,
                    "wick_breached_sl": False,
                    "max_wick_beyond_sl_atr": 0.0,
                    "missed_by_atr": None,
                    "last_spot": round(spot, digits) if spot is not None else None,
                    "last_evaluated_utc": created_str,

                    # Human Trader Verdict (1-Click Tags + Note)
                    "verdict_tags": [],
                    "trader_note": "",
                    "archived": False,
                    "archive_file": None,
                }

                self.battles.append(battle)
                added.append(battle)

            if added:
                self._save_ledger_locked()
                log.info("[BattleRecord] Recorded %d new entry plan(s) for %s.", len(added), session_title)

        return added

    # ------------------------------------------------------------------
    # M15 Candle Replay Evaluator
    # ------------------------------------------------------------------
    def _extract_candles(self, symbol: str) -> List[Tuple[datetime, float, float, float, float]]:
        """Fetches M15 OHLC candles from PriceEngine as chronological (open_utc, o, h, l, c) tuples."""
        if not self.price_engine:
            return []
        try:
            df = self.price_engine.get_rates(symbol, SL_CLOSE_TIMEFRAME, count=300)
            if df is None or df.empty:
                return []
            candles: List[Tuple[datetime, float, float, float, float]] = []
            times = df["time"] if "time" in df.columns else df.index
            for ts, o, h, l, c in zip(times, df["open"], df["high"], df["low"], df["close"]):
                if hasattr(ts, "to_pydatetime"):
                    dt = ts.to_pydatetime()
                elif isinstance(ts, datetime):
                    dt = ts
                else:
                    dt = _parse_utc(str(ts))
                if dt is None:
                    continue
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
                candles.append((dt, float(o), float(h), float(l), float(c)))
            candles.sort(key=lambda x: x[0])
            return candles
        except Exception as e:
            log.debug("[BattleRecord] Candle fetch error for %s: %s", symbol, e)
            return []

    def _evaluate_battle_with_candles(
        self,
        battle: Dict[str, Any],
        candles: List[Tuple[datetime, float, float, float, float]],
        now_utc: datetime,
    ) -> bool:
        """
        Deterministically replays M15 candles from created_at_utc forward.
        Rules:
          - Entry activation: wick touch into [entry_low, entry_high]
          - TP1 touch: locks is_win = True immediately; continues tracking TP2/TP3 runners
          - SL invalidation: completed M15 candle close beyond stop_loss
          - Pending expiry: 24h; Active timeout: 48h
        Returns True if any field changed.
        """
        if battle.get("finalized"):
            return False

        created_dt = _parse_utc(battle.get("created_at_utc"))
        if not created_dt:
            return False

        bias = battle["bias"]
        e_low = float(battle["entry_low"])
        e_high = float(battle["entry_high"])
        entry_ref = float(battle["entry_ref"])
        sl = float(battle["stop_loss"])
        tp1 = float(battle["tp1"])
        tp2 = float(battle["tp2"]) if battle.get("tp2") is not None else None
        tp3 = float(battle["tp3"]) if battle.get("tp3") is not None else None
        digits = int(battle.get("digits", 5))
        sl_dist = max(float(battle.get("sl_distance") or abs(entry_ref - sl)), 1e-9)
        h1_atr = max(float((battle.get("context_snapshot") or {}).get("h1_atr") or sl_dist), 1e-9)
        candle_delta = timedelta(minutes=SL_CANDLE_MINUTES)

        # Replay state fresh from creation for 100% restart idempotency
        status = "PENDING"
        finalized = False
        triggered = False
        is_win: Optional[bool] = None
        highest_tp = "NONE"
        triggered_dt: Optional[datetime] = None
        tp1_dt: Optional[datetime] = None
        closed_dt: Optional[datetime] = None
        exit_price: Optional[float] = None
        exit_reason: Optional[str] = None
        mfe_price: Optional[float] = None
        mae_price: Optional[float] = None
        mfe_atr = 0.0
        mae_atr = 0.0
        max_r = 0.0
        wick_breached = False
        max_wick_beyond_sl_atr = 0.0
        min_dist_to_zone: Optional[float] = None
        last_close: Optional[float] = None

        # Relevant candles: any candle whose close time > created_dt (includes creation bar)
        rel_candles = [bar for bar in candles if (bar[0] + candle_delta) > created_dt]

        for c_open, o, h, l, c in rel_candles:
            c_close = c_open + candle_delta
            is_completed = c_close <= now_utc
            last_close = c

            # 1. Pending state checks
            if not triggered:
                # Check expiry before this candle
                if (c_open - created_dt).total_seconds() >= PENDING_EXPIRY_HOURS * 3600:
                    status = "EXPIRED"
                    exit_reason = "EXPIRED_24H"
                    finalized = True
                    closed_dt = created_dt + timedelta(hours=PENDING_EXPIRY_HOURS)
                    break

                # Wick touch entry zone?
                if l <= e_high and h >= e_low:
                    triggered = True
                    triggered_dt = max(c_open, created_dt)
                    status = "ACTIVE"
                    min_dist_to_zone = 0.0
                else:
                    # Track how close price came to filling
                    dist = (l - e_high) if l > e_high else (e_low - h)
                    if min_dist_to_zone is None or dist < min_dist_to_zone:
                        min_dist_to_zone = max(0.0, dist)

                    # Pre-entry M15 close beyond SL -> thesis invalidated before fill
                    if is_completed and ((bias == "LONG" and c < sl) or (bias == "SHORT" and c > sl)):
                        status = "INVALIDATED"
                        exit_reason = "PRE_ENTRY_SL_CLOSE"
                        exit_price = round(c, digits)
                        finalized = True
                        closed_dt = c_close
                        break

                    # Ran straight to TP1 without ever filling -> MISSED_TP
                    if (bias == "LONG" and h >= tp1) or (bias == "SHORT" and l <= tp1):
                        status = "MISSED_TP"
                        exit_reason = "RAN_TO_TP_WITHOUT_FILL"
                        exit_price = round(tp1, digits)
                        finalized = True
                        closed_dt = c_open
                        break
                    continue

            # 2. Active / Runner state checks (also runs on activation candle)
            if triggered:
                # Active timeout check
                if triggered_dt and (c_open - triggered_dt).total_seconds() >= ACTIVE_TIMEOUT_HOURS * 3600:
                    finalized = True
                    closed_dt = triggered_dt + timedelta(hours=ACTIVE_TIMEOUT_HOURS)
                    if is_win:
                        exit_reason = f"{highest_tp}_RUNNER_TIMEOUT_48H"
                    else:
                        status = "ACTIVE_EXPIRED"
                        exit_reason = "ACTIVE_TIMEOUT_48H"
                        exit_price = round(c, digits)
                    break

                # MFE / MAE / Max R
                if bias == "LONG":
                    fav_price = h
                    adv_price = l
                    fav_move = max(0.0, h - entry_ref)
                    adv_move = max(0.0, entry_ref - l)
                    if l <= sl:
                        wick_breached = True
                        beyond = (sl - l) / h1_atr
                        if beyond > max_wick_beyond_sl_atr:
                            max_wick_beyond_sl_atr = beyond
                else:
                    fav_price = l
                    adv_price = h
                    fav_move = max(0.0, entry_ref - l)
                    adv_move = max(0.0, h - entry_ref)
                    if h >= sl:
                        wick_breached = True
                        beyond = (h - sl) / h1_atr
                        if beyond > max_wick_beyond_sl_atr:
                            max_wick_beyond_sl_atr = beyond

                if fav_move / h1_atr > mfe_atr or mfe_price is None:
                    mfe_atr = max(mfe_atr, fav_move / h1_atr)
                    mfe_price = round(fav_price, digits)
                if adv_move / h1_atr > mae_atr or mae_price is None:
                    mae_atr = max(mae_atr, adv_move / h1_atr)
                    mae_price = round(adv_price, digits)
                max_r = max(max_r, fav_move / sl_dist)

                # TP ladder hits (TP1 locks WIN)
                hit_tp1 = (bias == "LONG" and h >= tp1) or (bias == "SHORT" and l <= tp1)
                hit_tp2 = tp2 is not None and ((bias == "LONG" and h >= tp2) or (bias == "SHORT" and l <= tp2))
                hit_tp3 = tp3 is not None and ((bias == "LONG" and h >= tp3) or (bias == "SHORT" and l <= tp3))

                if hit_tp1:
                    if not is_win:
                        is_win = True
                        tp1_dt = c_open
                    if highest_tp == "NONE":
                        highest_tp = "TP1"
                        status = "WIN_TP1"
                        exit_price = round(tp1, digits)
                        exit_reason = "TP1_HIT"
                if hit_tp2 and highest_tp in ("NONE", "TP1"):
                    highest_tp = "TP2"
                    status = "WIN_TP2"
                    exit_price = round(tp2, digits)
                    exit_reason = "TP2_HIT"
                if hit_tp3:
                    highest_tp = "TP3"
                    status = "WIN_TP3"
                    exit_price = round(tp3, digits)
                    exit_reason = "TP3_HIT"
                    finalized = True
                    closed_dt = c_open
                    break

                # Final target reached if only TP1 or TP2 existed
                if (tp2 is None and hit_tp1) or (tp3 is None and tp2 is not None and hit_tp2):
                    finalized = True
                    closed_dt = c_open
                    break

                # Completed M15 candle close beyond SL
                sl_closed = is_completed and ((bias == "LONG" and c < sl) or (bias == "SHORT" and c > sl))
                if sl_closed:
                    finalized = True
                    closed_dt = c_close
                    if is_win:
                        # TP1/TP2 was already locked as WIN; runner ends here
                        exit_reason = f"{highest_tp}_LOCKED_THEN_SL_CLOSE"
                    else:
                        status = "STOPPED_OUT"
                        is_win = False
                        exit_price = round(c, digits)
                        exit_reason = "M15_SL_CLOSE"
                    break

        # Wall-clock expiry if no newer candles arrived yet
        if not finalized:
            if not triggered and (now_utc - created_dt).total_seconds() >= PENDING_EXPIRY_HOURS * 3600:
                status = "EXPIRED"
                exit_reason = "EXPIRED_24H"
                finalized = True
                closed_dt = created_dt + timedelta(hours=PENDING_EXPIRY_HOURS)
            elif triggered and triggered_dt and (now_utc - triggered_dt).total_seconds() >= ACTIVE_TIMEOUT_HOURS * 3600:
                finalized = True
                closed_dt = triggered_dt + timedelta(hours=ACTIVE_TIMEOUT_HOURS)
                if is_win:
                    exit_reason = f"{highest_tp}_RUNNER_TIMEOUT_48H"
                else:
                    status = "ACTIVE_EXPIRED"
                    exit_reason = "ACTIVE_TIMEOUT_48H"
                    if last_close is not None:
                        exit_price = round(last_close, digits)

        # Compute realized R
        realized_r: Optional[float] = None
        if triggered and exit_price is not None:
            raw_pnl = (exit_price - entry_ref) if bias == "LONG" else (entry_ref - exit_price)
            realized_r = round(raw_pnl / sl_dist, 2)
        elif triggered and last_close is not None:
            raw_pnl = (last_close - entry_ref) if bias == "LONG" else (entry_ref - last_close)
            realized_r = round(raw_pnl / sl_dist, 2)

        time_to_fill_min = (
            round((triggered_dt - created_dt).total_seconds() / 60.0, 1)
            if triggered_dt
            else None
        )
        end_ref = closed_dt or tp1_dt
        time_to_outcome_min = (
            round((end_ref - triggered_dt).total_seconds() / 60.0, 1)
            if (triggered_dt and end_ref)
            else None
        )

        # Update battle dict
        battle["status"] = status
        battle["finalized"] = finalized
        battle["triggered"] = triggered
        battle["is_win"] = is_win
        battle["highest_tp_hit"] = highest_tp
        battle["triggered_at_utc"] = _fmt_utc(triggered_dt)
        battle["tp1_hit_at_utc"] = _fmt_utc(tp1_dt)
        battle["closed_at_utc"] = _fmt_utc(closed_dt)
        battle["time_to_fill_min"] = time_to_fill_min
        battle["time_to_outcome_min"] = time_to_outcome_min
        battle["exit_price"] = exit_price
        battle["exit_reason"] = exit_reason
        battle["realized_r"] = realized_r
        battle["max_r_reached"] = round(max_r, 2)
        battle["mfe_price"] = mfe_price
        battle["mfe_atr"] = round(mfe_atr, 3)
        battle["mae_price"] = mae_price
        battle["mae_atr"] = round(mae_atr, 3)
        battle["wick_breached_sl"] = wick_breached
        battle["max_wick_beyond_sl_atr"] = round(max_wick_beyond_sl_atr, 3)
        battle["missed_by_atr"] = (
            round(min_dist_to_zone / h1_atr, 3)
            if (min_dist_to_zone is not None and not triggered)
            else (0.0 if triggered else None)
        )
        if last_close is not None:
            battle["last_spot"] = round(last_close, digits)
        battle["last_evaluated_utc"] = _fmt_utc(now_utc)

        if finalized and (triggered or status == "MISSED_TP"):
            self._archive_battle_locked(battle)

        return True

    def evaluate_all(self) -> int:
        """Evaluates all non-finalized battles against live M15 candles."""
        now_utc = datetime.now(timezone.utc)
        with self._lock:
            open_symbols = sorted({b["symbol"] for b in self.battles if not b.get("finalized")})

        if not open_symbols:
            return 0

        candle_map = {sym: self._extract_candles(sym) for sym in open_symbols}
        updated_count = 0
        with self._lock:
            for b in self.battles:
                if b.get("finalized"):
                    continue
                candles = candle_map.get(b["symbol"], [])
                if self._evaluate_battle_with_candles(b, candles, now_utc):
                    updated_count += 1
            if updated_count:
                self._save_ledger_locked()
        return updated_count

    def _monitor_loop(self):
        time.sleep(5)
        while not self._stop_event.is_set():
            try:
                self.evaluate_all()
            except Exception as e:
                log.debug("[BattleRecord] Monitor loop note: %s", e)
            self._stop_event.wait(EVAL_INTERVAL_SECONDS)

    # ------------------------------------------------------------------
    # 1-Click Trader Verdict Tagging (Syncs to Permanent Archive Too)
    # ------------------------------------------------------------------
    def update_verdict(
        self,
        battle_id: str,
        verdict_tags: Optional[List[str]] = None,
        trader_note: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            target = None
            for b in self.battles:
                if b.get("battle_id") == battle_id:
                    target = b
                    break
            if target is None:
                return None

            if verdict_tags is not None:
                clean_tags = [str(t).strip() for t in verdict_tags if str(t).strip()]
                target["verdict_tags"] = clean_tags
            if trader_note is not None:
                target["trader_note"] = str(trader_note).strip()[:300]

            self._save_ledger_locked()
            if target.get("archived") or (target.get("finalized") and (target.get("triggered") or target.get("status") == "MISSED_TP")):
                self._archive_battle_locked(target)
            return dict(target)

    # ------------------------------------------------------------------
    # Query & Performance Analytics
    # ------------------------------------------------------------------
    def get_records(
        self,
        symbol: Optional[str] = None,
        status: Optional[str] = None,
        session: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        with self._lock:
            all_battles = list(self.battles)

        # Newest first
        all_battles.sort(key=lambda x: x.get("created_at_utc", ""), reverse=True)

        filtered = all_battles
        if symbol and symbol.upper() != "ALL":
            filtered = [b for b in filtered if b.get("symbol", "").upper() == symbol.upper()]
        if session and session.upper() != "ALL":
            s_q = session.upper()
            filtered = [b for b in filtered if s_q in str(b.get("session", "")).upper()]
        if status and status.upper() != "ALL":
            st = status.upper()
            if st == "OPEN":
                filtered = [b for b in filtered if not b.get("finalized")]
            elif st == "WINS":
                filtered = [b for b in filtered if b.get("is_win") is True]
            elif st == "LOSSES":
                filtered = [b for b in filtered if b.get("status") == "STOPPED_OUT"]
            elif st == "MISSED":
                filtered = [b for b in filtered if b.get("status") in ("MISSED_TP", "EXPIRED", "INVALIDATED")]
            else:
                filtered = [b for b in filtered if b.get("status") == st]

        stats = self._compute_stats(filtered)
        return {
            "count": len(filtered),
            "total_in_ledger": len(all_battles),
            "verdict_tag_options": VERDICT_TAGS,
            "rules": {
                "sl_close_timeframe": SL_CLOSE_TIMEFRAME,
                "entry_trigger": "WICK_TOUCH",
                "tp1_is_win": True,
                "pending_expiry_hours": PENDING_EXPIRY_HOURS,
                "active_timeout_hours": ACTIVE_TIMEOUT_HOURS,
            },
            "stats": stats,
            "battles": filtered[:limit],
        }

    @staticmethod
    def _compute_stats(battles: List[Dict[str, Any]]) -> Dict[str, Any]:
        total = len(battles)
        pending = sum(1 for b in battles if b.get("status") == "PENDING")
        active = sum(1 for b in battles if not b.get("finalized") and b.get("triggered"))
        wins = [b for b in battles if b.get("is_win") is True]
        losses = [b for b in battles if b.get("status") == "STOPPED_OUT"]
        decided = len(wins) + len(losses)
        triggered_all = [b for b in battles if b.get("triggered")]
        missed_tp = sum(1 for b in battles if b.get("status") == "MISSED_TP")
        expired = sum(1 for b in battles if b.get("status") == "EXPIRED")
        invalidated = sum(1 for b in battles if b.get("status") == "INVALIDATED")
        wick_sweeps_saved = sum(1 for b in wins if b.get("wick_breached_sl"))
        tp2_plus = sum(1 for b in wins if b.get("highest_tp_hit") in ("TP2", "TP3"))
        archived_count = sum(1 for b in battles if b.get("archived"))

        win_rate = round((len(wins) / decided) * 100.0, 1) if decided > 0 else None
        fill_denom = len(triggered_all) + missed_tp + expired + invalidated
        fill_rate = round((len(triggered_all) / fill_denom) * 100.0, 1) if fill_denom > 0 else None

        r_vals = [b["realized_r"] for b in (wins + losses) if b.get("realized_r") is not None]
        avg_r = round(sum(r_vals) / len(r_vals), 2) if r_vals else None
        net_r = round(sum(r_vals), 2) if r_vals else 0.0

        # Per-pair summary
        by_pair: Dict[str, Dict[str, Any]] = {}
        for b in battles:
            sym = b.get("symbol", "UNKNOWN")
            if sym not in by_pair:
                by_pair[sym] = {"wins": 0, "losses": 0, "active": 0, "pending": 0, "missed_tp": 0, "net_r": 0.0}
            row = by_pair[sym]
            if b.get("is_win") is True:
                row["wins"] += 1
                row["net_r"] = round(row["net_r"] + float(b.get("realized_r") or 0.0), 2)
            elif b.get("status") == "STOPPED_OUT":
                row["losses"] += 1
                row["net_r"] = round(row["net_r"] + float(b.get("realized_r") or 0.0), 2)
            elif b.get("status") == "PENDING":
                row["pending"] += 1
            elif not b.get("finalized") and b.get("triggered"):
                row["active"] += 1
            elif b.get("status") == "MISSED_TP":
                row["missed_tp"] += 1

        for sym, row in by_pair.items():
            d = row["wins"] + row["losses"]
            row["win_rate"] = round((row["wins"] / d) * 100.0, 1) if d > 0 else None

        return {
            "total": total,
            "pending": pending,
            "active": active,
            "wins": len(wins),
            "losses": len(losses),
            "decided": decided,
            "win_rate": win_rate,
            "fill_rate": fill_rate,
            "avg_r": avg_r,
            "net_r": net_r,
            "missed_tp": missed_tp,
            "expired": expired,
            "invalidated": invalidated,
            "wick_sweeps_saved": wick_sweeps_saved,
            "tp2_plus_runners": tp2_plus,
            "archived_count": archived_count,
            "by_pair": by_pair,
        }

    def get_battle_by_id(self, battle_id: str) -> Optional[Dict[str, Any]]:
        """Lookup a battle by ID in the live ledger first, then across monthly permanent archives."""
        with self._lock:
            for b in self.battles:
                if b.get("battle_id") == battle_id:
                    return dict(b)
        if os.path.isdir(ARCHIVE_DIR):
            for fname in sorted(os.listdir(ARCHIVE_DIR), reverse=True):
                if not fname.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(ARCHIVE_DIR, fname), "r", encoding="utf-8") as f:
                        data = json.load(f)
                        bmap = data.get("battles", {}) if isinstance(data, dict) else {}
                        if battle_id in bmap:
                            return dict(bmap[battle_id])
                except Exception:
                    continue
        return None


def load_all_battles(symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Loads and deduplicates all battles across permanent monthly archives
    (data/battle_archive/*.json) and the live ledger (logs/battle_record.json).
    """
    merged: Dict[str, Dict[str, Any]] = {}

    if os.path.isdir(ARCHIVE_DIR):
        for fname in sorted(os.listdir(ARCHIVE_DIR)):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(ARCHIVE_DIR, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                bmap = data.get("battles", {}) if isinstance(data, dict) else {}
                for bid, rec in bmap.items():
                    if isinstance(rec, dict):
                        merged[bid] = rec
            except Exception:
                continue

    if os.path.isfile(LEDGER_FILE):
        try:
            with open(LEDGER_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            rows = data.get("battles", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            for rec in rows:
                if isinstance(rec, dict) and rec.get("battle_id"):
                    # Hot ledger has freshest evaluation for open/recent battles
                    merged[rec["battle_id"]] = rec
        except Exception:
            pass

    battles = list(merged.values())
    if symbol and symbol.upper() != "ALL":
        s_up = symbol.upper()
        battles = [b for b in battles if str(b.get("symbol", "")).upper() == s_up]
    battles.sort(key=lambda x: x.get("created_at_utc", ""))
    return battles


def compute_weekend_diagnostics(symbol: Optional[str] = None) -> Dict[str, Any]:
    """
    Weekend BattleRecord Tuning Diagnostics:
      - Overall Win Rate, Fill Rate, Net R, Avg R, Wick Sweeps Saved
      - Breakdown by Pair, by Session (Tokyo / London / NY), and by Engine Mode (AI vs Rule)
      - MFE / MAE & SL-Buffer Check
      - Missed-by-ATR Zone-Depth Calibration
      - 1-Click Trader Verdict Tag Summary
      - Data-backed Calibration Hints (only emitted when supported by observed numbers)
    """
    battles = load_all_battles(symbol=symbol)
    overall = BattleRecordEngine._compute_stats(battles)

    def _bucket_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        wins = [b for b in rows if b.get("is_win") is True]
        losses = [b for b in rows if b.get("status") == "STOPPED_OUT"]
        decided = len(wins) + len(losses)
        trig = [b for b in rows if b.get("triggered")]
        missed_tp = sum(1 for b in rows if b.get("status") == "MISSED_TP")
        expired = sum(1 for b in rows if b.get("status") == "EXPIRED")
        inv = sum(1 for b in rows if b.get("status") == "INVALIDATED")
        r_vals = [b["realized_r"] for b in (wins + losses) if b.get("realized_r") is not None]
        fill_den = len(trig) + missed_tp + expired + inv
        return {
            "total": len(rows),
            "wins": len(wins),
            "losses": len(losses),
            "decided": decided,
            "win_rate": round((len(wins) / decided) * 100.0, 1) if decided > 0 else None,
            "fill_rate": round((len(trig) / fill_den) * 100.0, 1) if fill_den > 0 else None,
            "avg_r": round(sum(r_vals) / len(r_vals), 2) if r_vals else None,
            "net_r": round(sum(r_vals), 2) if r_vals else 0.0,
            "missed_tp": missed_tp,
        }

    # Breakdowns
    by_pair_groups: Dict[str, List[Dict[str, Any]]] = {}
    by_session_groups: Dict[str, List[Dict[str, Any]]] = {}
    by_engine_groups: Dict[str, List[Dict[str, Any]]] = {}

    for b in battles:
        sym = str(b.get("symbol", "UNKNOWN")).upper()
        by_pair_groups.setdefault(sym, []).append(b)

        sess_raw = str(b.get("session", "Unknown")).upper()
        if "TOKYO" in sess_raw or "ASIAN" in sess_raw:
            sess_key = "TOKYO_ASIAN"
        elif "LONDON" in sess_raw or "EURO" in sess_raw:
            sess_key = "LONDON_EUROPEAN"
        elif "NEW YORK" in sess_raw or "US" in sess_raw:
            sess_key = "NEW_YORK_US"
        else:
            sess_key = "OTHER"
        by_session_groups.setdefault(sess_key, []).append(b)

        eng = "RULE_BASED" if str(b.get("engine_mode", "")).upper() == "SYNTHESIZED_RULE_BASED" else "GEMINI_AI"
        by_engine_groups.setdefault(eng, []).append(b)

    by_pair = {k: _bucket_stats(v) for k, v in sorted(by_pair_groups.items())}
    by_session = {k: _bucket_stats(v) for k, v in sorted(by_session_groups.items())}
    by_engine = {k: _bucket_stats(v) for k, v in sorted(by_engine_groups.items())}

    # MFE / MAE & SL-Buffer Diagnostics
    wins = [b for b in battles if b.get("is_win") is True]
    losses = [b for b in battles if b.get("status") == "STOPPED_OUT"]
    sl_dists = [float(b["sl_distance_atr"]) for b in (wins + losses) if b.get("sl_distance_atr") is not None]
    win_maes = [float(b["mae_atr"]) for b in wins if b.get("mae_atr") is not None]
    loss_mfes = [float(b["mfe_atr"]) for b in losses if b.get("mfe_atr") is not None]
    sweep_saved = [b for b in wins if b.get("wick_breached_sl")]
    sweep_depths = [float(b["max_wick_beyond_sl_atr"]) for b in sweep_saved if b.get("max_wick_beyond_sl_atr") is not None]

    # Losses that reached >= 70% of TP1 before reversing
    near_tp1_reversals = 0
    for b in losses:
        tp1_atr = b.get("tp1_distance_atr")
        mfe_atr = b.get("mfe_atr")
        if tp1_atr and tp1_atr > 0 and mfe_atr is not None and (float(mfe_atr) / float(tp1_atr)) >= 0.70:
            near_tp1_reversals += 1

    # Missed-by-ATR Zone-Depth Diagnostics
    missed_tp_rows = [b for b in battles if b.get("status") == "MISSED_TP"]
    missed_dists = [float(b["missed_by_atr"]) for b in missed_tp_rows if b.get("missed_by_atr") is not None]
    missed_sorted = sorted(missed_dists)
    median_missed = (
        round(missed_sorted[len(missed_sorted) // 2], 3) if missed_sorted else None
    )

    # Verdict tag frequencies
    verdict_counts: Dict[str, int] = {}
    for b in battles:
        for tag in (b.get("verdict_tags") or []):
            verdict_counts[tag] = verdict_counts.get(tag, 0) + 1

    # Concrete, data-backed calibration hints
    hints: List[str] = []
    decided_n = len(wins) + len(losses)
    if decided_n < 5:
        hints.append(
            f"Baseline collection in progress ({decided_n} decided battle(s)). "
            "Gather at least 5-10 decided battles before adjusting ATR/SL parameters."
        )
    else:
        if len(losses) >= 3 and (near_tp1_reversals / len(losses)) >= 0.40:
            hints.append(
                f"TP1 / BE Calibration: {near_tp1_reversals}/{len(losses)} losses reached >=70% of TP1 before reversing. "
                "Consider tightening TP1 ATR multiple or moving stop to breakeven earlier."
            )
        if sweep_saved and len(wins) > 0:
            pct_saved = round((len(sweep_saved) / len(wins)) * 100.0, 1)
            avg_sw = round(sum(sweep_depths) / len(sweep_depths), 3) if sweep_depths else 0.0
            hints.append(
                f"M15 Close SL Edge: {len(sweep_saved)}/{len(wins)} wins ({pct_saved}%) survived a wick sweep past SL "
                f"(avg wick overshoot {avg_sw} ATR). Keep close-based SL active."
            )
        if verdict_counts.get("SL Too Tight", 0) >= 2:
            hints.append(
                f"Trader Verdict Signal: 'SL Too Tight' tagged {verdict_counts['SL Too Tight']} times. "
                "Consider adding +0.15 to +0.25 ATR padding to stop-loss placement."
            )
    if len(missed_tp_rows) >= 2 and missed_dists:
        avg_m = round(sum(missed_dists) / len(missed_dists), 3)
        hints.append(
            f"Zone Depth Calibration: {len(missed_tp_rows)} plans ran to TP1 without filling (avg missed by {avg_m} ATR, "
            f"median {median_missed} ATR). Consider widening entry corridor front-edge by ~{round(min(avg_m, 0.35), 2)} ATR."
        )

    return {
        "symbol_filter": symbol or "ALL",
        "overall": overall,
        "by_pair": by_pair,
        "by_session": by_session,
        "by_engine": by_engine,
        "mfe_mae": {
            "avg_sl_distance_atr": round(sum(sl_dists) / len(sl_dists), 3) if sl_dists else None,
            "avg_win_mae_atr": round(sum(win_maes) / len(win_maes), 3) if win_maes else None,
            "avg_loss_mfe_atr": round(sum(loss_mfes) / len(loss_mfes), 3) if loss_mfes else None,
            "wick_sweeps_saved": len(sweep_saved),
            "avg_sweep_overshoot_atr": round(sum(sweep_depths) / len(sweep_depths), 3) if sweep_depths else None,
            "near_tp1_reversals": near_tp1_reversals,
        },
        "zone_depth": {
            "missed_tp_count": len(missed_tp_rows),
            "avg_missed_by_atr": round(sum(missed_dists) / len(missed_dists), 3) if missed_dists else None,
            "median_missed_by_atr": median_missed,
        },
        "verdict_counts": verdict_counts,
        "calibration_hints": hints,
    }
