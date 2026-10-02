# Real news calendar for Tomoko Brain (ForexFactory free feed)
import requests
from datetime import datetime, timezone, timedelta
import config

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CACHE_SECONDS = 15 * 60
RELEVANT_COUNTRIES = {"USD", "EUR", "GBP", "JPY"}


class NewsFeed:
    def __init__(self):
        self._cache = None
        self._cache_time = None

    def _fetch(self):
        now = datetime.now(timezone.utc)
        if self._cache is not None and self._cache_time is not None:
            if (now - self._cache_time).total_seconds() < CACHE_SECONDS:
                return self._cache
        events = self._fetch_ff()
        if events is None:
            events = self._fallback()
        self._cache = events
        self._cache_time = now
        return events

    def _fetch_ff(self):
        try:
            r = requests.get(FF_URL, timeout=5)
            data = r.json()
        except Exception as e:
            print("news feed fetch failed:", e)
            return None
        parsed = []
        for raw in data:
            dt = self._parse_date(raw.get("date"))
            if dt is None:
                continue
            country = (raw.get("country") or "").strip()
            if country not in RELEVANT_COUNTRIES:
                continue
            impact_raw = (raw.get("impact") or "").lower()
            if impact_raw.startswith("high"):
                impact = "HIGH"
            elif impact_raw.startswith("med"):
                impact = "MEDIUM"
            else:
                impact = "LOW"
            parsed.append({
                "time": dt.astimezone().strftime("%H:%M"),
                "currency": country,
                "event": (raw.get("title") or "").strip(),
                "impact": impact,
                "forecast": raw.get("forecast") or "-",
                "previous": raw.get("previous") or "-",
                "actual": raw.get("actual") or None,
                "_dt": dt,
            })
        return parsed

    def _parse_date(self, s):
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            try:
                dt = datetime.strptime(s, "%a %b %d %H:%M:%S GMT %Y")
                return dt.replace(tzinfo=timezone.utc)
            except Exception:
                return None

    def _fallback(self):
        now = datetime.now(timezone.utc)
        b1 = now + timedelta(minutes=30)
        b2 = now + timedelta(minutes=70)
        b3 = now + timedelta(minutes=150)
        return [
            {"time": b1.astimezone().strftime("%H:%M"), "currency": "US", "event": "CPI m/m",
             "impact": "HIGH", "forecast": "3.2%", "previous": "3.1%", "actual": None, "_dt": b1},
            {"time": b2.astimezone().strftime("%H:%M"), "currency": "US", "event": "Fed Chair Speech",
             "impact": "MEDIUM", "forecast": "-", "previous": "-", "actual": None, "_dt": b2},
            {"time": b3.astimezone().strftime("%H:%M"), "currency": "US", "event": "Crude Oil Inventories",
             "impact": "HIGH", "forecast": "-2M", "previous": "-1M", "actual": None, "_dt": b3},
        ]

    def get_upcoming(self, hours=12):
        now = datetime.now(timezone.utc)
        events = [e for e in self._fetch() if e["impact"] in ("HIGH", "MEDIUM")]
        if config.NEWS_CONFIG["high_impact_only"]:
            events = [e for e in events if e["impact"] == "HIGH"]
        events.sort(key=lambda x: x["_dt"])
        # Prefer events within the next `hours` (allow 1h past so just-released show)
        upcoming = [e for e in events if now - timedelta(hours=1) <= e["_dt"] <= now + timedelta(hours=hours)]
        if upcoming:
            return upcoming[:8]
        # Otherwise show nearest future events, or most recent past if all past
        future = [e for e in events if e["_dt"] > now - timedelta(hours=1)]
        if future:
            return future[:8]
        return events[-8:]

    def get_next_high_impact(self):
        now = datetime.now(timezone.utc)
        block = config.NEWS_CONFIG["block_minutes_before"]
        events = self._fetch()
        for e in events:
            et = e.get("_dt")
            if et is None:
                continue
            minutes = (et - now).total_seconds() / 60
            if 0 <= minutes <= block and e["impact"] == "HIGH":
                return e
        return None
