# Decoupled NewsEngine - Autonomous Macroeconomic Calendar
#
# Primary Feed: nfs.faireconomy.media (ForexFactory) for weekly calendar, countdowns, forecasts, previous
# Post-Release Actuals: Tradays API (/widget/value) for actual values polled at T+60s
# Caching: Daily at 6:00 AM UTC (or on startup if stale) to avoid Cloudflare rate limiting

import os
import json
import time
import logging
import threading
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "decoupled_calendar_cache.json")

CURRENCY_MAP = {
    "EURUSD": ["EUR", "USD"],
    "GBPUSD": ["GBP", "USD"],
    "USDJPY": ["USD", "JPY"],
    "GBPJPY": ["GBP", "JPY"],
    "EURJPY": ["EUR", "JPY"],
    "EURGBP": ["EUR", "GBP"],
    "XAUUSD": ["XAU", "USD"],
}

WAITING_SOURCE = "waiting_for_calendar"
CALENDAR_SOURCE = "decoupled_faireconomy_tradays"


def _parse_event_time(time_str: str) -> Optional[datetime]:
    if not time_str:
        return None
    s = str(time_str).strip()
    if s.endswith("Z") or "+00:00" in s:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            pass
    formats = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y.%m.%d %H:%M:%S",
        "%Y.%m.%d %H:%M",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


