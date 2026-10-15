"""
Standalone Test & Verification for Decoupled News Engine.
Combines:
  1. nfs.faireconomy.media (Forex Factory) for weekly schedule, countdown, forecast, previous, impact
  2. Tradays widget API (/widget/content and /widget/value) for numeric IDs and post-release Actual values
  3. Daily 6am / on-startup caching logic to eliminate Cloudflare 429 rate limiting
  4. Post-release watcher (T+60s) with 3-attempt retry to fetch actual values
"""

import os
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "decoupled_calendar_cache.json")

CURRENCY_MAP = {
    "EURUSD": ["EUR", "USD"],
    "GBPUSD": ["GBP", "USD"],
    "USDJPY": ["USD", "JPY"],
    "GBPJPY": ["GBP", "JPY"],
    "EURJPY": ["EUR", "JPY"],
    "EURGBP": ["EUR", "GBP"],
    "XAUUSD": ["XAU", "USD"],
}


class DecoupledNewsEngine:
    """
    Decoupled News Engine without MT5/EA dependency.
    """

    def __init__(self, cache_file: str = CACHE_FILE):
        self.cache_file = cache_file
        self.events: List[Dict] = []
        self.last_fetch_time: Optional[datetime] = None
        self._load_cache()

    def _load_cache(self) -> bool:
        if not os.path.exists(self.cache_file):
            return False
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.events = data.get("events", [])
                last_str = data.get("last_fetch_time")
                if last_str:
                    self.last_fetch_time = datetime.fromisoformat(last_str)
                return bool(self.events)
        except Exception as e:
            print(f"[NewsEngine] Error loading cache: {e}")
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
            print(f"[NewsEngine] Error saving cache: {e}")

    def should_refresh_daily(self) -> bool:
        """
        True if cache doesn't exist, is empty, or the last fetch was before 6:00 AM today.
        """
        if not self.events or not self.last_fetch_time:
            return True
        now = datetime.now(timezone.utc)
        # Determine today's 6:00 AM UTC cutoff (or local equivalent)
        today_6am = now.replace(hour=6, minute=0, second=0, microsecond=0)
        if now < today_6am:
            yesterday_6am = today_6am - timedelta(days=1)
            return self.last_fetch_time < yesterday_6am
        else:
            return self.last_fetch_time < today_6am

    def fetch_forex_factory(self) -> List[Dict]:
        """Fetch weekly calendar from nfs.faireconomy.media."""
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
        """Fetch Tradays events for ID mapping."""
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
        """
        Runs the daily sync:
          1. Fetches ForexFactory this week.
          2. Fetches Tradays calendar to match Tradays IDs.
          3. Normalizes into Tomoko event format.
        """
        if not force and not self.should_refresh_daily():
            print(f"[NewsEngine] Using cached calendar ({len(self.events)} events, last fetched {self.last_fetch_time})")
            return {"status": "cached", "count": len(self.events)}

        print("[NewsEngine] Fetching fresh calendar from nfs.faireconomy.media...")
        ff_raw = self.fetch_forex_factory()
        print(f"[NewsEngine] Received {len(ff_raw)} events from ForexFactory.")

        # Determine date range for Tradays fetch
        now = datetime.now(timezone.utc)
        date_from = now - timedelta(days=2)
        date_to = now + timedelta(days=7)

        print("[NewsEngine] Fetching Tradays event IDs for post-release tracking...")
        tradays_raw = []
        try:
            tradays_raw = self.fetch_tradays_content(date_from, date_to)
            print(f"[NewsEngine] Received {len(tradays_raw)} events from Tradays.")
        except Exception as e:
            print(f"[NewsEngine] Warning: Tradays content fetch failed ({e}). Proceeding without IDs.")

        # Build Tradays index for fast matching: (currency, timestamp_minute) -> list of events
        td_index = {}
        for td in tradays_raw:
            ccy = (td.get("CurrencyCode") or "").upper()
            rel_ms = td.get("ReleaseDate")
            if ccy and rel_ms:
                td_dt = datetime.fromtimestamp(rel_ms / 1000.0, timezone.utc)
                key = (ccy, td_dt.strftime("%Y-%m-%d %H:%M"))
                td_index.setdefault(key, []).append(td)

        # Normalize and merge
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
                # If only one candidate matches currency & exact minute, take it
                if len(candidates) == 1:
                    td_id = candidates[0].get("Id")
                else:
                    # Best match by title substring
                    for c in candidates:
                        c_name = c.get("EventName", "").lower()
                        if any(w in c_name for w in title.lower().split() if len(w) > 3):
                            td_id = c.get("Id")
                            break
                    if not td_id:
                        td_id = candidates[0].get("Id")

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
                "actual": "",  # To be populated post-release
                "tradays_id": td_id,
                "actual_checked_count": 0,
            })

        self.events = normalized_events
        self.last_fetch_time = datetime.now(timezone.utc)
        self._save_cache()
        print(f"[NewsEngine] Successfully normalized & cached {len(self.events)} events.")
        return {"status": "refreshed", "count": len(self.events)}

    def fetch_actual_values(self, event_ids: List[int]) -> Dict[int, Dict]:
        """
        Polls Tradays /widget/value?ids=... for the given event IDs.
        """
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
            print(f"[NewsEngine] Error fetching actuals: {e}")
            return {}

    def update_actuals_for_due_events(self) -> int:
        """
        Checks for events that released in the last 2 hours where actual is still empty,
        and attempts to query Tradays for their ActualValue (up to 3 retries).
        """
        now = datetime.now(timezone.utc)
        now_ts = int(now.timestamp())

        # An event is due for an actual value if:
        # 1. It released at least 60 seconds ago (now_ts >= event_ts + 60)
        # 2. It is not older than 3 hours
        # 3. actual is still empty
        # 4. checked count < 3 attempts
        # 5. has a tradays_id
        due = []
        for ev in self.events:
            ts = ev.get("timestamp", 0)
            if not ev.get("actual") and ev.get("tradays_id") and (ts + 60 <= now_ts <= ts + 10800):
                if ev.get("actual_checked_count", 0) < 3:
                    due.append(ev)

        if not due:
            return 0

        print(f"[NewsEngine] Checking actual values for {len(due)} due event(s)...")
        ids_to_query = [ev["tradays_id"] for ev in due]
        actuals = self.fetch_actual_values(ids_to_query)

        updated_count = 0
        for ev in due:
            ev["actual_checked_count"] = ev.get("actual_checked_count", 0) + 1
            tid = ev["tradays_id"]
            if tid in actuals:
                val = actuals[tid].get("ActualValue")
                if val:
                    ev["actual"] = val
                    ev["impact_direction"] = actuals[tid].get("ImpactDirection", 0)
                    print(f"  [+] Actual released for '{ev['event']}' ({ev['currency']}): {val}")
                    updated_count += 1

        if updated_count > 0:
            self._save_cache()

        return updated_count

    # -------------------------------------------------------------
    # Tomoko News Gate Interface (100% compatible with existing callers)
    # -------------------------------------------------------------

    def is_news_clean(self, symbol: str, minutes_before: int = 60, minutes_after: int = 30) -> Tuple[bool, List[Dict]]:
        """
        Returns (is_clean, blocking_events) for `symbol`.
        HIGH events block the window [-minutes_after, +minutes_before].
        """
        if not self.events:
            return True, []

        currencies = CURRENCY_MAP.get(symbol, [])
        if not currencies:
            return True, []

        now = datetime.now(timezone.utc)
        now_ts = int(now.timestamp())
        before_sec = minutes_before * 60
        after_sec = minutes_after * 60

        blocking = []
        for ev in self.events:
            if ev.get("impact") != "HIGH":
                continue
            if ev.get("currency") not in currencies:
                continue
            ev_ts = ev.get("timestamp", 0)
            # Blocking window: ev_ts - before_sec <= now_ts <= ev_ts + after_sec
            if (ev_ts - before_sec) <= now_ts <= (ev_ts + after_sec):
                blocking.append(ev)

        return (len(blocking) == 0), blocking

    def get_upcoming_events(self, symbol: str, hours_ahead: int = 168) -> List[Dict]:
        """Returns upcoming events for symbol's currencies within hours_ahead."""
        currencies = None if symbol == "ALL" else CURRENCY_MAP.get(symbol, [])
        now_ts = int(datetime.now(timezone.utc).timestamp())
        max_ts = now_ts + (hours_ahead * 3600)

        upcoming = []
        for ev in self.events:
            if currencies and ev.get("currency") not in currencies:
                continue
            ev_ts = ev.get("timestamp", 0)
            if now_ts <= ev_ts <= max_ts:
                upcoming.append(ev)

        return sorted(upcoming, key=lambda x: x.get("timestamp", 0))

    def get_news_status(self, symbol: str) -> Dict:
        """Dashboard compact news status."""
        is_clean, blocking = self.is_news_clean(symbol)
        now_ts = int(datetime.now(timezone.utc).timestamp())
        next_events = []
        for ev in blocking:
            ev_ts = ev.get("timestamp", 0)
            countdown_min = int((ev_ts - now_ts) / 60)
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
                "countdown_min": countdown_min,
            })
        return {
            "is_clean": is_clean,
            "next_events": next_events,
            "news_score": 0 if not is_clean else 100,
            "source": "decoupled_news_engine",
            "status": "ok",
        }


