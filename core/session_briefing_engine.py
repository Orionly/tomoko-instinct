# Autonomous Pre-Session AI Trade Battle-Plan Engine
#
# Generates scheduled institutional trade briefings across all 7 pairs
# using Gemini 2.5 Flash in a single batched cloud API call.
#
# Schedule (30 min prior to liquidity handoffs):
# - Tokyo:    23:30 UTC / 07:30 MYT
# - London:   07:30 UTC / 15:30 MYT
# - New York: 12:30 UTC / 20:30 MYT

import os
import re
import time
import json
import logging
import threading
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

import config
import pandas as pd
from core.macro_scenario_engine import MacroScenarioEngine
from core.trap_radar_engine import TrapRadarEngine
from core.decision_zones import calculate_all_decision_zones
from core.regime_engine import RegimeEngine

log = logging.getLogger(__name__)
from core.telemetry_bus import emit_flow, STATE_LIVE, STATE_CACHED, STATE_FALLBACK, STATE_ERROR

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIEFING_CACHE_FILE = os.path.join(ROOT, "logs", "session_briefings.json")
TUNED_ENTRY_DNA_PATH = os.path.join(ROOT, "data", "tuned_entry_dna.json")

DEFAULT_ENTRY_PARAMS: Dict[str, Any] = {
    "default_sl_buf_atr": 0.85,
    "max_sl_dist_atr": 2.20,
    "min_tp1_rr": 0.80,
    "tp1_rr": 1.00,
    "tp2_rr": 2.00,
    "wick_rejection_ratio": 0.55,
    "require_displacement": True,
}
_ENTRY_DNA_CACHE: Dict[str, Any] = {"mtime": None, "data": None}


def load_tuned_entry_dna() -> Optional[Dict[str, Any]]:
    """Load weekend-calibrated Entry Execution DNA from data/tuned_entry_dna.json (mtime-cached)."""
    try:
        if not os.path.isfile(TUNED_ENTRY_DNA_PATH):
            _ENTRY_DNA_CACHE["mtime"] = None
            _ENTRY_DNA_CACHE["data"] = None
            return None
        mtime = os.path.getmtime(TUNED_ENTRY_DNA_PATH)
        if _ENTRY_DNA_CACHE["mtime"] != mtime:
            with open(TUNED_ENTRY_DNA_PATH, "r", encoding="utf-8") as f:
                _ENTRY_DNA_CACHE["data"] = json.load(f)
            _ENTRY_DNA_CACHE["mtime"] = mtime
        return _ENTRY_DNA_CACHE["data"]
    except Exception:
        return None


def get_effective_entry_params(symbol: str = "XAUUSD") -> Dict[str, Any]:
    """Return effective SL/TP/trigger parameters, merging weekend-calibrated overrides when available."""
    eff = dict(DEFAULT_ENTRY_PARAMS)
    tuned = load_tuned_entry_dna()
    if not isinstance(tuned, dict) or int(tuned.get("trades_total", 0) or 0) <= 0:
        return eff
    global_p = tuned.get("effective_params") or {}
    if isinstance(global_p, dict):
        for k in eff:
            if k in global_p and global_p[k] is not None:
                eff[k] = global_p[k]
    per_sym = (tuned.get("per_symbol_params") or {}).get(str(symbol).upper())
    if isinstance(per_sym, dict):
        for k in eff:
            if k in per_sym and per_sym[k] is not None:
                eff[k] = per_sym[k]
    return eff


def get_entry_dna_badge() -> str:
    tuned = load_tuned_entry_dna()
    if not isinstance(tuned, dict) or int(tuned.get("trades_total", 0) or 0) <= 0:
        return "FACTORY_DEFAULT"
    stage = tuned.get("calibration_stage", "STAGE_1_PLAYBOOK_ONLY")
    n = int(tuned.get("trades_total", 0))
    return f"{stage} ({n} trade{'s' if n != 1 else ''})"


def build_entry_dna_prompt_block() -> str:
    """Build a compact weekend-calibrated Trader Execution DNA block for the Gemini Battle-Plan prompt."""
    tuned = load_tuned_entry_dna()
    if not isinstance(tuned, dict) or int(tuned.get("trades_total", 0) or 0) <= 0:
        return ""
    pb = tuned.get("playbook") or {}
    ep = tuned.get("effective_params") or DEFAULT_ENTRY_PARAMS
    es = tuned.get("empirical_stats") or {}

    lines = [
        f"\nTRADER EXECUTION DNA PLAYBOOK (WEEKEND-CALIBRATED · {get_entry_dna_badge()}):",
        "- Align `entry_reason`, `trigger_confirmation`, `stop_loss` invalidation style, and Safety `targets` to the trader's calibrated execution rules below (while keeping ALL prices 100% anchored to live MT5 telemetry).",
    ]
    pats = pb.get("dominant_patterns") or []
    if pats:
        pat_txt = ", ".join(f"{p.get('pattern')} ({p.get('share_pct')}%)" for p in pats[:4])
        lines.append(f"- Preferred Setup Patterns: {pat_txt}")
    wick_pct = int(round(float(ep.get("wick_rejection_ratio", 0.55)) * 100))
    disp_txt = " + displacement close confirmed" if ep.get("require_displacement", True) else ""
    lines.append(f"- Trigger Candle DNA: Rejection wick >= {wick_pct}% of candle range{disp_txt}")
    if es.get("median_rr") is not None:
        lines.append(f"- Empirical Median Target R:R: {es['median_rr']}R (Safety TP1 >= {ep.get('tp1_rr', 1.0)}R, TP2 ~ {ep.get('tp2_rr', 2.0)}R at opposing structural shelf)")
    inv_rules = pb.get("invalidation_rules") or []
    if inv_rules:
        lines.append(f"- Stop-Loss Invalidation Style: {' | '.join(inv_rules[:3])}")
    tp_rules = pb.get("tp_rules") or []
    if tp_rules:
        lines.append(f"- Safety Take-Profit Style: {' | '.join(tp_rules[:3])}")
    examples = pb.get("example_setups") or []
    if examples:
        lines.append("- Distilled Real Execution Style Examples:")
        for ex in examples[:3]:
            ex_line = (
                f"  * [{ex.get('symbol')} {ex.get('timeframe')} {ex.get('direction')} · {ex.get('pattern')} · R:R {ex.get('risk_reward')}] "
                f"Why: {ex.get('why_entered') or '—'} | SL: {ex.get('invalidation_logic') or '—'} | TP: {ex.get('tp_logic') or '—'}"
            )
            lines.append(ex_line)
    return "\n".join(lines) + "\n"

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "EURJPY", "EURGBP", "XAUUSD"]

SYSTEM_PROMPT = """You are the Senior Chief Market Strategist for an elite institutional prop trading desk.
Your mandate is to provide actionable, institutional-grade Pre-Session Trade Battle-Plans for professional traders.
Avoid generic commentary. Every pair requires specific price levels derived STRICTLY from the provided live MT5 broker telemetry, quantitative entry triggers (e.g. M5 rejection wicks), hard stop losses with realistic ATR risk, staged take-profit targets, top-down multi-timeframe structure, and catalyst IF/THEN scenario branching.

CRITICAL RULES:
- NEVER invent or copy prices from web search snippets. Every price level (entry zone, SL, TP, invalidation, landmarks) MUST come strictly from Tomoko's live MT5 telemetry.
- Respect multi-day crash/liquidation regimes and TrapRadar alerts: never recommend blind hero counter-trend trades or sell directly into an active CRITICAL BEAR TRAP.
- You must respond with valid JSON adhering strictly to the requested schema for all pairs."""