class NewsEngine:
    """
    Autonomous Decoupled News Engine.
    Requires no MT5 or EA connection.
    """

    def __init__(self, bridge=None, cache_file: str = CACHE_FILE):
        self.bridge = bridge
        self.cache_file = cache_file
        self.events: List[Dict] = []
        self.last_fetch_time: Optional[datetime] = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

        # Load existing cache if valid
        self._load_cache()

        # Initial sync if cache missing or stale
        try:
            self.sync_calendar(force=False)
        except Exception as e:
            log.warning("[NewsEngine] Initial sync failed: %s. Using cached if available.", e)

        # Start background watcher daemon for T+60s actuals and daily 6am sync
        self._bg_thread = threading.Thread(target=self._background_loop, daemon=True)
        self._bg_thread.start()

    def _load_cache(self) -> bool:
        if not os.path.exists(self.cache_file):
            return False
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                with self._lock:
                    self.events = data.get("events", [])
                    last_str = data.get("last_fetch_time")
                    if last_str:
                        self.last_fetch_time = datetime.fromisoformat(last_str)
                return bool(self.events)
        except Exception as e:
            log.error("[NewsEngine] Error loading cache: %s", e)
            return False

    def _save_cache(self):
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump({
                    "last_fetch_time": self.last_fetch_time.isoformat() if self.last_fetch_time else None,
                    "count": len(self.events),
                    "events": self.events,
                }, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log.error("[NewsEngine] Error saving cache: %s", e)

    def should_refresh_daily(self) -> bool:
        if not self.events or not self.last_fetch_time:
            return True
        now = datetime.now(timezone.utc)
        today_6am = now.replace(hour=6, minute=0, second=0, microsecond=0)
        if now < today_6am:
            yesterday_6am = today_6am - timedelta(days=1)
            return self.last_fetch_time < yesterday_6am
        else:
            return self.last_fetch_time < today_6am

    def fetch_forex_factory(self) -> List[Dict]:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data

    def fetch_tradays_content(self, date_from: datetime, date_to: datetime) -> List[Dict]:
        from_str = date_from.strftime("%Y-%m-%dT%H:%M:%S")
        to_str = date_to.strftime("%Y-%m-%dT%H:%M:%S")
        url = f"https://www.tradays.com/en/economic-calendar/widget/content?date_mode=0&from={from_str}&to={to_str}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://www.tradays.com/en/economic-calendar/widget?mode=1"
            }
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data

    def sync_calendar(self, force: bool = False) -> Dict:
        if not force and not self.should_refresh_daily():
            return {"status": "cached", "count": len(self.events)}

        log.info("[NewsEngine] Refreshing weekly macroeconomic calendar...")
        ff_raw = self.fetch_forex_factory()

        now = datetime.now(timezone.utc)
        date_from = now - timedelta(days=2)
        date_to = now + timedelta(days=7)

        tradays_raw = []
        try:
            tradays_raw = self.fetch_tradays_content(date_from, date_to)
        except Exception as e:
            log.warning("[NewsEngine] Tradays content fetch warning: %s", e)

        # Index Tradays by (currency, minute)
        td_index = {}
        for td in tradays_raw:
            ccy = (td.get("CurrencyCode") or "").upper()
            rel_ms = td.get("ReleaseDate")
            if ccy and rel_ms:
                td_dt = datetime.fromtimestamp(rel_ms / 1000.0, timezone.utc)
                key = (ccy, td_dt.strftime("%Y-%m-%d %H:%M"))
                td_index.setdefault(key, []).append(td)

        # Preserve existing actuals if already fetched
        existing_actuals = {}
        with self._lock:
            for ev in self.events:
                eid = ev.get("tradays_id")
                if eid and ev.get("actual"):
                    existing_actuals[eid] = (ev.get("actual"), ev.get("impact_direction", 0))

        normalized_events = []
        for item in ff_raw:
            date_str = item.get("date", "")
            try:
                ev_dt = datetime.fromisoformat(date_str).astimezone(timezone.utc)
            except Exception:
                continue

            impact_str = (item.get("impact") or "").upper()
            priority = 3 if impact_str == "HIGH" else (2 if impact_str in ("MEDIUM", "MED") else 1)
            ccy = (item.get("country") or "").upper()
            title = item.get("title", "")

            # Match Tradays ID
            td_id = None
            key = (ccy, ev_dt.strftime("%Y-%m-%d %H:%M"))
            candidates = td_index.get(key, [])
            if candidates:
                if len(candidates) == 1:
                    td_id = candidates[0].get("Id")
                else:
                    for c in candidates:
                        c_name = c.get("EventName", "").lower()
                        if any(w in c_name for w in title.lower().split() if len(w) > 3):
                            td_id = c.get("Id")
                            break
                    if not td_id:
                        td_id = candidates[0].get("Id")

            actual_val = ""
            impact_dir = 0
            if td_id and td_id in existing_actuals:
                actual_val, impact_dir = existing_actuals[td_id]

            normalized_events.append({
                "time": ev_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "timestamp": int(ev_dt.timestamp()),
                "event": title,
                "currency": ccy,
                "impact": impact_str,
                "importance": impact_str,
                "priority": priority,
                "forecast": item.get("forecast") or "",
                "previous": item.get("previous") or "",
                "actual": actual_val,
                "impact_direction": impact_dir,
                "tradays_id": td_id,
                "actual_checked_count": 0,
            })

        with self._lock:
            self.events = normalized_events
            self.last_fetch_time = datetime.now(timezone.utc)
            self._save_cache()

        log.info("[NewsEngine] Calendar sync complete: %d events cached.", len(self.events))
        return {"status": "refreshed", "count": len(self.events)}

    def fetch_actual_values(self, event_ids: List[int]) -> Dict[int, Dict]:
        if not event_ids:
            return {}
        query_parts = [f"ids={eid}" for eid in event_ids]
        url = "https://www.tradays.com/en/economic-calendar/widget/value?" + "&".join(query_parts)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://www.tradays.com/en/economic-calendar/widget?mode=1"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                result = {}
                for item in data:
                    eid = item.get("Id")
                    if eid:
                        result[eid] = item
                return result
        except Exception as e:
            log.debug("[NewsEngine] Error fetching actuals: %s", e)
            return {}

    def update_actuals_for_due_events(self) -> int:
        now_ts = int(datetime.now(timezone.utc).timestamp())
        with self._lock:
            due = [
                ev for ev in self.events
                if not ev.get("actual")
                and ev.get("tradays_id")
                and (ev.get("timestamp", 0) + 60 <= now_ts <= ev.get("timestamp", 0) + 10800)
                and ev.get("actual_checked_count", 0) < 3
            ]

        if not due:
            return 0

        ids = [ev["tradays_id"] for ev in due]
        actuals = self.fetch_actual_values(ids)

        updated_count = 0
        with self._lock:
            for ev in due:
                ev["actual_checked_count"] = ev.get("actual_checked_count", 0) + 1
                tid = ev["tradays_id"]
                if tid in actuals:
                    val = actuals[tid].get("ActualValue")
                    if val:
                        ev["actual"] = val
                        ev["impact_direction"] = actuals[tid].get("ImpactDirection", 0)
                        updated_count += 1
            if updated_count > 0:
                self._save_cache()

        return updated_count

    def _background_loop(self):
        """Background daemon: checks for released news every 30s and runs daily 6am sync."""
        while not self._stop_event.is_set():
            try:
                # 1. Daily 6am sync check
                if self.should_refresh_daily():
                    self.sync_calendar(force=True)

                # 2. Post-release watcher (T+60s)
                self.update_actuals_for_due_events()
            except Exception as e:
                log.debug("[NewsEngine] Background loop error: %s", e)

            # Sleep 30 seconds
            self._stop_event.wait(30)

    # -------------------------------------------------------------
    # Tomoko Gate API (100% compatible with existing callers)
    # -------------------------------------------------------------

    def is_available(self) -> bool:
        with self._lock:
            return bool(self.events)

    def is_news_clean(self, symbol: str, minutes_before: int = 60, minutes_after: int = 30) -> Tuple[bool, List[Dict]]:
        currencies = CURRENCY_MAP.get(symbol, [])
        if not currencies:
            return True, []

        now_ts = int(datetime.now(timezone.utc).timestamp())
        before_sec = minutes_before * 60
        after_sec = minutes_after * 60

        blocking = []
        with self._lock:
            for ev in self.events:
                if ev.get("impact") != "HIGH":
                    continue
                if ev.get("currency") not in currencies:
                    continue
                ev_ts = ev.get("timestamp", 0)
                if (ev_ts - before_sec) <= now_ts <= (ev_ts + after_sec):
                    blocking.append(ev)

        return (len(blocking) == 0), blocking

    def get_upcoming_events(self, symbol: str, hours_ahead: int = 168) -> List[Dict]:
        currencies = None if symbol == "ALL" else CURRENCY_MAP.get(symbol, [])
        now_ts = int(datetime.now(timezone.utc).timestamp())
        max_ts = now_ts + (hours_ahead * 3600)

        with self._lock:
            upcoming = [
                {**ev, "_rel_min": int((ev.get("timestamp", 0) - now_ts) / 60)}
                for ev in self.events
                if (not currencies or ev.get("currency") in currencies)
                and (now_ts - 3600 <= ev.get("timestamp", 0) <= max_ts)
            ]

        return sorted(upcoming, key=lambda x: x.get("timestamp", 0))

    def get_global_events(self, hours_ahead: int = 168) -> Dict:
        events = self.get_upcoming_events("ALL", hours_ahead=hours_ahead)
        last_str = self.last_fetch_time.strftime("%Y.%m.%d %H:%M") if self.last_fetch_time else ""
        return {
            "events": events,
            "count": len(events),
            "source": CALENDAR_SOURCE,
            "status": "ok",
            "last_update": last_str,
        }

    def get_news_for_symbol(self, symbol: str, hours_ahead: int = 168) -> Dict:
        total = len(self.events)
        is_clean, blocking = self.is_news_clean(symbol)
        upcoming = self.get_upcoming_events(symbol, hours_ahead=hours_ahead)
        risk = "RISK-OFF" if blocking else "RISK-ON"
        last_str = self.last_fetch_time.strftime("%Y.%m.%d %H:%M") if self.last_fetch_time else ""

        return {
            "is_clean": is_clean,
            "blocking": blocking,
            "upcoming_events": upcoming,
            "source": CALENDAR_SOURCE,
            "total_events": total,
            "count": total,
            "risk": risk,
            "status": "ok",
            "last_update": last_str,
        }

    def get_news_status(self, symbol: str) -> Dict:
        is_clean, blocking = self.is_news_clean(symbol)
        now_ts = int(datetime.now(timezone.utc).timestamp())
        next_events = []
        for ev in blocking:
            ev_ts = ev.get("timestamp", 0)
            countdown = int((ev_ts - now_ts) / 60)
            next_events.append({
                "time": ev.get("time"),
                "event": ev.get("event"),
                "impact": ev.get("impact"),
                "importance": ev.get("importance"),
                "priority": ev.get("priority"),
                "currency": ev.get("currency"),
                "forecast": ev.get("forecast"),
                "previous": ev.get("previous"),
                "actual": ev.get("actual"),
                "impact_direction": ev.get("impact_direction", 0),
                "countdown_min": countdown,
            })
        return {
            "is_clean": is_clean,
            "next_events": next_events,
            "news_score": 0 if not is_clean else 100,
            "source": CALENDAR_SOURCE,
            "status": "ok",
        }