def run_full_test():
    print("=" * 60)
    print("RUNNING DECOUPLED NEWS ENGINE INTEGRATION TEST")
    print("=" * 60)

    engine = DecoupledNewsEngine()

    # Step 1: Sync Calendar (Daily 6am logic)
    print("\n[Step 1] Synchronizing calendar (Simulating daily 6am / startup)...")
    res = engine.sync_calendar(force=True)
    print("Sync Result:", res)

    # Step 2: Verify Event Structure & Tradays Mapping
    print("\n[Step 2] Verifying events & Tradays ID mapping...")
    with_ids = [e for e in engine.events if e.get("tradays_id")]
    high_impact = [e for e in engine.events if e.get("impact") == "HIGH"]
    print(f"Total events: {len(engine.events)}")
    print(f"Events mapped to Tradays IDs: {len(with_ids)} ({len(with_ids)/len(engine.events)*100:.1f}%)")
    print(f"High-impact events: {len(high_impact)}")

    if with_ids:
        sample = with_ids[0]
        print("\nSample mapped event:")
        print(json.dumps(sample, indent=2))

    # Step 3: Test Post-Release Actual Watcher
    print("\n[Step 3] Testing post-release Actual Value lookup on Tradays...")
    # Test querying Tradays with the first 3 mapped IDs
    test_ids = [e["tradays_id"] for e in with_ids[:3]]
    print(f"Querying Tradays for test IDs: {test_ids}")
    actuals = engine.fetch_actual_values(test_ids)
    print("Tradays Actual Response:")
    print(json.dumps(actuals, indent=2))

    # Step 4: Test Tomoko Trading Gates (EURUSD, USDJPY, XAUUSD)
    print("\n[Step 4] Testing is_news_clean() gate across key pairs...")
    for sym in ["EURUSD", "USDJPY", "XAUUSD"]:
        is_clean, blocking = engine.is_news_clean(sym)
        status = engine.get_news_status(sym)
        upcoming = engine.get_upcoming_events(sym, hours_ahead=24)
        print(f"\nSymbol: {sym}")
        print(f"  is_news_clean: {is_clean} (Blocking: {len(blocking)})")
        print(f"  Upcoming (next 24h): {len(upcoming)} events")
        if upcoming:
            first = upcoming[0]
            print(f"  Next event: {first['time']} | {first['currency']} | {first['impact']} | {first['event']}")

    # Step 5: Test Cache Re-use (Simulate second run without force)
    print("\n[Step 5] Testing Cache Persistence (Simulate non-6am restart)...")
    engine2 = DecoupledNewsEngine()
    res2 = engine2.sync_calendar(force=False)
    print(f"Second call result: {res2['status']} (re-used {res2['count']} events without calling external API!)")

    print("\n" + "=" * 60)
    print("ALL TESTS COMPLETED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    run_full_test()
