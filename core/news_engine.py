# NewsEngine - EA calendar only (TomokoDataPump dump via MT5Bridge).
# No mt5.calendar_events (not available in MetaTrader5 pip 5.0.6147), no pip-version spam.
# Single source of truth = MT5Bridge.get_calendar_from_ea().

from datetime import datetime
from core.mt5_bridge import MT5Bridge


class NewsEngine:
    """EA-calendar news filter. Delegates to MT5Bridge (EA dump, terminal-native)."""

    def __init__(self):
        self.bridge = MT5Bridge()

    def get_upcoming_events(self, symbol, hours_ahead=168):
        """Upcoming events for the symbol within the display window (168h, weekend-aware)."""
        return self.bridge.get_upcoming_events(symbol, hours_ahead=hours_ahead)

    def get_news_for_symbol(self, symbol, hours_ahead=168):
        """Full news payload for a symbol: is_clean gate + display events + risk."""
        cal = self.bridge.get_calendar_from_ea()
        source = cal.get("source", "mt5_calendar_missing")
        total = len(cal.get("calendar", []))

        if source == "mt5_calendar_missing":
            return {
                "is_clean": True,
                "blocking": [],
                "upcoming_events": [],
                "source": source,
                "total_events": 0,
                "risk": "RISK-ON",
            }

        is_clean, blocking = self.bridge.is_news_clean(symbol)
        upcoming = self.bridge.get_upcoming_events(symbol, hours_ahead=hours_ahead)
        risk = "RISK-OFF" if blocking else "RISK-ON"

        return {
            "is_clean": is_clean,
            "blocking": blocking,
            "upcoming_events": upcoming,
            "source": source,
            "total_events": total,
            "risk": risk,
        }

    def get_news_status(self, symbol):
        """Legacy compact payload (is_clean + next HIGH events)."""
        cal = self.bridge.get_calendar_from_ea()
        source = cal.get("source", "mt5_calendar_missing")
        if source == "mt5_calendar_missing":
            return {"is_clean": True, "next_events": [], "news_score": 100, "source": source}

        is_clean, blocking = self.bridge.is_news_clean(symbol)
        now = datetime.now()
        next_events = []
        for ev in blocking:
            try:
                ev_time = datetime.strptime(ev.get("time", ""), "%Y.%m.%d %H:%M")
                countdown = int((ev_time - now).total_seconds() / 60)
            except Exception:
                countdown = None
            next_events.append({
                "time": ev.get("time", ""),
                "event": ev.get("event", "High Impact"),
                "impact": "HIGH",
                "currency": ev.get("currency", ""),
                "countdown_min": countdown,
            })

        return {
            "is_clean": is_clean,
            "next_events": next_events,
            "news_score": 0 if not is_clean else 100,
            "source": source,
        }
