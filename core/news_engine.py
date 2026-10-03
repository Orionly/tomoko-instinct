# NewsEngine - MT5 / EA calendar is the ONLY news source.
#
# Single source of truth = MT5Bridge.get_calendar_from_ea(), which reads the
# TomokoDataPump EA dump (tomoko_calendar_raw.json). No external feeds, no
# fallback providers: if the EA file is missing or empty we return an empty
# calendar and log a warning instead of inventing data.
#
# is_news_clean() lives here as the one news gate used by both the web API and
# the desktop dashboard, so both surfaces always agree.

import logging
from datetime import datetime

from core.mt5_bridge import MT5Bridge

log = logging.getLogger(__name__)

# Source value reported when the EA calendar file is not available yet.
WAITING_SOURCE = "waiting_for_ea"
MISSING_SOURCE = "mt5_calendar_missing"

# Warn once per state transition instead of on every scan (7 symbols x 5s).
_cal_state = {"warned": False}


class NewsEngine:
    """MT5/EA-only news filter. Delegates raw reads to MT5Bridge."""

    def __init__(self, bridge=None):
        self.bridge = bridge or MT5Bridge()

    # ------------------------------------------------------------------ #
    # Calendar access (EA only)
    # ------------------------------------------------------------------ #

    def _calendar(self):
        """Normalized EA calendar, or an empty one marked waiting_for_ea."""
        cal = self.bridge.get_calendar_from_ea()
        events = cal.get("calendar") or []
        source = cal.get("source", MISSING_SOURCE)
        last_update = cal.get("last_update", "")
        if source == MISSING_SOURCE or not events:
            if not _cal_state["warned"]:
                _cal_state["warned"] = True
                log.warning("EA calendar not available - waiting for EA")
            return {"source": WAITING_SOURCE, "calendar": [], "last_update": last_update}
        _cal_state["warned"] = False
        return {"source": source, "calendar": events, "last_update": last_update}

    def is_available(self):
        """True when the EA calendar file has been read with at least one event."""
        return bool(self._calendar()["calendar"])

    # ------------------------------------------------------------------ #
    # News gate - the single implementation used by web + desktop
    # ------------------------------------------------------------------ #

    def is_news_clean(self, symbol, minutes_before=60, minutes_after=30):
        """HIGH events for `symbol` block the window [-after, +before] minutes."""
        cal = self._calendar()
        if not cal["calendar"]:
            # No EA calendar => nothing to block on. Never block on missing data.
            return True, []
        return self.bridge.is_news_clean(symbol, minutes_before=minutes_before,
                                        minutes_after=minutes_after)

    # ------------------------------------------------------------------ #
    # Event views
    # ------------------------------------------------------------------ #

    def get_upcoming_events(self, symbol, hours_ahead=168):
        """Upcoming EA events for the symbol's currencies (empty when no EA file)."""
        cal = self._calendar()
        if not cal["calendar"]:
            return []
        return self.bridge.get_upcoming_events(symbol, hours_ahead=hours_ahead)

    def get_global_events(self, hours_ahead=168):
        """All-currency EA events for the global calendar block."""
        cal = self._calendar()
        if not cal["calendar"]:
            return {
                "events": [],
                "count": 0,
                "source": WAITING_SOURCE,
                "status": WAITING_SOURCE,
                "last_update": cal["last_update"],
            }
        events = self.bridge.get_upcoming_events("ALL", hours_ahead=hours_ahead)
        return {
            "events": events,
            "count": len(events),
            "source": cal["source"],
            "status": "ok",
            "last_update": cal["last_update"],
        }

    def get_news_for_symbol(self, symbol, hours_ahead=168):
        """Full news payload for a symbol: is_clean gate + display events + risk."""
        cal = self._calendar()
        total = len(cal["calendar"])
        source = cal["source"]

        if not cal["calendar"]:
            return {
                "is_clean": True,
                "blocking": [],
                "upcoming_events": [],
                "source": source,
                "total_events": 0,
                "risk": "RISK-ON",
                "status": WAITING_SOURCE,
            }

        is_clean, blocking = self.is_news_clean(symbol)
        upcoming = self.get_upcoming_events(symbol, hours_ahead=hours_ahead)
        risk = "RISK-OFF" if blocking else "RISK-ON"

        return {
            "is_clean": is_clean,
            "blocking": blocking,
            "upcoming_events": upcoming,
            "source": source,
            "total_events": total,
            "risk": risk,
            "status": "ok",
        }

    def get_news_status(self, symbol):
        """Compact payload (is_clean + next HIGH events with countdown)."""
        cal = self._calendar()
        source = cal["source"]
        if not cal["calendar"]:
            return {"is_clean": True, "next_events": [], "news_score": 100,
                    "source": source, "status": WAITING_SOURCE}

        is_clean, blocking = self.is_news_clean(symbol)
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
            "status": "ok",
        }