class SessionBriefingEngine:
    """
    Autonomous Pre-Session Briefing Engine.
    Executes a 2-step institutional pipeline 30 minutes before each major session:
      1. Lightweight Google-Search-grounded live macro narrative brief (with WorldNewsEngine RSS fallback).
      2. Single batched structured JSON Battle-Plan across all 7 pairs anchored to live MT5 telemetry,
         Decision Zones, Trap Radar, Multi-Day Regime, and Catalyst-to-Pair scenario trees.
    """

    def __init__(
        self,
        price_engine=None,
        levels_engine=None,
        news_engine=None,
        macro_engine=None,
        macro_scenario_engine=None,
        trap_radar_engine=None,
        battle_record_engine=None,
        world_news_engine=None,
    ):
        self.api_key = getattr(config, "GEMINI_API_KEY", "")
        self.model = getattr(config, "GEMINI_MODEL", "gemini-2.5-flash")
        self.enable_web_grounding = bool(getattr(config, "BRIEFING_ENABLE_WEB_GROUNDING", True))
        self.price_engine = price_engine
        self.levels_engine = levels_engine
        self.news_engine = news_engine
        self.macro_engine = macro_engine
        self.world_news_engine = world_news_engine
        self.regime_engine = RegimeEngine()
        self.macro_scenario_engine = macro_scenario_engine or MacroScenarioEngine(
            news_engine=news_engine, macro_engine=macro_engine, price_engine=price_engine
        )
        self.trap_radar_engine = trap_radar_engine or TrapRadarEngine(
            price_engine=price_engine, levels_engine=levels_engine
        )
        self.battle_record_engine = battle_record_engine

        self.latest_briefing: Dict = {}
        self._grounded_macro_cache: Dict[str, Any] = {"session_key": None, "text": "", "fetched_at": 0.0}
        self._lock = threading.Lock()
        self._gen_lock = threading.Lock()
        self._is_generating = False
        self._stop_event = threading.Event()
        self._last_triggered_session: Optional[str] = None

        # Load existing briefing from disk cache for 0ms startup
        self._load_cache()
        if self.battle_record_engine and not self.battle_record_engine.battles and self.latest_briefing:
            try:
                self.battle_record_engine.record_briefing(self.latest_briefing)
            except Exception as e:
                log.debug("[SessionBriefing] BattleRecord initial seed note: %s", e)

        # Start background scheduler thread
        self._thread = threading.Thread(target=self._scheduler_loop, daemon=True, name="session-briefing-scheduler")
        self._thread.start()

    def _load_cache(self) -> bool:
        try:
            if os.path.isfile(BRIEFING_CACHE_FILE):
                with open(BRIEFING_CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    with self._lock:
                        self.latest_briefing = data
                    log.info("[SessionBriefing] Loaded session briefing from disk cache.")
                    return True
        except Exception as e:
            log.debug("[SessionBriefing] Cache read note: %s", e)
        return False

    def _save_cache(self):
        try:
            os.makedirs(os.path.dirname(BRIEFING_CACHE_FILE), exist_ok=True)
            with open(BRIEFING_CACHE_FILE, "w", encoding="utf-8") as f:
                with self._lock:
                    json.dump(self.latest_briefing, f, indent=2)
        except Exception as e:
            log.debug("[SessionBriefing] Cache write note: %s", e)

    @staticmethod
    def get_current_session_info(now_utc: Optional[datetime] = None) -> Dict[str, Any]:
        """Calculates current UTC time, market-open status, and active/upcoming session."""
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)
        elif now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)
        else:
            now_utc = now_utc.astimezone(timezone.utc)

        weekday = now_utc.weekday()  # 0=Mon .. 4=Fri, 5=Sat, 6=Sun
        cur_min = now_utc.hour * 60 + now_utc.minute

        # Global FX institutional week: closes Fri 21:00 UTC (1260m), reopens Sun 21:00 UTC (1260m)
        is_weekend = (weekday == 5) or (weekday == 4 and cur_min >= 1260) or (weekday == 6 and cur_min < 1260)

        # Session boundaries (UTC):
        # - Weekend close: Fri 21:00 -> Sun 21:00
        # - Tokyo / Asian: 23:30 -> 07:30 (plus Sun 21:00 -> 23:30 weekly open)
        # - London / European: 07:30 -> 12:30
        # - New York / US: 12:30 -> 23:30 (Fri ends at 21:00)
        if is_weekend:
            session_name = "WEEKEND_CLOSED"
            session_title = "Weekend Market Close (Pre-Tokyo)"
        elif (weekday == 6 and cur_min >= 1260) or cur_min >= 1410 or cur_min < 450:
            session_name = "TOKYO_ASIAN"
            session_title = "Tokyo / Asian Session"
        elif 450 <= cur_min < 750:
            session_name = "LONDON_EUROPEAN"
            session_title = "London / European Session"
        else:
            session_name = "NEW_YORK_US"
            session_title = "New York / US Session"

        # Anchor Tokyo evening hours (21:00-23:59 UTC) to next UTC date so the session key stays continuous past 00:00 UTC
        session_date = (now_utc + timedelta(days=1)).strftime("%Y-%m-%d") if (session_name == "TOKYO_ASIAN" and cur_min >= 1260) else now_utc.strftime("%Y-%m-%d")

        return {
            "session_name": session_name,
            "session_title": session_title,
            "time_utc": now_utc.strftime("%Y-%m-%d %H:%M UTC"),
            "is_weekend": is_weekend,
            "is_market_open": not is_weekend,
            "session_key": f"{session_date}:{session_name}",
        }

    @staticmethod
    def _compute_d1_regime(df_d1, sym: str, spot: float, digits: int) -> Dict[str, Any]:
        """Extract multi-day (1D/5D/20D) regime, streak, and recent D1 closes from live MT5 D1 bars."""
        if df_d1 is None or len(df_d1) < 2:
            return {}
        try:
            closes = df_d1["close"].dropna()
            if len(closes) < 2:
                return {}
            last_close = float(spot or closes.iloc[-1])
            prev_close = float(closes.iloc[-2])
            chg_1d = round((last_close - prev_close) / prev_close * 100.0, 2) if prev_close else 0.0

            idx_5d = max(0, len(closes) - 6)
            base_5d = float(closes.iloc[idx_5d])
            chg_5d = round((last_close - base_5d) / base_5d * 100.0, 2) if base_5d else 0.0

            base_20d = float(closes.iloc[0])
            chg_20d = round((last_close - base_20d) / base_20d * 100.0, 2) if base_20d else 0.0

            high_20d = round(float(df_d1["high"].dropna().max()), digits) if "high" in df_d1 else round(float(closes.max()), digits)
            low_20d = round(float(df_d1["low"].dropna().min()), digits) if "low" in df_d1 else round(float(closes.min()), digits)

            # Consecutive red/green daily candle streak (completed + current)
            streak_dir = None
            streak_cnt = 0
            for i in range(len(df_d1) - 1, -1, -1):
                row = df_d1.iloc[i]
                o, c = float(row["open"]), float(row["close"])
                d = "RED" if c < o else ("GREEN" if c > o else "DOJI")
                if streak_dir is None:
                    if d == "DOJI":
                        break
                    streak_dir = d
                    streak_cnt = 1
                elif d == streak_dir:
                    streak_cnt += 1
                else:
                    break
            streak_str = f"{streak_cnt} {streak_dir.lower()} day{'s' if streak_cnt != 1 else ''}" if streak_dir else "flat"

            # Recent 5 D1 closes for top-down narrative
            recent_closes = []
            for i in range(max(0, len(df_d1) - 5), len(df_d1)):
                idx_val = df_d1.index[i]
                if hasattr(idx_val, "strftime"):
                    dt_lbl = idx_val.strftime("%m-%d")
                elif "time" in df_d1.columns:
                    try:
                        dt_lbl = pd.to_datetime(df_d1["time"].iloc[i]).strftime("%m-%d")
                    except Exception:
                        dt_lbl = f"D-{len(df_d1)-1-i}"
                else:
                    dt_lbl = f"D-{len(df_d1)-1-i}"
                recent_closes.append(f"{dt_lbl}: {round(float(df_d1['close'].iloc[i]), digits)}")

            # Regime classification scaled for Metals vs FX volatility
            is_gold = (sym == "XAUUSD")
            crash_5d = -4.0 if is_gold else -1.8
            crash_20d = -8.0 if is_gold else -3.0
            trend_5d = 1.5 if is_gold else 0.7

            if chg_5d <= crash_5d or chg_20d <= crash_20d:
                regime_label = "CRASH_BREAKDOWN"
            elif chg_5d >= abs(crash_5d) or chg_20d >= abs(crash_20d):
                regime_label = "PARABOLIC_BREAKOUT"
            elif chg_5d <= -trend_5d:
                regime_label = "TRENDING_BEAR"
            elif chg_5d >= trend_5d:
                regime_label = "TRENDING_BULL"
            else:
                regime_label = "RANGE_CONSOLIDATION"

            return {
                "regime_label": regime_label,
                "change_1d_pct": chg_1d,
                "change_5d_pct": chg_5d,
                "change_20d_pct": chg_20d,
                "streak": streak_str,
                "high_20d": high_20d,
                "low_20d": low_20d,
                "prev_close": round(prev_close, digits),
                "recent_daily_closes": recent_closes,
            }
        except Exception:
            return {}

    @staticmethod
    def _compute_session_landmarks(df_h1, lv: Dict, digits: int) -> Dict[str, Any]:
        """Extract today's Asia (00-07 UTC) and London (07-13 UTC) highs/lows from live H1 candles."""
        landmarks = {
            "pdh": round(float(lv["daily_high"]), digits) if lv.get("daily_high") else None,
            "pdl": round(float(lv["daily_low"]), digits) if lv.get("daily_low") else None,
            "prev_close": round(float(lv["prev_close"]), digits) if lv.get("prev_close") else None,
            "today_high": round(float(lv["today_high"]), digits) if lv.get("today_high") else None,
            "today_low": round(float(lv["today_low"]), digits) if lv.get("today_low") else None,
            "pivot": round(float(lv["pivot"]), digits) if lv.get("pivot") else None,
            "weekly_open": round(float(lv["weekly_open"]), digits) if lv.get("weekly_open") else None,
        }
        if df_h1 is None or len(df_h1) == 0:
            return landmarks
        try:
            if isinstance(df_h1.index, pd.DatetimeIndex):
                times = df_h1.index
            elif "time" in df_h1.columns:
                times = pd.to_datetime(df_h1["time"], utc=True, errors="coerce")
            else:
                return landmarks

            if times.isna().all():
                return landmarks

            last_date = times[-1].date() if hasattr(times[-1], "date") else pd.Timestamp(times[-1]).date()
            df_work = df_h1.copy()
            df_work["_ts"] = pd.to_datetime(times, utc=True)
            df_today = df_work[df_work["_ts"].dt.date == last_date]
            if len(df_today) == 0:
                return landmarks

            asia = df_today[(df_today["_ts"].dt.hour >= 0) & (df_today["_ts"].dt.hour < 8)]
            if len(asia) > 0:
                landmarks["asia_high"] = round(float(asia["high"].max()), digits)
                landmarks["asia_low"] = round(float(asia["low"].min()), digits)

            london = df_today[(df_today["_ts"].dt.hour >= 7) & (df_today["_ts"].dt.hour < 14)]
            if len(london) > 0:
                landmarks["london_high"] = round(float(london["high"].max()), digits)
                landmarks["london_low"] = round(float(london["low"].min()), digits)
        except Exception:
            pass
        return landmarks

    def _build_telemetry_payload(self) -> Dict:
        """Assembles real-time telemetry from all sub-engines."""
        now_utc = datetime.now(timezone.utc)
        session_info = self.get_current_session_info(now_utc)
        day_name = now_utc.strftime("%A")
        is_friday_ny = (now_utc.weekday() == 4 and session_info["session_name"] == "NEW_YORK_US")
        discipline_hint = (
            "FRIDAY NY SESSION: Use reduced sizing, bank partials early, and DO NOT hold open intraday trades overnight into the weekend close (21:00 UTC)."
            if is_friday_ny
            else f"{day_name} {session_info['session_title']}: Trade structural extremes with M5 confirmation; respect multi-day trend and active Trap Radar alerts."
        )

        payload = {
            "session": session_info["session_title"],
            "session_name": session_info["session_name"],
            "day_of_week": day_name,
            "is_friday_ny": is_friday_ny,
            "session_discipline_hint": discipline_hint,
            "timestamp_utc": session_info["time_utc"],
            "macro_telemetry": {},
            "world_news_headlines": [],
            "pairs": {}
        }

        # 1. Live Macro Telemetry (fixed wiring via get_full_intelligence / get_macro_matrix)
        if self.macro_engine:
            try:
                if hasattr(self.macro_engine, "get_full_intelligence"):
                    full_intel = self.macro_engine.get_full_intelligence() or {}
                    matrix = full_intel.get("matrix", {})
                    insight = full_intel.get("insight", {})
                else:
                    matrix = self.macro_engine.get_macro_matrix() if hasattr(self.macro_engine, "get_macro_matrix") else {}
                    insight = self.macro_engine.synthesize_macro_insight(matrix) if hasattr(self.macro_engine, "synthesize_macro_insight") else {}
                tips = matrix.get("TIPS_HYBRID", {})
                payload["macro_telemetry"] = {
                    "dxy": matrix.get("DXY", {}),
                    "us10y": matrix.get("US10Y", {}),
                    "vix": matrix.get("VIX", {}),
                    "spx": matrix.get("SPX", {}),
                    "oil": matrix.get("OIL", {}),
                    "gold_fut": matrix.get("GOLD_FUT", {}),
                    "real_yield_10y_tips": tips.get("real_yield_pct", 2.88),
                    "real_yield_pressure": tips.get("yield_pressure", "STEADY"),
                    "regime": insight.get("regime", "BALANCED // CONSOLIDATION REGIME"),
                    "regime_thesis": insight.get("decision_context", ""),
                    "playbook": insight.get("playbook", ""),
                    "gold_impact": insight.get("gold_impact", ""),
                    "usd_impact": insight.get("usd_impact", ""),
                }
            except Exception as e:
                log.warning("[SessionBriefing] Macro fetch error: %s", e)

        # 1b. Recent World / FX / Gold / Fed Headlines (WorldNewsEngine)
        if self.world_news_engine:
            try:
                if hasattr(self.world_news_engine, "get_macro_brief_headlines"):
                    payload["world_news_headlines"] = self.world_news_engine.get_macro_brief_headlines(max_hours=36, limit=14)
                elif hasattr(self.world_news_engine, "get_news"):
                    raw_items = self.world_news_engine.get_news(limit=12)
                    payload["world_news_headlines"] = [
                        {"title": it.get("title", ""), "source": it.get("source", ""), "severity": it.get("severity", "MONITOR"), "impacted_pairs": it.get("impacted_pairs", [])}
                        for it in raw_items
                    ]
            except Exception as e:
                log.debug("[SessionBriefing] WorldNews fetch note: %s", e)

        # 2. Per-Pair Telemetry
        for sym in PAIRS:
            digits = 3 if "JPY" in sym else (2 if sym == "XAUUSD" else 5)
            pair_data = {"symbol": sym}
            df_d1 = pd.DataFrame()
            df_h1 = pd.DataFrame()
            df_h4 = pd.DataFrame()
            df_m15 = pd.DataFrame()

            if self.price_engine:
                p_info = self.price_engine.get_price(sym) or {}
                pair_data["spot_price"] = p_info.get("price")
                pair_data["bid"] = p_info.get("bid")
                pair_data["ask"] = p_info.get("ask")
                pair_data["spread_pts"] = p_info.get("spread")

                df_d1 = self.price_engine.get_rates(sym, "D1", 30)
                df_h4 = self.price_engine.get_rates(sym, "H4", 120)
                df_h1 = self.price_engine.get_rates(sym, "H1", 200)
                df_m15 = self.price_engine.get_rates(sym, "M15", 200)

                if not df_h1.empty:
                    pair_data["h1_close"] = float(df_h1["close"].iloc[-1])
                    pair_data["h1_atr"] = float(df_h1["ATR"].iloc[-1]) if "ATR" in df_h1 else None
                    pair_data["h1_rsi"] = float(df_h1["RSI"].iloc[-1]) if "RSI" in df_h1 else None
                    pair_data["h1_ema21"] = float(df_h1["EMA21"].iloc[-1]) if "EMA21" in df_h1 else None

                # Multi-day D1 regime (1D/5D/20D %, streak, 20D high/low, crash detection)
                if not df_d1.empty:
                    pair_data["d1_regime"] = self._compute_d1_regime(
                        df_d1, sym, float(pair_data.get("spot_price") or df_d1["close"].iloc[-1]), digits
                    )

                # H4 institutional regime gate (direction, ADX, ATR ratio, EMA21 distance)
                if not df_h4.empty and all(c in df_h4.columns for c in ("EMA20", "EMA50", "EMA21", "ADX", "ATR", "close")):
                    try:
                        h4_eval = self.regime_engine.evaluate_h4_gate(df_h4)
                        pair_data["h4_regime"] = {
                            "direction": h4_eval.get("direction"),
                            "gate": h4_eval.get("gate"),
                            "adx": h4_eval.get("adx"),
                            "atr_ratio": h4_eval.get("atr_ratio"),
                            "dist_ema21_atr": h4_eval.get("dist_ema21_atr"),
                        }
                    except Exception as e:
                        log.debug("[SessionBriefing] H4 regime note for %s: %s", sym, e)

            # Levels & Session Landmarks
            lv = {}
            if self.levels_engine and self.price_engine:
                try:
                    df_w1 = self.price_engine.get_rates(sym, "W1", 5)
                    if not df_d1.empty and not df_h1.empty:
                        lv = self.levels_engine.get_key_levels(df_d1, df_w1, df_h1)
                        pair_data["pdh"] = lv.get("daily_high")
                        pair_data["pdl"] = lv.get("daily_low")
                        pair_data["prev_close"] = lv.get("prev_close")
                        pair_data["weekly_open"] = lv.get("weekly_open")
                        pair_data["today_high"] = lv.get("today_high")
                        pair_data["today_low"] = lv.get("today_low")
                        pair_data["pivot"] = lv.get("pivot")
                        pair_data["session_landmarks"] = self._compute_session_landmarks(df_h1, lv, digits)
                except Exception as e:
                    log.debug("[SessionBriefing] Levels error for %s: %s", sym, e)

            # Decision Zones (M15/H1 Supply & Demand anchors)
            if self.price_engine and pair_data.get("spot_price"):
                try:
                    dz_all = calculate_all_decision_zones(
                        df_m15, df_h1, df_h4, float(pair_data["spot_price"]), lv, symbol=sym
                    )
                    def _compact_dz(z):
                        if not isinstance(z, dict) or z.get("low") is None or z.get("high") is None:
                            return None
                        return {
                            "low": round(float(z["low"]), digits),
                            "high": round(float(z["high"]), digits),
                            "midline": round(float(z.get("midline") or (z["low"] + z["high"]) / 2.0), digits),
                            "is_swap": bool(z.get("is_swap", False)),
                        }
                    m15_d = dz_all.get("m15") or {}
                    h1_d = dz_all.get("h1") or {}
                    pair_data["decision_zones"] = {
                        "m15": {
                            "upper_dz_1": _compact_dz(m15_d.get("upper_dz_1")),
                            "upper_dz_2": _compact_dz(m15_d.get("upper_dz_2")),
                            "lower_dz_1": _compact_dz(m15_d.get("lower_dz_1")),
                            "lower_dz_2": _compact_dz(m15_d.get("lower_dz_2")),
                        },
                        "h1": {
                            "upper_dz_1": _compact_dz(h1_d.get("upper_dz_1")),
                            "lower_dz_1": _compact_dz(h1_d.get("lower_dz_1")),
                        },
                    }
                except Exception as e:
                    log.debug("[SessionBriefing] DecisionZones telemetry note for %s: %s", sym, e)

            # MacroScenarioEngine catalyst-to-pair scenario trees
            if self.macro_scenario_engine:
                try:
                    briefing = self.macro_scenario_engine.get_pair_catalyst_briefing(sym)
                    pair_data["catalyst_briefing"] = briefing
                    pair_data["catalyst_scenarios"] = briefing.get("catalysts", [])
                except Exception as e:
                    log.debug("[SessionBriefing] MacroScenarioEngine error for %s: %s", sym, e)

            # TrapRadarEngine liquidity sweep & trap diagnosis
            if self.trap_radar_engine:
                try:
                    trap_data = self.trap_radar_engine.scan_pair_liquidity_traps(sym)
                    pair_data["trap_radar"] = trap_data
                except Exception as e:
                    log.debug("[SessionBriefing] TrapRadarEngine error for %s: %s", sym, e)

            # News upcoming catalysts fallback
            if self.news_engine and "catalyst_scenarios" not in pair_data:
                try:
                    sym_news = self.news_engine.get_news_for_symbol(sym, hours_ahead=24)
                    pair_data["upcoming_events"] = [
                        f"{ev.get('time_utc', '')} {ev.get('currency', '')} {ev.get('title', '')} (Impact: {ev.get('impact', '')}, Fore: {ev.get('forecast', '')}, Prev: {ev.get('previous', '')})"
                        for ev in sym_news.get("upcoming_events", [])[:3]
                    ]
                except Exception as e:
                    log.debug("[SessionBriefing] News error for %s: %s", sym, e)

            payload["pairs"][sym] = pair_data

        return payload

    @staticmethod
    def _build_prompt_telemetry(telemetry: Dict) -> Dict:
        """
        Produces a lean, high-signal telemetry subset for the Gemini prompt.
        Strips redundant UI boilerplate from catalyst_scenarios & trap_radar to minimize latency and avoid timeouts.
        """
        compact_pairs = {}
        for sym, pd_raw in telemetry.get("pairs", {}).items():
            # Compact catalyst scenarios (top 2 events, only this pair's playbook)
            c_list = []
            for ev in (pd_raw.get("catalyst_scenarios") or [])[:2]:
                sc = ev.get("scenarios") or {}
                hot = sc.get("hot_beat") or {}
                cool = sc.get("cool_miss") or {}
                c_list.append({
                    "event": ev.get("event_title"),
                    "currency": ev.get("currency"),
                    "time": ev.get("time"),
                    "impact": ev.get("impact"),
                    "forecast": ev.get("forecast"),
                    "previous": ev.get("previous"),
                    "hot_trigger": hot.get("trigger_condition"),
                    "hot_playbook": (hot.get("pair_playbook") or {}).get(sym),
                    "cool_trigger": cool.get("trigger_condition"),
                    "cool_playbook": (cool.get("pair_playbook") or {}).get(sym),
                })

            trap = pd_raw.get("trap_radar") or {}
            compact_trap = {
                "trap_state": trap.get("trap_state", "NONE"),
                "severity": trap.get("severity", "LOW"),
                "trap_title": trap.get("trap_title"),
                "description": trap.get("description"),
                "guidance": (trap.get("operational_guidance") or {}).get("beginners"),
            } if trap.get("trap_state", "NONE") != "NONE" else {"trap_state": "NONE"}

            compact_pairs[sym] = {
                "spot_price": pd_raw.get("spot_price"),
                "spread_pts": pd_raw.get("spread_pts"),
                "h1_close": pd_raw.get("h1_close"),
                "h1_atr": round(float(pd_raw["h1_atr"]), 5) if pd_raw.get("h1_atr") else None,
                "h1_rsi": round(float(pd_raw["h1_rsi"]), 1) if pd_raw.get("h1_rsi") else None,
                "h1_ema21": round(float(pd_raw["h1_ema21"]), 5) if pd_raw.get("h1_ema21") else None,
                "d1_regime": pd_raw.get("d1_regime"),
                "h4_regime": pd_raw.get("h4_regime"),
                "session_landmarks": pd_raw.get("session_landmarks"),
                "decision_zones": pd_raw.get("decision_zones"),
                "trap_radar": compact_trap,
                "upcoming_catalysts": c_list,
            }

        return {
            "session": telemetry.get("session"),
            "day_of_week": telemetry.get("day_of_week"),
            "is_friday_ny": telemetry.get("is_friday_ny"),
            "session_discipline_hint": telemetry.get("session_discipline_hint"),
            "timestamp_utc": telemetry.get("timestamp_utc"),
            "macro_telemetry": telemetry.get("macro_telemetry"),
            "world_news_headlines": (telemetry.get("world_news_headlines") or [])[:12],
            "pairs": compact_pairs,
        }

    def _fetch_grounded_macro_brief(self, telemetry: Dict, curr_info: Dict[str, Any], force: bool = False) -> str:
        """
        Step 1 of the 2-step AI pipeline:
        Lightweight Gemini call with live Google Search Grounding (plain text, NOT responseMimeType=json)
        to pull real-world 24h-10d macro catalysts (Fed pricing, DXY/Yields narrative, Gold/FX drivers,
        central bank speakers, geopolitics). Cached per session; falls back cleanly to empty string.
        """
        if not self.api_key or not self.enable_web_grounding:
            return ""

        now_ts = time.time()
        s_key = curr_info.get("session_key")
        cached = self._grounded_macro_cache
        cache_age = now_ts - float(cached.get("fetched_at") or 0.0)
        if cached.get("text") and cached.get("session_key") == s_key:
            # Reuse within 90m on normal refresh, or within 10m even on force refresh to protect quota
            if (not force and cache_age < 5400) or (force and cache_age < 600):
                log.info("[SessionBriefing] Reusing cached Google-Search-grounded macro brief (%ds old).", int(cache_age))
                return cached["text"]

        macro = telemetry.get("macro_telemetry") or {}
        dxy_p = (macro.get("dxy") or {}).get("price", "N/A")
        us10y_p = (macro.get("us10y") or {}).get("price", "N/A")
        vix_p = (macro.get("vix") or {}).get("price", "N/A")
        gold_d1 = ((telemetry.get("pairs") or {}).get("XAUUSD") or {}).get("d1_regime") or {}
        gold_5d = gold_d1.get("change_5d_pct", "N/A")

        grounding_prompt = (
            f"Today is {telemetry.get('timestamp_utc')} ({telemetry.get('day_of_week')}), heading into {telemetry.get('session')}.\n"
            f"Live market snapshot from our broker feed: DXY ~{dxy_p}, US 10Y ~{us10y_p}%, VIX ~{vix_p}, Gold 5D change ~{gold_5d}%.\n\n"
            "Search the latest financial & macro news from the last 24 hours to 10 days and provide a crisp, factual 5-bullet Institutional Macro Narrative Brief covering:\n"
            "1. Federal Reserve / FOMC rate expectations (latest minutes, market-implied hike/cut odds, key Fed speakers)\n"
            "2. US Dollar (DXY) & US Treasury Yield drivers (why DXY and yields are moving)\n"
            "3. Gold (XAUUSD) / Metals narrative (why Gold is rallying or selling off — rates, dollar squeeze, liquidation, or safe-haven flows)\n"
            "4. Major FX Central Banks (ECB, BoE, BoJ / Yen intervention watch)\n"
            "5. Equities, Oil & Geopolitics (VIX risk tone, Middle East/energy/tariffs)\n\n"
            "STRICT RULES:\n"
            "- Be concise (max ~220 words total), institutional, and factual with dates/percentages where verified.\n"
            "- Do NOT recommend trade entry/SL/TP prices or quote conflicting retail broker spot prices (our live MT5 feed handles all price levels)."
        )

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        body = json.dumps({
            "contents": [{"parts": [{"text": grounding_prompt}]}],
            "tools": [{"google_search": {}}],
        }).encode("utf-8")

        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        t0 = time.time()
        try:
            log.info("[SessionBriefing] Step 1: Fetching Google-Search-grounded live macro narrative (%s)...", self.model)
            with urllib.request.urlopen(req, timeout=25.0) as resp:
                raw_resp = json.loads(resp.read().decode("utf-8"))
                parts = raw_resp.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                text = "\n".join(str(p.get("text", "")).strip() for p in parts if p.get("text")).strip()
                if text:
                    self._grounded_macro_cache = {"session_key": s_key, "text": text, "fetched_at": now_ts}
                    log.info("[SessionBriefing] Step 1 Grounded macro brief fetched in %.2fs (%d chars).", time.time() - t0, len(text))
                    emit_flow("up.gemini_cloud", "eng.session_briefing", STATE_LIVE,
                              latency_ms=(time.time() - t0) * 1000.0, items=len(text),
                              detail=f"Gemini Step-1 Google Search Grounding succeeded ({len(text)} chars)")
                    return text
        except Exception as e:
            log.warning("[SessionBriefing] Step 1 Google Search grounding skipped/failed (%s). Continuing with WorldNewsEngine headlines.", e)
            emit_flow("up.gemini_cloud", "eng.session_briefing", STATE_FALLBACK,
                      latency_ms=(time.time() - t0) * 1000.0, items=0,
                      detail=f"Gemini Step-1 Search Grounding failed ({type(e).__name__}: {e}) -> falling back to WorldNewsEngine RSS")
        return ""

    def is_briefing_stale(self, now_utc: Optional[datetime] = None) -> bool:
        """Determines if current cached briefing is stale (different session, missing pairs, or > 6 hours old)."""
        now = now_utc or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        else:
            now = now.astimezone(timezone.utc)

        with self._lock:
            if not self.latest_briefing or not self.latest_briefing.get("pairs"):
                return True
            curr_info = self.get_current_session_info(now)
            cached_session = self.latest_briefing.get("session", "")
            if not cached_session or cached_session != curr_info["session_title"]:
                return True

            gen_utc_str = self.latest_briefing.get("generated_at_utc", "")
            if not gen_utc_str:
                return True
            try:
                cleaned = gen_utc_str.replace(" UTC", "").replace("Z", "").strip()
                dt = datetime.fromisoformat(cleaned) if "T" in cleaned else datetime.strptime(cleaned, "%Y-%m-%d %H:%M")
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
                age_hours = (now - dt).total_seconds() / 3600.0
                if age_hours > 6.0 or age_hours < -0.1:
                    return True
            except Exception:
                return True
        return False

    @staticmethod
    def _validate_and_sanitize_setup(
        setup: Dict,
        sym: str,
        spot: float,
        atr: float,
        digits: int,
        pdh: float,
        pdl: float,
        today_high: float,
        today_low: float,
        decision_zones: Optional[Dict] = None,
    ) -> Dict:
        """
        Institutional Guardrail / Assertion Gate.
        Enforces mathematical & structural consistency across every setup:
        - Entry corridor: entry_low < entry_high (width >= 0.15 * ATR).
        - Anti-Collision & Discount/Premium Gate:
          * LONG must NOT sit above spot chasing into Upper DZ 1 Supply; re-anchors to Lower DZ 1 Demand (or discount pullback).
          * SHORT must NOT sit below spot chasing into Lower DZ 1 Demand; re-anchors to Upper DZ 1 Supply (or premium pullback).
        - SHORT: Stop Loss MUST be strictly > entry_high. All TPs MUST be < entry_low and descending.
        - LONG: Stop Loss MUST be strictly < entry_low. All TPs MUST be > entry_high and ascending.
        """
        if not setup or not isinstance(setup, dict):
            return setup

        bias = str(setup.get("bias", "LONG")).upper()
        is_short = "SHORT" in bias or "SELL" in bias
        entry_zone_str = str(setup.get("entry_zone", ""))
        min_buf = 1.50 if sym == "XAUUSD" else (0.15 if "JPY" in sym else 0.0015)
        corridor_buf = max(0.25 * atr, min_buf)

        # Extract M15 (fallback H1) Decision Zones for structural anchoring
        dz_m15 = (decision_zones or {}).get("m15") or {}
        dz_h1 = (decision_zones or {}).get("h1") or {}
        u1 = dz_m15.get("upper_dz_1") or dz_h1.get("upper_dz_1")
        l1 = dz_m15.get("lower_dz_1") or dz_h1.get("lower_dz_1")

        # 1. Parse and validate entry corridor
        match = re.search(r'([0-9]+\.?[0-9]*)\s*-\s*([0-9]+\.?[0-9]*)', entry_zone_str)
        if match:
            e_low = float(match.group(1))
            e_high = float(match.group(2))
            if e_low > e_high:
                e_low, e_high = e_high, e_low
            if (e_high - e_low) < (0.15 * atr):
                e_low = round(e_low - corridor_buf * 0.5, digits)
                e_high = round(e_high + corridor_buf * 0.5, digits)
        else:
            e_low = round(spot - corridor_buf * 0.5, digits)
            e_high = round(spot + corridor_buf * 0.5, digits)

        # 1b. Anti-Collision & Discount/Premium Gate
        reanchored = False
        if spot > 0:
            if not is_short:
                # LONG: never buy above spot chasing into Upper DZ 1 Supply
                chasing_above = e_low > (spot + 0.10 * atr)
                colliding_supply = (
                    u1 is not None
                    and e_high > spot
                    and e_high >= (float(u1["low"]) - 0.15 * atr)
                )
                if chasing_above or colliding_supply:
                    if l1 is not None and float(l1["high"]) <= (spot + 0.15 * atr):
                        e_low = round(float(l1["low"]), digits)
                        e_high = round(float(l1["high"]), digits)
                    else:
                        e_high = round(spot, digits)
                        e_low = round(spot - max(0.6 * atr, corridor_buf), digits)
                    reanchored = True
                    log.warning(
                        "[SessionBriefingSanitizer] Re-anchored LONG entry for %s into demand/discount (%s - %s) to avoid buying above spot/into Upper DZ 1",
                        sym, e_low, e_high,
                    )
            else:
                # SHORT: never sell below spot chasing into Lower DZ 1 Demand
                chasing_below = e_high < (spot - 0.10 * atr)
                colliding_demand = (
                    l1 is not None
                    and e_low < spot
                    and e_low <= (float(l1["high"]) + 0.15 * atr)
                )
                if chasing_below or colliding_demand:
                    if u1 is not None and float(u1["low"]) >= (spot - 0.15 * atr):
                        e_low = round(float(u1["low"]), digits)
                        e_high = round(float(u1["high"]), digits)
                    else:
                        e_low = round(spot, digits)
                        e_high = round(spot + max(0.6 * atr, corridor_buf), digits)
                    reanchored = True
                    log.warning(
                        "[SessionBriefingSanitizer] Re-anchored SHORT entry for %s into supply/premium (%s - %s) to avoid selling below spot/into Lower DZ 1",
                        sym, e_low, e_high,
                    )

        setup["entry_zone"] = f"{round(e_low, digits)} - {round(e_high, digits)}"

        # 2. Parse and validate SL and Targets
        try:
            sl = float(setup.get("stop_loss", 0.0))
        except (ValueError, TypeError):
            sl = 0.0

        raw_targets = setup.get("targets", [])
        if not isinstance(raw_targets, list):
            raw_targets = [raw_targets] if raw_targets else []

        valid_targets = []
        for t in raw_targets:
            try:
                t_match = re.search(r'([0-9]+\.?[0-9]*)', str(t))
                if t_match:
                    valid_targets.append(float(t_match.group(1)))
            except Exception:
                pass

        ep = get_effective_entry_params(sym)
        max_sl_atr = float(ep.get("max_sl_dist_atr", 2.20))
        def_sl_atr = float(ep.get("default_sl_buf_atr", 0.85))
        min_tp1_rr = float(ep.get("min_tp1_rr", 0.80))
        tp1_rr = float(ep.get("tp1_rr", 1.00))
        tp2_rr = float(ep.get("tp2_rr", 2.00))

        if is_short:
            # STOP LOSS MUST BE > entry_high AND capped within realistic structural ATR bounds
            max_sl_dist = max(max_sl_atr * atr, min_buf * 3.0)
            default_sl_buf = max(def_sl_atr * atr, min_buf * 1.5)
            if reanchored or sl <= e_high or (sl - e_high) > max_sl_dist:
                # Prefer intraday high if it sits cleanly just above e_high within cap, else zone + buffer
                if today_high > e_high and (today_high - e_high) <= (1.4 * atr):
                    sl = round(today_high + max(0.35 * atr, min_buf), digits)
                else:
                    sl = round(e_high + default_sl_buf, digits)
                log.warning("[SessionBriefingSanitizer] Fixed Short SL for %s -> %s", sym, sl)

            setup["stop_loss"] = sl
            risk = max(abs(sl - e_low), 0.75 * atr)

            # TARGETS MUST BE < entry_low, strictly descending, with TP1 >= min_tp1_rr
            min_tp1 = e_low - min_tp1_rr * risk
            sanitized_targets = [t for t in valid_targets if t <= min_tp1] if not reanchored else []
            if len(sanitized_targets) < 3:
                tp1 = round(e_low - tp1_rr * risk, digits)
                tp2 = round(min(pdl if (pdl > 0 and pdl < tp1 - 0.3 * atr) else (e_low - tp2_rr * risk), e_low - max(1.6, tp2_rr - 0.2) * risk), digits)
                tp3 = round(tp2 - 1.0 * atr, digits)
                sanitized_targets = sorted(list(set(sanitized_targets + [tp1, tp2, tp3])), reverse=True)
            else:
                sanitized_targets = sorted(list(set(sanitized_targets)), reverse=True)

            setup["targets"] = sanitized_targets[:4]
        else:
            # STOP LOSS MUST BE < entry_low AND capped within realistic structural ATR bounds
            max_sl_dist = max(max_sl_atr * atr, min_buf * 3.0)
            default_sl_buf = max(def_sl_atr * atr, min_buf * 1.5)
            if reanchored or sl >= e_low or sl <= 0 or (e_low - sl) > max_sl_dist:
                if today_low > 0 and today_low < e_low and (e_low - today_low) <= (1.4 * atr):
                    sl = round(today_low - max(0.35 * atr, min_buf), digits)
                else:
                    sl = round(e_low - default_sl_buf, digits)
                log.warning("[SessionBriefingSanitizer] Fixed Long SL for %s -> %s", sym, sl)

            setup["stop_loss"] = sl
            risk = max(abs(e_high - sl), 0.75 * atr)

            # TARGETS MUST BE > entry_high, strictly ascending, with TP1 >= min_tp1_rr
            min_tp1 = e_high + min_tp1_rr * risk
            sanitized_targets = [t for t in valid_targets if t >= min_tp1] if not reanchored else []
            if len(sanitized_targets) < 3:
                tp1 = round(e_high + tp1_rr * risk, digits)
                tp2 = round(max(pdh if pdh > (tp1 + 0.3 * atr) else (e_high + tp2_rr * risk), e_high + max(1.6, tp2_rr - 0.2) * risk), digits)
                tp3 = round(tp2 + 1.0 * atr, digits)
                sanitized_targets = sorted(list(set(sanitized_targets + [tp1, tp2, tp3])))
            else:
                sanitized_targets = sorted(list(set(sanitized_targets)))

            setup["targets"] = sanitized_targets[:4]

        setup["_reanchored"] = reanchored
        return setup

    @staticmethod
    def _sync_plan_text_after_sanitize(p_plan: Dict, sym: str, spot: float, digits: int, p_data: Dict) -> Dict:
        """
        If _validate_and_sanitize_setup re-anchored entry_zone, synchronize trigger_confirmation,
        entry_reason, executive_bottom_line, and checklist[0] so text NEVER contradicts the final entry_zone.
        """
        if not isinstance(p_plan, dict):
            return p_plan
        setup = p_plan.get("primary_setup")
        if not isinstance(setup, dict):
            return p_plan

        reanchored = bool(setup.pop("_reanchored", False))
        ez = str(setup.get("entry_zone") or "").strip()
        bias = str(setup.get("bias") or "LONG").upper()
        is_short = "SHORT" in bias or "SELL" in bias
        targets = setup.get("targets") or []
        sl = setup.get("stop_loss")

        if reanchored and ez:
            ez_parts = [x.strip() for x in ez.split("-")]
            anchor_edge = ez_parts[0] if len(ez_parts) >= 1 else ez
            if is_short:
                setup["trigger_confirmation"] = f"M5 upper wick rejection >= 2x body inside {ez} Supply + bearish engulfing close below {anchor_edge}"
            else:
                setup["trigger_confirmation"] = f"M5 lower wick rejection >= 2x body inside {ez} Demand + bullish engulfing close above {anchor_edge}"
            drv = (p_plan.get("macro_catalyst") or {}).get("primary_driver", "Macro Structure")
            setup["entry_reason"] = (
                f"{bias} re-anchored by institutional supply/demand gate to {ez} (spot {round(spot, digits)}, SL {sl}). "
                f"Avoids chasing into opposing Decision Zone. Driver: {drv}."
            )
            tgt_lbl = targets[1] if len(targets) > 1 else (targets[0] if targets else "opposing liquidity")
            p_plan["executive_bottom_line"] = (
                f"{bias} favored on pullback into {ez} {'Supply' if is_short else 'Demand'} (SL {sl}). Primary objective: {tgt_lbl}."
            )
            tc = p_plan.get("traps_and_checklist")
            if isinstance(tc, dict) and isinstance(tc.get("checklist"), list) and len(tc["checklist"]) > 0:
                if "entry zone" in str(tc["checklist"][0]).lower() or "alert" in str(tc["checklist"][0]).lower():
                    tc["checklist"][0] = f"Set alert at entry zone boundary: {anchor_edge}"

        # Sanitize optional conditional counter-trend scalp R:R if present
        cont = p_plan.get("contingency_plan")
        if isinstance(cont, dict) and isinstance(cont.get("scalp_setup"), dict):
            sc = cont["scalp_setup"]
            sc_bias = str(sc.get("bias") or "").upper()
            sc_match = re.search(r'([0-9]+\.?[0-9]*)\s*-\s*([0-9]+\.?[0-9]*)', str(sc.get("zone") or ""))
            try:
                sc_sl = float(sc.get("stop_loss")) if sc.get("stop_loss") is not None else None
            except (TypeError, ValueError):
                sc_sl = None
            if sc_match and sc_sl is not None:
                z_lo, z_hi = sorted([float(sc_match.group(1)), float(sc_match.group(2))])
                sc["zone"] = f"{round(z_lo, digits)} - {round(z_hi, digits)}"
                sc_is_long = "LONG" in sc_bias or "BUY" in sc_bias
                atr = float(p_data.get("h1_atr") or (spot * 0.003 if spot else 1.0)) or 1.0
                if sc_is_long and sc_sl >= z_lo:
                    sc_sl = round(z_lo - 0.7 * atr, digits)
                    sc["stop_loss"] = sc_sl
                elif (not sc_is_long) and sc_sl <= z_hi:
                    sc_sl = round(z_hi + 0.7 * atr, digits)
                    sc["stop_loss"] = sc_sl
                sc_risk = max(abs((z_hi if sc_is_long else z_lo) - sc_sl), 0.4 * atr)
                raw_sc_tgts = sc.get("targets") or []
                clean_sc_tgts = []
                for t in (raw_sc_tgts if isinstance(raw_sc_tgts, list) else [raw_sc_tgts]):
                    tm = re.search(r'([0-9]+\.?[0-9]*)', str(t))
                    if tm:
                        v = float(tm.group(1))
                        if sc_is_long and v >= z_hi + 0.6 * sc_risk:
                            clean_sc_tgts.append(round(v, digits))
                        elif (not sc_is_long) and v <= z_lo - 0.6 * sc_risk:
                            clean_sc_tgts.append(round(v, digits))
                if not clean_sc_tgts:
                    if sc_is_long:
                        clean_sc_tgts = [round(z_hi + 1.0 * sc_risk, digits), round(z_hi + 1.8 * sc_risk, digits)]
                    else:
                        clean_sc_tgts = [round(z_lo - 1.0 * sc_risk, digits), round(z_lo - 1.8 * sc_risk, digits)]
                sc["targets"] = sorted(list(set(clean_sc_tgts)), reverse=(not sc_is_long))[:3]

        return p_plan

    @staticmethod
    def _apply_trap_conflict_gate(p_plan: Dict, trap_info: Optional[Dict]) -> Dict:
        """
        Prevents self-contradictory outputs where TrapRadar fires a CRITICAL opposing trap
        (e.g., CRITICAL BEAR TRAP / 'DO NOT SELL') while primary_setup displays high-confidence SHORT.
        """
        if not isinstance(p_plan, dict) or not isinstance(trap_info, dict):
            return p_plan
        setup = p_plan.get("primary_setup")
        if not isinstance(setup, dict):
            return p_plan

        trap_state = str(trap_info.get("trap_state") or "NONE").upper()
        severity = str(trap_info.get("severity") or "").upper()
        if trap_state == "NONE" or severity not in ("CRITICAL", "HIGH"):
            return p_plan

        bias = str(setup.get("bias") or "").upper()
        is_short = "SHORT" in bias or "SELL" in bias
        is_long = "LONG" in bias or "BUY" in bias

        # BEAR_TRAP / SELL-SIDE LIQUIDITY PURGE opposes SHORT; BULL_TRAP / BUY-SIDE SWEEP opposes LONG
        trap_title = str(trap_info.get("trap_title") or trap_state).upper()
        bear_trap_conflict = is_short and ("BEAR_TRAP" in trap_state or "BEAR TRAP" in trap_title or "SELL-SIDE" in trap_title or "SELL_SIDE" in trap_state)
        bull_trap_conflict = is_long and ("BULL_TRAP" in trap_state or "BULL TRAP" in trap_title or "BUY-SIDE" in trap_title or "BUY_SIDE" in trap_state)

        if bear_trap_conflict or bull_trap_conflict:
            conflict_msg = (
                f"TRAP CONFLICT ({severity} {trap_info.get('trap_title', trap_state)}): "
                f"Active liquidity absorption opposes immediate {bias} entries. Wait strictly for price to reach upper/lower entry corridor and print fresh M5 rejection — do NOT chase at spot."
            )
            setup["trap_conflict_warning"] = conflict_msg
            # Downgrade displayed probability so trader sees reduced edge
            try:
                m = re.search(r'(\d+)', str(setup.get("probability", "60")))
                orig_p = int(m.group(1)) if m else 60
                setup["probability"] = f"{max(40, min(orig_p - 15, 50))}%"
            except Exception:
                setup["probability"] = "45%"
            if conflict_msg not in str(setup.get("entry_reason", "")):
                setup["entry_reason"] = f"{setup.get('entry_reason', '').strip()} [⚠ {conflict_msg}]".strip()

        return p_plan

    def _generate_rule_based_briefing(
        self,
        telemetry: Dict,
        fallback_reason: Optional[str] = None,
        grounded_macro_brief: str = "",
    ) -> Dict:
        """
        Deterministic, zero-API-cost institutional trade briefing generator.
        Synthesizes live 6-pillar multi-day macro regime, MT5 broker Decision Zones, session landmarks,
        D1/H4 multi-day regime, TrapRadar diagnostics, WorldNews headlines, and MacroScenarioEngine branches.
        """
        session_title = telemetry.get("session", "Active Session")
        time_utc = telemetry.get("timestamp_utc", "")
        discipline_hint = telemetry.get("session_discipline_hint", "")
        is_friday_ny = bool(telemetry.get("is_friday_ny", False))
        macro = telemetry.get("macro_telemetry", {})
        dxy = macro.get("dxy", {})
        us10y = macro.get("us10y", {})
        vix = macro.get("vix", {})
        oil = macro.get("oil", {})
        dxy_price = dxy.get("price")
        dxy_macro_trend = dxy.get("macro_trend") or dxy.get("trend", "FLAT")
        dxy_5d = dxy.get("change_5d_pct")
        us10y_price = us10y.get("price")
        us10y_bps = us10y.get("change_bps")
        vix_price = vix.get("price")
        macro_regime = macro.get("regime", "BALANCED // CONSOLIDATION REGIME")
        regime_thesis = macro.get("regime_thesis", "")
        world_headlines = telemetry.get("world_news_headlines") or []

        if regime_thesis:
            executive_thesis = regime_thesis
        else:
            dxy_str = f"{dxy_price:.2f} (5D {dxy_5d:+.2f}%)" if (dxy_price is not None and dxy_5d is not None) else str(dxy_price or "N/A")
            y10_str = f"{us10y_price:.2f}% ({us10y_bps:+.1f} bps)" if (us10y_price is not None and us10y_bps is not None) else str(us10y_price or "N/A")
            executive_thesis = (
                f"[{macro_regime}] DXY {dxy_macro_trend.replace('_', ' ')} at {dxy_str}, "
                f"US 10Y at {y10_str}, VIX at {vix_price if vix_price is not None else 'N/A'}. "
                f"Execute strictly from Decision Zones with M5 confirmation."
            )

        # Build structured "What Happened & Why" macro narrative bullets from live pillars + Grounded Brief + WorldNewsEngine
        macro_narrative_summary = [
            f"Macro Regime: {macro_regime} — DXY {dxy_price if dxy_price is not None else 'N/A'} ({dxy_macro_trend.replace('_',' ')}, 5D {f'{dxy_5d:+.2f}%' if dxy_5d is not None else 'N/A'}), US10Y {f'{us10y_price:.2f}%' if us10y_price is not None else 'N/A'}, VIX {f'{vix_price:.1f}' if vix_price is not None else 'N/A'}, WTI ${oil.get('price', 'N/A')}.",
        ]
        if grounded_macro_brief:
            for ln in grounded_macro_brief.splitlines():
                cleaned = re.sub(r'^[\s\-\*\•\d\.]+', '', ln).replace('**', '').strip()
                if len(cleaned) < 25:
                    continue
                if re.match(r'^(here is|below is|summary of|institutional macro)', cleaned.lower()) and cleaned.endswith(':'):
                    continue
                macro_narrative_summary.append(cleaned)
                if len(macro_narrative_summary) >= 6:
                    break
        elif macro.get("gold_impact"):
            macro_narrative_summary.append(f"Gold & Yields: {macro['gold_impact']}")
        for h in world_headlines[:3]:
            if h.get("title") and len(macro_narrative_summary) < 7:
                macro_narrative_summary.append(f"[{h.get('severity', 'NEWS')}] {h['title']} ({h.get('source', 'Wire')})")

        dxy_strong_multi = dxy_macro_trend in ("UP", "STRONG_UP") or (dxy_5d is not None and dxy_5d >= 0.4)
        dxy_weak_multi = dxy_macro_trend in ("DOWN", "STRONG_DOWN") or (dxy_5d is not None and dxy_5d <= -0.4)

        pairs_plan = {}
        for sym in PAIRS:
            p_data = telemetry.get("pairs", {}).get(sym, {})
            spot = float(p_data.get("spot_price") or 0.0)
            digits = 3 if "JPY" in sym else (2 if sym == "XAUUSD" else 5)
            pdh = float(p_data.get("pdh") or (spot * 1.004 if spot else 0.0))
            pdl = float(p_data.get("pdl") or (spot * 0.996 if spot else 0.0))
            prev_close = float(p_data.get("prev_close") or pivot if (pivot := float(p_data.get("pivot") or spot)) else spot)
            today_high = float(p_data.get("today_high") or spot)
            today_low = float(p_data.get("today_low") or spot)
            atr = float(p_data.get("h1_atr") or (spot * 0.003 if spot else 1.0))
            if atr <= 0:
                atr = spot * 0.003 if spot else 1.0

            d1_reg = p_data.get("d1_regime") or {}
            h4_reg = p_data.get("h4_regime") or {}
            lm = p_data.get("session_landmarks") or {}
            dz_m15 = (p_data.get("decision_zones") or {}).get("m15") or {}
            dz_h1 = (p_data.get("decision_zones") or {}).get("h1") or {}
            u1 = dz_m15.get("upper_dz_1") or dz_h1.get("upper_dz_1")
            u2 = dz_m15.get("upper_dz_2") or dz_h1.get("upper_dz_1")
            l1 = dz_m15.get("lower_dz_1") or dz_h1.get("lower_dz_1")
            l2 = dz_m15.get("lower_dz_2") or dz_h1.get("lower_dz_1")

            scenarios = p_data.get("catalyst_scenarios", [])

            # Multi-timeframe & macro-aware directional bias (avoids naive single-bar spot > pivot flips)
            d1_label = d1_reg.get("regime_label", "RANGE_CONSOLIDATION")
            h4_dir = h4_reg.get("direction", "FLAT")
            h4_gate = h4_reg.get("gate", "INSIDE")
            bull_votes = 0
            bear_votes = 0

            if d1_label in ("CRASH_BREAKDOWN", "TRENDING_BEAR"):
                bear_votes += 2 if d1_label == "CRASH_BREAKDOWN" else 1
            elif d1_label in ("PARABOLIC_BREAKOUT", "TRENDING_BULL"):
                bull_votes += 2 if d1_label == "PARABOLIC_BREAKOUT" else 1

            if h4_dir == "DOWN":
                bear_votes += 2 if h4_gate == "OPEN" else 1
            elif h4_dir == "UP":
                bull_votes += 2 if h4_gate == "OPEN" else 1

            if spot < pivot:
                bear_votes += 1
            elif spot > pivot:
                bull_votes += 1

            if sym in ("XAUUSD", "EURUSD", "GBPUSD"):
                if dxy_strong_multi or "LIQUIDITY CRUNCH" in macro_regime:
                    bear_votes += 1
                elif dxy_weak_multi:
                    bull_votes += 1
            elif sym == "USDJPY":
                if dxy_strong_multi:
                    bull_votes += 1
                elif dxy_weak_multi:
                    bear_votes += 1

            bias = "LONG" if bull_votes > bear_votes else "SHORT"
            edge_gap = abs(bull_votes - bear_votes)
            prob = "70%" if edge_gap >= 3 else ("65%" if edge_gap >= 2 else "58%")

            min_buf = 1.50 if sym == "XAUUSD" else (0.15 if "JPY" in sym else 0.0015)
            buffer = max(0.30 * atr, min_buf)

            # Anchor entry corridor to live Decision Zones / intraday landmarks (NEVER blind far-away Pivot/PDH)
            if bias == "LONG":
                if l1 is not None and float(l1["high"]) <= (spot + 0.15 * atr) and (spot - float(l1["low"])) <= (3.0 * atr):
                    entry_low = round(float(l1["low"]), digits)
                    entry_high = round(float(l1["high"]), digits)
                    anchor_label = "M15/H1 Lower DZ 1 Demand"
                elif today_low > 0 and today_low <= spot and (spot - today_low) <= (2.2 * atr):
                    entry_low = round(today_low, digits)
                    entry_high = round(min(spot, today_low + buffer), digits)
                    anchor_label = "Today Intraday Low Demand"
                else:
                    entry_high = round(spot - 0.15 * atr, digits)
                    entry_low = round(entry_high - buffer, digits)
                    anchor_label = "Discount Pullback Corridor"

                sl = round(entry_low - max(0.95 * atr, min_buf * 1.5), digits)
                risk = max(abs(entry_high - sl), 0.75 * atr)
                # Structural ascending targets
                cand_tgts = [round(entry_high + 1.0 * risk, digits)]
                for lvl in (lm.get("asia_high"), lm.get("london_high"), today_high, prev_close, pdh, u1["low"] if u1 else None):
                    if lvl and float(lvl) >= (entry_high + 0.85 * risk):
                        cand_tgts.append(round(float(lvl), digits))
                cand_tgts.extend([round(entry_high + 2.0 * risk, digits), round(entry_high + 3.0 * risk, digits)])
                tgts = sorted(list(set(cand_tgts)))[:4]
            else:
                if u1 is not None and float(u1["low"]) >= (spot - 0.15 * atr) and (float(u1["high"]) - spot) <= (3.0 * atr):
                    entry_low = round(float(u1["low"]), digits)
                    entry_high = round(float(u1["high"]), digits)
                    anchor_label = "M15/H1 Upper DZ 1 Supply"
                elif today_high >= spot and (today_high - spot) <= (2.2 * atr):
                    entry_high = round(today_high, digits)
                    entry_low = round(max(spot, today_high - buffer), digits)
                    anchor_label = "Today High / Breakdown Retest"
                else:
                    entry_low = round(spot + 0.15 * atr, digits)
                    entry_high = round(entry_low + buffer, digits)
                    anchor_label = "Premium Pullback Corridor"

                sl = round(entry_high + max(0.95 * atr, min_buf * 1.5), digits)
                risk = max(abs(sl - entry_low), 0.75 * atr)
                # Structural descending targets
                cand_tgts = [round(entry_low - 1.0 * risk, digits)]
                for lvl in (lm.get("asia_low"), lm.get("london_low"), today_low, pdl, l1["high"] if l1 else None):
                    if lvl and float(lvl) <= (entry_low - 0.85 * risk):
                        cand_tgts.append(round(float(lvl), digits))
                cand_tgts.extend([round(entry_low - 2.0 * risk, digits), round(entry_low - 3.0 * risk, digits)])
                tgts = sorted(list(set(cand_tgts)), reverse=True)[:4]

            # Pull catalyst branches
            branches = []
            primary_driver = f"{macro_regime} (DXY {dxy_price if dxy_price is not None else 'N/A'}, US10Y {f'{us10y_price:.2f}%' if us10y_price is not None else 'N/A'})"
            if scenarios:
                top_cat = scenarios[0]
                primary_driver = f"{top_cat.get('event_title', '')} ({top_cat.get('currency', '')}) · {macro_regime}"
                sc_map = top_cat.get("scenarios", {})
                hot_b = sc_map.get("hot_beat", {})
                cool_m = sc_map.get("cool_miss", {})
                if hot_b:
                    branches.append(f"HOT PRINT ({hot_b.get('trigger_condition', '')}): {hot_b.get('pair_playbook', {}).get(sym, 'Look for directional impulse')}")
                if cool_m:
                    branches.append(f"COOL PRINT ({cool_m.get('trigger_condition', '')}): {cool_m.get('pair_playbook', {}).get(sym, 'Watch for counter-reaction')}")
            else:
                branches = [
                    "IF price sweeps session liquidity and prints M5 rejection -> Enter with confirmation",
                    "IF H1 closes beyond stop boundary -> Stand down, structure invalidated"
                ]

            trap_info = p_data.get("trap_radar", {})
            trap_state = trap_info.get("trap_state", "NONE")
            if trap_state != "NONE":
                trap_warning = f"[{trap_info.get('severity', 'HIGH')}] {trap_info.get('trap_title')}: {trap_info.get('description')}"
                trap_check = f"Trap Alert: {trap_info.get('operational_guidance', {}).get('beginners')}"
            else:
                trap_warning = "Session opening spike / Judas swing often hunts pre-session stops before real expansion."
                trap_check = "Monitor liquidity boundaries for false breakout wicks."

            # 3-Tier Risk Hierarchy anchored realistically to spot / entry zone / outer DZ
            if bias == "LONG":
                agg_low = round(spot - 0.35 * atr, digits)
                agg_high = round(spot, digits)
                agg_sl = round(agg_low - 0.65 * atr, digits)
                med_low, med_high, med_sl = entry_low, entry_high, sl
                deep_base = float(l2["low"]) if (l2 and float(l2["low"]) < entry_low) else min(today_low if today_low > 0 else entry_low, entry_low - 1.0 * atr)
                prem_low = round(deep_base, digits)
                prem_high = round(deep_base + 0.5 * atr, digits)
                prem_sl = round(prem_low - 0.65 * atr, digits)
            else:
                agg_low = round(spot, digits)
                agg_high = round(spot + 0.35 * atr, digits)
                agg_sl = round(agg_high + 0.65 * atr, digits)
                med_low, med_high, med_sl = entry_low, entry_high, sl
                deep_cap = float(u2["high"]) if (u2 and float(u2["high"]) > entry_high) else max(today_high if today_high > 0 else entry_high, entry_high + 1.0 * atr)
                prem_high = round(deep_cap, digits)
                prem_low = round(deep_cap - 0.5 * atr, digits)
                prem_sl = round(prem_high + 0.65 * atr, digits)

            risk_playbook = {
                "aggressive_continuation": {
                    "tier": "HIGH_RISK",
                    "badge": "🔴 HIGH RISK · CONTINUATION / BREAKER",
                    "zone": f"{agg_low} - {agg_high}",
                    "stop_loss": agg_sl,
                    "position_sizing": "0.5x Risk (Half Lot)",
                    "note": "Immediate momentum entry. High risk of deeper adverse wick; cut fast if M5 closes through SL."
                },
                "structural_retest": {
                    "tier": "MED_RISK",
                    "badge": "🟡 MED RISK · STRUCTURAL RETEST",
                    "zone": f"{med_low} - {med_high}",
                    "stop_loss": med_sl,
                    "position_sizing": "1.0x Risk (Standard Lot)",
                    "note": f"Primary value retest at {anchor_label}. Wait for M5 engulfing/rejection confirmation before committing."
                },
                "premium_extreme": {
                    "tier": "LOW_RISK",
                    "badge": "🟢 LOW RISK · DEEP PREMIUM / DISCOUNT",
                    "zone": f"{prem_low} - {prem_high}",
                    "stop_loss": prem_sl,
                    "position_sizing": "1.0x - 1.25x Risk (Max Edge)",
                    "note": "Outer structural extreme. Maximum mathematical R:R if liquidity sweep extends past primary zone."
                }
            }

            # Top-down multi-timeframe narrative from real MT5 data
            d1_summary = (
                f"{d1_label.replace('_', ' ')} (1D {d1_reg.get('change_1d_pct', 0):+.2f}%, 5D {d1_reg.get('change_5d_pct', 0):+.2f}%, "
                f"20D {d1_reg.get('change_20d_pct', 0):+.2f}%, {d1_reg.get('streak', 'n/a')}). "
                f"H4 {h4_dir} (Gate {h4_gate}, ADX {h4_reg.get('adx', 'n/a')})."
            )
            h1_summary = (
                f"Spot {round(spot, digits)} vs H1 EMA21 {round(float(p_data.get('h1_ema21') or spot), digits)} "
                f"(RSI {round(float(p_data.get('h1_rsi') or 50), 1)}, ATR {round(atr, digits + 1)}). "
                f"Today H/L: {round(today_high, digits)} / {round(today_low, digits)}; Asia L/H: {lm.get('asia_low', '—')} / {lm.get('asia_high', '—')}."
            )
            m15_summary = f"Primary corridor anchored at {entry_low} - {entry_high} ({anchor_label}); await M5 rejection wick + engulfing confirmation."

            # Conditional counter-trend scalp setup (strictly guarded R:R)
            if bias == "SHORT":
                sc_lo = round(float(l1["low"]) if l1 else (today_low if today_low > 0 else spot - 1.0 * atr), digits)
                sc_hi = round(float(l1["high"]) if l1 else (sc_lo + 0.45 * atr), digits)
                sc_sl = round(sc_lo - 0.7 * atr, digits)
                sc_r = max(abs(sc_hi - sc_sl), 0.5 * atr)
                scalp_setup = {
                    "bias": "LONG",
                    "zone": f"{sc_lo} - {sc_hi}",
                    "condition": "ONLY IF price sweeps demand with M5 >=2x lower wick + DXY softens intraday",
                    "trigger": f"M5 lower wick rejection inside {sc_lo}-{sc_hi} + bullish close above {sc_hi}",
                    "stop_loss": sc_sl,
                    "targets": [round(sc_hi + 1.0 * sc_r, digits), round(sc_hi + 1.8 * sc_r, digits)],
                    "note": "Counter-trend scalp only (0.5x size); bank 50% at TP1." + (" Do NOT hold overnight Fri." if is_friday_ny else ""),
                }
            else:
                sc_lo = round(float(u1["low"]) if u1 else (today_high - 0.45 * atr if today_high > 0 else spot + 0.6 * atr), digits)
                sc_hi = round(float(u1["high"]) if u1 else (today_high if today_high > 0 else sc_lo + 0.45 * atr), digits)
                sc_sl = round(sc_hi + 0.7 * atr, digits)
                sc_r = max(abs(sc_sl - sc_lo), 0.5 * atr)
                scalp_setup = {
                    "bias": "SHORT",
                    "zone": f"{sc_lo} - {sc_hi}",
                    "condition": "ONLY IF price sweeps supply with M5 >=2x upper wick + DXY firms intraday",
                    "trigger": f"M5 upper wick rejection inside {sc_lo}-{sc_hi} + bearish close below {sc_lo}",
                    "stop_loss": sc_sl,
                    "targets": [round(sc_lo - 1.0 * sc_r, digits), round(sc_lo - 1.8 * sc_r, digits)],
                    "note": "Counter-trend scalp only (0.5x size); bank 50% at TP1." + (" Do NOT hold overnight Fri." if is_friday_ny else ""),
                }

            pair_discipline = discipline_hint
            if d1_label == "CRASH_BREAKDOWN":
                pair_discipline = f"CRASH/BREAKDOWN REGIME ({d1_reg.get('change_5d_pct', 0):+.2f}% 5D): Small size, no blind dip-buying. {discipline_hint}"

            entry_reason = (
                f"{bias} anchored at {entry_low}-{entry_high} ({anchor_label}; spot {round(spot, digits)}, "
                f"H1 ATR {round(atr, digits + 1)}, D1 {d1_label.replace('_',' ')}, H4 {h4_dir}). "
                f"Macro driver: {primary_driver}."
            )

            ep_rule = get_effective_entry_params(sym)
            wick_pct_rule = int(round(float(ep_rule.get("wick_rejection_ratio", 0.55)) * 100))
            disp_rule = " + displacement engulfing close" if ep_rule.get("require_displacement", True) else " + rejection close"

            setup_dict = {
                "bias": bias,
                "probability": prob,
                "entry_zone": f"{entry_low} - {entry_high}",
                "entry_reason": entry_reason,
                "trigger_confirmation": (
                    f"M5 lower wick rejection >= {wick_pct_rule}% range inside {entry_low}-{entry_high}{disp_rule}"
                    if bias == "LONG"
                    else f"M5 upper wick rejection >= {wick_pct_rule}% range inside {entry_low}-{entry_high}{disp_rule}"
                ),
                "stop_loss": sl,
                "targets": tgts,
                "trade_management": (
                    "Bank 50% at TP1 & trail to BE; close all intraday exposure before Friday 21:00 UTC close."
                    if is_friday_ny
                    else "Bank 50% risk off at TP1; trail stop to Breakeven. Avoid holding through unmanaged red folders."
                ),
                "risk_playbook": risk_playbook,
            }

            sanitized_setup = self._validate_and_sanitize_setup(
                setup_dict, sym, spot, atr, digits, pdh, pdl, today_high, today_low,
                decision_zones=p_data.get("decision_zones"),
            )

            pair_plan = {
                "executive_bottom_line": (
                    f"{bias} favored on retest of {sanitized_setup['entry_zone']} ({anchor_label}) in {d1_label.replace('_', ' ')} regime. "
                    f"Target {sanitized_setup['targets'][1] if len(sanitized_setup['targets']) > 1 else sanitized_setup['targets'][0]}."
                ),
                "top_down_structure": {
                    "daily_h4": d1_summary,
                    "h1": h1_summary,
                    "m15_m5": m15_summary,
                },
                "session_discipline_note": pair_discipline,
                "primary_setup": sanitized_setup,
                "contingency_plan": {
                    "scenario": "Sudden macro catalyst shock or DXY reversal driving breakout past structural stop loss.",
                    "probability": "30%",
                    "counter_zone": scalp_setup["zone"],
                    "invalidation": f"Hard H1 close {'below ' + str(sanitized_setup['stop_loss']) if bias == 'LONG' else 'above ' + str(sanitized_setup['stop_loss'])} voids the primary thesis.",
                    "scalp_setup": scalp_setup,
                },
                "macro_catalyst": {
                    "primary_driver": primary_driver,
                    "if_then_branches": branches,
                },
                "traps_and_checklist": {
                    "trap_warning": trap_warning,
                    "checklist": [
                        f"Set alert at entry zone boundary: {sanitized_setup['entry_zone'].split(' - ')[0]}",
                        trap_check,
                        "Check economic calendar 15 minutes before executing" + (" · No Fri overnight holds" if is_friday_ny else ""),
                    ],
                },
                "catalyst_scenarios": scenarios,
                "trap_radar": trap_info,
            }

            pair_plan = self._sync_plan_text_after_sanitize(pair_plan, sym, spot, digits, p_data)
            pair_plan = self._apply_trap_conflict_gate(pair_plan, trap_info)
            pairs_plan[sym] = pair_plan

        return {
            "session": session_title,
            "generated_at_utc": time_utc,
            "macro_regime": macro_regime,
            "global_macro_executive_thesis": executive_thesis,
            "macro_narrative_summary": macro_narrative_summary,
            "engine_mode": "SYNTHESIZED_RULE_BASED",
            "fallback_reason": fallback_reason,
            "pairs": pairs_plan,
        }

    def _store_briefing(self, briefing: Dict, curr_info: Dict[str, Any], telemetry: Optional[Dict] = None) -> Dict:
        """Normalize session/timestamp metadata, update cache, archive into BattleRecord, and mark session triggered."""
        briefing["session"] = curr_info["session_title"]
        briefing["generated_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        with self._lock:
            self.latest_briefing = briefing
        self._last_triggered_session = curr_info["session_key"]
        self._save_cache()
        if self.battle_record_engine:
            try:
                self.battle_record_engine.record_briefing(briefing, telemetry=telemetry)
            except Exception as e:
                log.warning("[SessionBriefing] BattleRecord ingestion note: %s", e)
        return briefing

    def _trigger_background_refresh(self, force: bool = False) -> bool:
        """Launch a single-flight background refresh if generation is not already running."""
        if self._is_generating:
            return False

        def _bg_run():
            try:
                self.generate_briefing(force=force)
            except Exception as e:
                log.debug("[SessionBriefing] Background refresh note: %s", e)

        threading.Thread(target=_bg_run, daemon=True, name="session-briefing-autorefresh").start()
        return True

    def generate_briefing(self, force: bool = True) -> Dict:
        """
        2-Step Institutional Pre-Session Pipeline:
          Step 1: Google-Search-grounded live macro narrative brief (with WorldNewsEngine RSS fallback).
          Step 2: Single batched Gemini structured JSON call across all 7 pairs anchored strictly to live MT5 telemetry.
        Falls back cleanly to deterministic rule-based synthesis if API key is absent or network fails,
        recording the exact fallback_reason for diagnostics.
        """
        with self._gen_lock:
            if not force and not self.is_briefing_stale():
                with self._lock:
                    return dict(self.latest_briefing)

            self._is_generating = True
            try:
                log.info("[SessionBriefing] Compiling real-time telemetry for all 7 pairs...")
                curr_info = self.get_current_session_info()
                telemetry = self._build_telemetry_payload()
                session_title = telemetry["session"]

                if not self.api_key:
                    log.info("[SessionBriefing] No GEMINI_API_KEY configured. Utilizing deterministic synthesis.")
                    briefing = self._generate_rule_based_briefing(telemetry, fallback_reason="GEMINI_API_KEY not configured")
                    return self._store_briefing(briefing, curr_info, telemetry=telemetry)

                # Step 1: Lightweight live Google Search Grounding for real-world macro narrative
                grounded_macro_brief = self._fetch_grounded_macro_brief(telemetry, curr_info, force=force)
                prompt_telemetry = self._build_prompt_telemetry(telemetry)

                grounded_block = (
                    f"\nLIVE GOOGLE-SEARCH MACRO NARRATIVE BRIEF (LAST 24H-10D CATALYSTS — USE FOR MACRO STORY ONLY, NOT PRICES):\n"
                    f"{grounded_macro_brief}\n"
                    if grounded_macro_brief
                    else "\nLIVE MACRO NEWS SOURCE: Use `world_news_headlines` and `macro_telemetry` inside the telemetry JSON below.\n"
                )

                entry_dna_block = build_entry_dna_prompt_block()
                user_prompt = f"""You are analyzing live institutional market telemetry heading into {session_title} ({telemetry['day_of_week']}, {telemetry['timestamp_utc']}).
Generate a comprehensive, tactical institutional battle-plan for EACH of the 7 pairs: EURUSD, GBPUSD, USDJPY, GBPJPY, EURJPY, EURGBP, XAUUSD.
{grounded_block}{entry_dna_block}
LIVE MT5 BROKER TELEMETRY & STRUCTURAL ZONES (100% AUTHORITATIVE FOR ALL PRICE LEVELS):
{json.dumps(prompt_telemetry, separators=(",", ":"))}

REQUIREMENTS:
1. STRICT MACRO NARRATIVE & CROSS-ASSET CONSISTENCY:
   - Synthesize the real-world macro story (Fed/FOMC rate odds, DXY multi-day trend, US10Y yields, Gold liquidation vs safe-haven flows, central banks, geopolitics) into `global_macro_executive_thesis` and `macro_narrative_summary` (3-5 crisp bullets).
   - Ensure strict cross-asset consistency across all 7 pairs (DXY, Yields, XAUUSD, EURUSD, GBPUSD, JPY crosses).
   - Respect `macro_telemetry.regime` and `d1_regime.regime_label` (e.g., in `CRASH_BREAKDOWN` or `LIQUIDITY CRUNCH`, NEVER recommend blind dip buys; frame trend-aligned setups with reduced sizing and put any counter-trend bounce strictly in `contingency_plan.scalp_setup`).
2. STRICT MT5 PRICE & DECISION-ZONE ANCHORING RULE (CRITICAL):
   - EVERY price level MUST come strictly from the MT5 telemetry above (`spot_price`, `decision_zones`, `session_landmarks`, `h1_atr`). NEVER invent prices or copy scraped web prices.
   - For LONG setups: `entry_zone` MUST sit in discount AT OR BELOW `spot_price` (anchored to `lower_dz_1` Demand or intraday low). NEVER place a LONG above spot or into `upper_dz_1` Supply.
   - For SHORT setups: `entry_zone` MUST sit in premium AT OR ABOVE `spot_price` (anchored to `upper_dz_1` Supply or intraday high/prev_close). NEVER place a SHORT below spot or into `lower_dz_1` Demand.
   - Keep `stop_loss` structurally realistic (roughly 0.8 to 2.0 x `h1_atr` past the entry zone — NEVER blindly park SL 3.5+ ATR away at yesterday's PDH/PDL on trend-collapse days).
   - Ensure `targets` are strictly ascending (LONG) or descending (SHORT) with TP1 >= 0.8R from entry edge.
3. RESPECT TRAP RADAR & SESSION DISCIPLINE:
   - If `trap_radar.severity` is `CRITICAL` or `HIGH` and opposes a directional entry at spot (e.g. Bear Trap vs SHORT), reflect that explicitly in probability, trigger discipline, and warnings.
   - Respect `session_discipline_hint` (especially Friday NY: small size, no overnight weekend holds).
4. For every pair, provide:
   - executive_bottom_line: 1-sentence crisp institutional summary.
   - top_down_structure:
     - daily_h4: multi-day regime (1D/5D/20D %, streak, H4 direction/ADX)
     - h1: intraday structure & key landmarks (Asia/London H/L, today H/L, prev_close, H1 EMA21)
     - m15_m5: immediate execution context & trigger zone
   - session_discipline_note: 1-sentence sizing & session discipline rule (e.g. crash mode / Friday close).
   - primary_setup:
     - bias: "LONG" | "SHORT" | "RANGE_FADE" | "WAIT"
     - probability: e.g. "65%"
     - entry_zone: exact MT5 price range formatted as "low - high"
     - entry_reason: MANDATORY 1-2 sentences citing exact structural anchor (DZ/landmark), multi-day regime, and real macro driver
     - trigger_confirmation: mechanical M5 trigger rule strictly matching `entry_zone`
     - stop_loss: exact price level (numeric)
     - targets: array of 3 to 4 realistic structural target prices (numeric)
     - trade_management: exact de-risking & BE instructions
     - risk_playbook: object with 3 risk profiles: aggressive_continuation, structural_retest, premium_extreme (each with badge, zone, stop_loss, position_sizing, note)
   - contingency_plan:
     - scenario: what catalyst shock would break this trade
     - probability: e.g. "30%"
     - counter_zone: counter-trend price zone
     - invalidation: hard H1 close condition that voids the primary thesis
     - scalp_setup: optional conditional counter-trend scalp object (`bias`, `zone`, `condition`, `trigger`, `stop_loss`, `targets` [2-3 numbers], `note`) with valid R:R
   - macro_catalyst:
     - primary_driver: the true dominant macro driver (e.g., Fed hike repricing + DXY squeeze, not just a low/medium calendar item)
     - if_then_branches: array of 2 actionable IF/THEN scenario statements
   - traps_and_checklist:
     - trap_warning: liquidity trap & sweep warning
     - checklist: array of 3 concise actionable items (max 3 alerts, not alert spam)

RESPOND ONLY WITH VALID JSON matching this exact structure:
{{
  "session": "{session_title}",
  "generated_at_utc": "{telemetry['timestamp_utc']}",
  "macro_regime": "{(telemetry.get('macro_telemetry') or {}).get('regime', 'BALANCED // CONSOLIDATION REGIME')}",
  "global_macro_executive_thesis": "2-3 sentences summarizing cross-asset capital flow, Fed/yields narrative, and crash/risk regime.",
  "macro_narrative_summary": [
    "Bullet 1: Fed/FOMC & Rates driver",
    "Bullet 2: DXY & Treasury yields narrative",
    "Bullet 3: Gold/Metals & FX cross-asset flow",
    "Bullet 4: Geopolitics / Energy / Risk sentiment"
  ],
  "pairs": {{
    "XAUUSD": {{ ... }},
    "EURUSD": {{ ... }},
    "GBPUSD": {{ ... }},
    "USDJPY": {{ ... }},
    "GBPJPY": {{ ... }},
    "EURJPY": {{ ... }},
    "EURGBP": {{ ... }}
  }}
}}"""

                url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
                gen_cfg: Dict[str, Any] = {
                    "responseMimeType": "application/json",
                    "maxOutputTokens": 24576,
                }
                if "2.5" in str(self.model):
                    # Cap internal thinking budget so batched 7-pair JSON synthesis finishes fast without timing out
                    gen_cfg["thinkingConfig"] = {"thinkingBudget": 0}
                body = json.dumps({
                    "contents": [{"parts": [{"text": f"{SYSTEM_PROMPT}\n\n{user_prompt}"}]}],
                    "generationConfig": gen_cfg,
                }).encode("utf-8")

                req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
                t0 = time.time()
                try:
                    log.info("[SessionBriefing] Step 2: Dispatching batched 7-pair Battle-Plan call to Gemini (%s)...", self.model)
                    with urllib.request.urlopen(req, timeout=90.0) as resp:
                        raw_resp = json.loads(resp.read().decode("utf-8"))
                        text_content = raw_resp["candidates"][0]["content"]["parts"][0]["text"]
                        parsed_data = json.loads(text_content)
                        if isinstance(parsed_data, list) and len(parsed_data) > 0:
                            parsed_data = parsed_data[0]
                        elapsed = round(time.time() - t0, 2)
                        mode_tag = "GEMINI_GROUNDED_AI" if grounded_macro_brief else "GEMINI_AI"
                        parsed_data["engine_mode"] = mode_tag
                        parsed_data["fallback_reason"] = None
                        if not parsed_data.get("macro_regime"):
                            parsed_data["macro_regime"] = (telemetry.get("macro_telemetry") or {}).get("regime")
                        log.info("[SessionBriefing] Successfully generated %s briefing in %ss!", mode_tag, elapsed)

                        # Institutional Assertion Gate: Sanitize & sync every pair from Gemini
                        for sym, p_plan in parsed_data.get("pairs", {}).items():
                            p_data = telemetry.get("pairs", {}).get(sym, {})
                            spot = float(p_data.get("spot_price") or 0.0)
                            digits = 3 if "JPY" in sym else (2 if sym == "XAUUSD" else 5)
                            pdh = float(p_data.get("pdh") or (spot * 1.004 if spot else 0.0))
                            pdl = float(p_data.get("pdl") or (spot * 0.996 if spot else 0.0))
                            today_high = float(p_data.get("today_high") or spot)
                            today_low = float(p_data.get("today_low") or spot)
                            atr = float(p_data.get("h1_atr") or (spot * 0.003 if spot else 1.0))
                            if atr <= 0:
                                atr = spot * 0.003 if spot else 1.0
                            if isinstance(p_plan, dict) and "primary_setup" in p_plan:
                                p_plan["primary_setup"] = self._validate_and_sanitize_setup(
                                    p_plan["primary_setup"], sym, spot, atr, digits, pdh, pdl, today_high, today_low,
                                    decision_zones=p_data.get("decision_zones"),
                                )
                                if not str(p_plan["primary_setup"].get("entry_reason") or "").strip():
                                    drv = (p_plan.get("macro_catalyst") or {}).get("primary_driver", "")
                                    eb = p_plan.get("executive_bottom_line", "")
                                    p_plan["primary_setup"]["entry_reason"] = (
                                        f"{eb} {('Driver: ' + drv) if drv else ''}".strip()
                                        or f"{p_plan['primary_setup'].get('bias', 'SETUP')} anchored at {p_plan['primary_setup'].get('entry_zone', '')}."
                                    )
                                # Sync text if sanitizer re-anchored + check TrapRadar conflict
                                self._sync_plan_text_after_sanitize(p_plan, sym, spot, digits, p_data)
                                self._apply_trap_conflict_gate(p_plan, p_data.get("trap_radar"))
                                # Attach full live catalyst_scenarios & trap_radar for UI tabs
                                if "catalyst_scenarios" not in p_plan and p_data.get("catalyst_scenarios"):
                                    p_plan["catalyst_scenarios"] = p_data.get("catalyst_scenarios", [])
                                if "trap_radar" not in p_plan and p_data.get("trap_radar"):
                                    p_plan["trap_radar"] = p_data.get("trap_radar", {})

                        return self._store_briefing(parsed_data, curr_info, telemetry=telemetry)
                except Exception as e:
                    err_str = f"{type(e).__name__}: {e}"
                    log.warning("[SessionBriefing] Gemini API call error (%s). Falling back to rule-based synthesis.", err_str)
                    emit_flow("up.gemini_cloud", "eng.session_briefing", STATE_FALLBACK,
                              latency_ms=(time.time() - t0) * 1000.0, items=7,
                              detail=f"Gemini Step-2 Battle-Plan failed ({err_str}) -> fallback SYNTHESIZED_RULE_BASED")
                    briefing = self._generate_rule_based_briefing(
                        telemetry, fallback_reason=err_str, grounded_macro_brief=grounded_macro_brief
                    )
                    return self._store_briefing(briefing, curr_info, telemetry=telemetry)
            finally:
                self._is_generating = False

    def _scheduler_loop(self):
        """
        Runs continuously in background.
        Checks every 30s and fires 30 minutes before Tokyo (23:30 UTC), London (07:30 UTC), New York (12:30 UTC),
        or on startup / session rollover if cached briefing is stale.
        """
        # If cache does not exist or is stale on startup during open market hours, trigger one immediate run
        startup_info = self.get_current_session_info()
        if startup_info["is_market_open"] and self.is_briefing_stale():
            time.sleep(3)  # Brief delay to let price engine seed
            try:
                log.info("[SessionBriefing] Startup check: cache is stale or missing. Generating fresh briefing...")
                self.generate_briefing(force=False)
            except Exception as e:
                log.debug("[SessionBriefing] Startup briefing generation warning: %s", e)

        while not self._stop_event.is_set():
            time.sleep(30)
            now_utc = datetime.now(timezone.utc)
            curr_info = self.get_current_session_info(now_utc)
            if not curr_info["is_market_open"]:
                continue

            hour = now_utc.hour
            minute = now_utc.minute

            # Check scheduled 30-min pre-session handoff windows or stale/rollover cache
            in_handoff_window = (
                (hour == 23 and 30 <= minute <= 35)
                or (hour == 7 and 30 <= minute <= 35)
                or (hour == 12 and 30 <= minute <= 35)
            )
            session_key = curr_info["session_key"]

            if in_handoff_window and session_key != self._last_triggered_session:
                log.info("[SessionBriefing] Triggering scheduled Pre-Session Briefing for %s...", curr_info["session_title"])
                try:
                    self.generate_briefing(force=True)
                except Exception as ex:
                    log.error("[SessionBriefing] Scheduled trigger error: %s", ex)
            elif self.is_briefing_stale(now_utc) and not self._is_generating:
                log.info("[SessionBriefing] Stale/rollover detected for %s. Refreshing briefing...", curr_info["session_title"])
                try:
                    self.generate_briefing(force=False)
                except Exception as ex:
                    log.error("[SessionBriefing] Rollover trigger error: %s", ex)

    def get_briefing(self, symbol: Optional[str] = None, auto_refresh_stale: bool = True) -> Dict:
        """Returns the latest cached briefing for the pair or full portfolio, auto-refreshing if stale."""
        curr_info = self.get_current_session_info()
        stale = self.is_briefing_stale()
        if auto_refresh_stale and stale and curr_info["is_market_open"]:
            self._trigger_background_refresh(force=False)

        meta = {
            "current_session": curr_info["session_title"],
            "current_session_name": curr_info["session_name"],
            "current_time_utc": curr_info["time_utc"],
            "is_weekend": curr_info["is_weekend"],
            "is_stale": stale,
            "is_updating": bool(self._is_generating),
            "entry_dna_stage": get_entry_dna_badge(),
        }

        with self._lock:
            if not self.latest_briefing:
                return meta if symbol else dict(meta)
            if symbol:
                pair_plan = self.latest_briefing.get("pairs", {}).get(symbol.upper(), {})
                return {
                    **meta,
                    "session": self.latest_briefing.get("session"),
                    "generated_at_utc": self.latest_briefing.get("generated_at_utc"),
                    "macro_regime": self.latest_briefing.get("macro_regime"),
                    "global_macro_executive_thesis": self.latest_briefing.get("global_macro_executive_thesis"),
                    "macro_narrative_summary": self.latest_briefing.get("macro_narrative_summary", []),
                    "engine_mode": self.latest_briefing.get("engine_mode", "GEMINI_AI"),
                    "fallback_reason": self.latest_briefing.get("fallback_reason"),
                    "plan": pair_plan,
                }
            res = dict(self.latest_briefing)
            res.update(meta)
            return res

