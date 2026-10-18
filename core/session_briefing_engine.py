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
import time
import json
import logging
import threading
import urllib.request
from datetime import datetime, timezone
from typing import Dict, Optional

import config

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIEFING_CACHE_FILE = os.path.join(ROOT, "logs", "session_briefings.json")

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "EURJPY", "EURGBP", "XAUUSD"]

SYSTEM_PROMPT = """You are the Senior Chief Market Strategist for an elite institutional prop trading desk.
Your mandate is to provide actionable, institutional-grade Pre-Session Trade Battle-Plans for professional traders.
Avoid generic commentary. Every pair requires specific price levels, quantitative entry triggers (e.g. M5 rejection wicks), hard stop losses, staged take-profit targets, and catalyst IF/THEN scenario branching.

You must respond with valid JSON adhering strictly to the requested schema for all pairs."""


class SessionBriefingEngine:
    """
    Autonomous Pre-Session Briefing Engine.
    Executes a single batched prompt for all 7 pairs 30 minutes before each major session.
    """

    def __init__(self, price_engine=None, levels_engine=None, news_engine=None, macro_engine=None):
        self.api_key = getattr(config, "GEMINI_API_KEY", "")
        self.model = getattr(config, "GEMINI_MODEL", "gemini-2.5-flash")
        self.price_engine = price_engine
        self.levels_engine = levels_engine
        self.news_engine = news_engine
        self.macro_engine = macro_engine

        self.latest_briefing: Dict = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._last_triggered_session: Optional[str] = None

        # Load existing briefing from disk cache for 0ms startup
        self._load_cache()

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
    def get_current_session_info() -> Dict[str, str]:
        """Calculates current UTC/MYT time and identifies active/upcoming session."""
        now_utc = datetime.now(timezone.utc)
        hour = now_utc.hour
        minute = now_utc.minute
        time_dec = hour + minute / 60.0

        # Session boundaries:
        # Tokyo:    23:30 UTC -> 07:30 UTC
        # London:   07:30 UTC -> 12:30 UTC
        # New York: 12:30 UTC -> 23:30 UTC
        if 23.5 <= time_dec or time_dec < 7.5:
            session_name = "TOKYO_ASIAN"
            session_title = "Tokyo / Asian Session"
        elif 7.5 <= time_dec < 12.5:
            session_name = "LONDON_EUROPEAN"
            session_title = "London / European Session"
        else:
            session_name = "NEW_YORK_US"
            session_title = "New York / US Session"

        return {
            "session_name": session_name,
            "session_title": session_title,
            "time_utc": now_utc.strftime("%Y-%m-%d %H:%M UTC"),
        }

    def _build_telemetry_payload(self) -> Dict:
        """Assembles real-time telemetry from all sub-engines."""
        session_info = self.get_current_session_info()
        payload = {
            "session": session_info["session_title"],
            "timestamp_utc": session_info["time_utc"],
            "macro_telemetry": {},
            "pairs": {}
        }

        # 1. Macro Telemetry
        if self.macro_engine:
            try:
                snap = self.macro_engine.get_snapshot() if hasattr(self.macro_engine, "get_snapshot") else {}
                tips = self.macro_engine.get_tips_real_yield() if hasattr(self.macro_engine, "get_tips_real_yield") else {}
                payload["macro_telemetry"] = {
                    "dxy": snap.get("DXY", {}),
                    "us10y": snap.get("US10Y", {}),
                    "vix": snap.get("VIX", {}),
                    "spx": snap.get("SPX", {}),
                    "real_yield_10y_tips": tips.get("real_yield_pct", 2.78),
                    "real_yield_pressure": tips.get("yield_pressure", "STEADY"),
                }
            except Exception as e:
                log.debug("[SessionBriefing] Macro fetch note: %s", e)

        # 2. Per-Pair Telemetry
        for sym in PAIRS:
            pair_data = {"symbol": sym}
            if self.price_engine:
                p_info = self.price_engine.get_price(sym) or {}
                pair_data["spot_price"] = p_info.get("price")
                pair_data["bid"] = p_info.get("bid")
                pair_data["ask"] = p_info.get("ask")
                pair_data["spread_pts"] = p_info.get("spread")

                # Get latest candle closes and indicators
                df_h1 = self.price_engine.get_rates(sym, "H1", 20)
                if not df_h1.empty:
                    pair_data["h1_close"] = float(df_h1["close"].iloc[-1])
                    pair_data["h1_atr"] = float(df_h1["ATR"].iloc[-1]) if "ATR" in df_h1 else None
                    pair_data["h1_rsi"] = float(df_h1["RSI"].iloc[-1]) if "RSI" in df_h1 else None
                    pair_data["h1_ema21"] = float(df_h1["EMA21"].iloc[-1]) if "EMA21" in df_h1 else None

            # Levels
            if self.levels_engine and self.price_engine:
                try:
                    df_d1 = self.price_engine.get_rates(sym, "D1", 10)
                    df_w1 = self.price_engine.get_rates(sym, "W1", 5)
                    df_h1 = self.price_engine.get_rates(sym, "H1", 20)
                    if not df_d1.empty and not df_h1.empty:
                        lv = self.levels_engine.get_key_levels(df_d1, df_w1, df_h1)
                        pair_data["pdh"] = lv.get("daily_high")
                        pair_data["pdl"] = lv.get("daily_low")
                        pair_data["weekly_open"] = lv.get("weekly_open")
                        pair_data["today_high"] = lv.get("today_high")
                        pair_data["today_low"] = lv.get("today_low")
                        pair_data["pivot"] = lv.get("pivot")
                except Exception as e:
                    log.debug("[SessionBriefing] Levels error for %s: %s", sym, e)

            # News upcoming catalysts
            if self.news_engine:
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

    def generate_briefing(self) -> Dict:
        """
        Executes a single batched prompt to Gemini 2.5 Flash for all 7 pairs.
        Saves output to disk cache and updates state.
        """
        if not self.api_key:
            log.warning("[SessionBriefing] No GEMINI_API_KEY found in config. Cannot generate AI briefing.")
            return {}

        log.info("[SessionBriefing] Compiling real-time telemetry for all 7 pairs...")
        telemetry = self._build_telemetry_payload()
        session_title = telemetry["session"]

        user_prompt = f"""You are analyzing live institutional market telemetry 30 minutes before the upcoming {session_title}.
Generate a comprehensive, tactical 5-block battle-plan for EACH of the 7 pairs: EURUSD, GBPUSD, USDJPY, GBPJPY, EURJPY, EURGBP, XAUUSD.

TELEMETRY DATA (100% REAL-TIME LIVE DATA):
{json.dumps(telemetry, indent=2)}

REQUIREMENTS:
1. Ensure strict macroeconomic consistency across all 7 pairs. (If DXY is weak, Gold and EURUSD must align logically).
2. For every pair, provide:
   - executive_bottom_line: 1-sentence crisp summary that a beginner trader can understand instantly.
   - primary_setup:
     - bias: "LONG" | "SHORT" | "RANGE_FADE" | "WAIT"
     - probability: e.g. "65%"
     - entry_zone: exact price range formatted as "$low - $high" or "low - high"
     - trigger_confirmation: mechanical trigger rule (e.g. "M5 lower wick rejection >= 2x body at [level] + bullish engulf close")
     - stop_loss: exact price level (beneath structural invalidation)
     - targets: array of 3 to 5 realistic target prices (TP1, TP2, TP3, TP4)
     - trade_management: exact de-risking instructions (e.g. move to BE at TP1, reduce risk 15m before news)
   - contingency_plan:
     - scenario: what catalyst shock would break this trade
     - probability: e.g. "35%"
     - counter_zone: counter-trend entry price zone
     - invalidation: hard H1 close condition that voids the primary thesis
   - macro_catalyst:
     - primary_driver: the dominant macro transmission channel
     - if_then_branches: array of 2 actionable IF/THEN scenario statements for upcoming news
   - traps_and_checklist:
     - trap_warning: session liquidity traps (e.g. London Judas swing, spread spikes)
     - checklist: array of 3 actionable items (alerts to set, time to de-risk)

RESPOND ONLY WITH VALID JSON matching this exact structure:
{{
  "session": "{session_title}",
  "generated_at_utc": "{telemetry['timestamp_utc']}",
  "global_macro_executive_thesis": "2-3 sentences summarizing the cross-asset institutional capital flow across DXY, Yields, Equities and Commodities for this session.",
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
        body = json.dumps({
            "contents": [{"parts": [{"text": f"{SYSTEM_PROMPT}\n\n{user_prompt}"}]}],
            "generationConfig": {"responseMimeType": "application/json"}
        }).encode("utf-8")

        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        t0 = time.time()
        try:
            log.info("[SessionBriefing] Dispatching single batched call to Gemini (%s)...", self.model)
            with urllib.request.urlopen(req, timeout=120.0) as resp:
                raw_resp = json.loads(resp.read().decode("utf-8"))
                text_content = raw_resp["candidates"][0]["content"]["parts"][0]["text"]
                parsed_data = json.loads(text_content)
                elapsed = round(time.time() - t0, 2)
                log.info("[SessionBriefing] Successfully generated institutional briefing in %ss!", elapsed)

                with self._lock:
                    self.latest_briefing = parsed_data

                self._save_cache()
                return parsed_data
        except Exception as e:
            log.error("[SessionBriefing] Gemini API call error: %s", e)
            return {}

    def _scheduler_loop(self):
        """
        Runs continuously in background.
        Checks every 30s and fires 30 minutes before Tokyo (23:30 UTC), London (07:30 UTC), New York (12:30 UTC).
        """
        # If cache does not exist on startup, trigger one immediate run
        if not self.latest_briefing and self.api_key:
            time.sleep(3)  # Brief delay to let price engine seed
            try:
                self.generate_briefing()
            except Exception as e:
                log.debug("[SessionBriefing] Startup briefing generation warning: %s", e)

        while not self._stop_event.is_set():
            time.sleep(30)
            now_utc = datetime.now(timezone.utc)
            hour = now_utc.hour
            minute = now_utc.minute

            # Check for trigger windows:
            # 1. Tokyo: 23:30 - 23:35 UTC (07:30 MYT)
            # 2. London: 07:30 - 07:35 UTC (15:30 MYT)
            # 3. New York: 12:30 - 12:35 UTC (20:30 MYT)
            target_session = None
            if hour == 23 and 30 <= minute <= 35:
                target_session = "TOKYO"
            elif hour == 7 and 30 <= minute <= 35:
                target_session = "LONDON"
            elif hour == 12 and 30 <= minute <= 35:
                target_session = "NEW_YORK"

            if target_session and target_session != self._last_triggered_session:
                # Monday morning Asia through Friday evening NY
                if now_utc.weekday() < 5 or (now_utc.weekday() == 6 and hour >= 21):
                    log.info("[SessionBriefing] Triggering scheduled Pre-Session Briefing for %s session...", target_session)
                    try:
                        self.generate_briefing()
                        self._last_triggered_session = target_session
                    except Exception as ex:
                        log.error("[SessionBriefing] Scheduled trigger error: %s", ex)

    def get_briefing(self, symbol: Optional[str] = None) -> Dict:
        """Returns the latest cached briefing for the pair or full portfolio."""
        with self._lock:
            if not self.latest_briefing:
                return {}
            if symbol:
                pair_plan = self.latest_briefing.get("pairs", {}).get(symbol.upper(), {})
                return {
                    "session": self.latest_briefing.get("session"),
                    "generated_at_utc": self.latest_briefing.get("generated_at_utc"),
                    "global_macro_executive_thesis": self.latest_briefing.get("global_macro_executive_thesis"),
                    "plan": pair_plan
                }
            return dict(self.latest_briefing)
