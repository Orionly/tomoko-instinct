# Tomoko Brain Scanner - Web API (FastAPI)
# Run: uvicorn backend.api:app --reload
# Then open http://localhost:8000/

import os
import sys
import csv
import time
import threading

# Ensure project root (parent of backend/) is importable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

import MetaTrader5 as mt5
from datetime import datetime

from core.mt5_bridge import MT5Bridge, calculate_currency_strength, sanitize_for_json, safe_float, ea_calendar, ea_quotes, _socket_server
from core.regime_engine import RegimeEngine
from core.levels_engine import LevelsEngine
from core.brain_score import calculate_brain_score, get_manual_action
from core.fundamental_engine import FundamentalEngine
from core.watchout_zones import calculate_watchout_zones
from core.intermarket_engine import IntermarketEngine
from core.performance_tracker import PerformanceTracker
from core.context_feed import ContextFeed
from core.news_engine import NewsEngine, WAITING_SOURCE
import config

tracker = PerformanceTracker()

# --- Lazy MT5 bridge singleton ---------------------------------------------- #
# connect() must NOT run at import time: if the MT5 terminal is closed the API
# should still start and answer /api/scan_all with a 503 JSON instead of dying.
# Endpoints call _ensure_mt5() to (re)connect once per cooldown window.
_mt5_bridge = None
_mt5_retry_state = {"last_attempt": 0.0}
_MT5_RETRY_COOLDOWN = 15.0  # seconds between reconnect attempts while MT5 is down

_ctx_feed = None


def get_mt5_bridge():
    global _mt5_bridge
    if _mt5_bridge is None:
        _mt5_bridge = MT5Bridge()
    return _mt5_bridge


def _ensure_mt5():
    """Return True if the bridge is (or just became) connected.

    While MT5 is down, retries at most once per _MT5_RETRY_COOLDOWN so a closed
    terminal does not stall every poll for the full connect() timeout.
    """
    bridge = get_mt5_bridge()
    if bridge.connected:
        return True
    now = time.time()
    if now - _mt5_retry_state["last_attempt"] < _MT5_RETRY_COOLDOWN:
        return False
    _mt5_retry_state["last_attempt"] = now
    try:
        ok = bool(bridge.connect())
    except Exception:
        ok = False
    return ok


def get_ctx_feed():
    global _ctx_feed
    if _ctx_feed is None:
        _ctx_feed = ContextFeed(get_mt5_bridge())
    return _ctx_feed


_news_engine = None


def get_news_engine():
    """Single news gate for web + desktop: EA calendar only, no external feeds."""
    global _news_engine
    if _news_engine is None:
        _news_engine = NewsEngine(get_mt5_bridge())
    return _news_engine

# Calendar module globals - ensure same module instances, not new instances


# --- Calendar source of truth: THE EA DUMP via MT5Bridge only (no mt5.calendar_events) ---
_cal_log_state = {"source": None}


def _report_calendar_once(source, count):
    """Log calendar source once at startup + on state change only. No per-scan spam."""
    if _cal_log_state["source"] == source:
        return
    _cal_log_state["source"] = source
    if source == "mt5_calendar_missing":
        print(f"EA Calendar MISSING ({source}) - news will not block. "
              f"Start TomokoDataPump EA to dump tomoko_calendar_raw.json")
    else:
        print(f"EA Calendar: {source} {count} events")


def _get_ea_calendar():
    """Read EA-dumped calendar (exactly as MT5 terminal shows). Single source of truth."""
    cal = get_mt5_bridge().get_calendar_from_ea()
    source = cal.get('source', 'mt5_calendar_missing')
    events = cal.get('calendar', [])
    _report_calendar_once(source, len(events))
    return {
        "source": source,
        "last_update": cal.get('last_update', ''),
        "calendar": events,
        "count": len(events),
    }


def _build_news_bar_text(blocking_events, cal):
    """Global news bar from blocking HIGH events (60min before / 30min after)."""
    parts, seen = [], set()
    for ev in blocking_events:
        key = (ev.get("time"), ev.get("event"), ev.get("currency"))
        if key in seen:
            continue
        seen.add(key)
        try:
            t = datetime.strptime(ev.get("time", ""), "%Y.%m.%d %H:%M").strftime("%H:%M")
        except Exception:
            t = ev.get("time", "")
        parts.append(f"{t} {ev.get('event', '')} [HIGH] AVOID {ev.get('currency', '')}")
    if parts:
        return " • ".join(parts) + " • Risk: RISK-OFF"
    src = cal.get("source", "mt5_calendar_missing")
    if src == "mt5_calendar_missing":
        return f"EA calendar unavailable (mt5_calendar_missing) • Risk: RISK-ON"
    return (f"No HIGH news in 60min • Calendar: {src} ({cal.get('count', 0)} events) "
            f"• {cal.get('last_update', '')} • Risk: RISK-ON")


def _clamp(v, lo=0, hi=100):
    return int(max(lo, min(hi, v)))

TIMEFRAME_H4 = mt5.TIMEFRAME_H4
TIMEFRAME_H1 = mt5.TIMEFRAME_H1
TIMEFRAME_M15 = mt5.TIMEFRAME_M15
TIMEFRAME_D1 = mt5.TIMEFRAME_D1
TIMEFRAME_W1 = mt5.TIMEFRAME_W1

def start_socket_in_thread():
    """Start the EA socket server in a background thread."""
    _socket_server(config.EA_SOCKET_PORT)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Start EA socket server + log calendar source once at startup.

    MT5 is intentionally NOT connected here - connect lazily per request so the
    API boots even when the terminal is closed.
    """
    threading.Thread(target=start_socket_in_thread, daemon=True).start()
    _get_ea_calendar()
    yield


app = FastAPI(title="Tomoko Brain API", version="2.0", lifespan=lifespan)

# Intermarket snapshot failure logged once, not per-scan
_im_logged = {"done": False}

# Simple time-based cache for expensive endpoints (scan_all runs 7x _scan serially = 2-5s)
_scan_cache = {"ts": 0.0, "result": None}
_HEATMAP_CACHE_TTL = 5.0  # seconds


def _intermarket_snapshot():
    """Get DXY/US10Y/VIX snapshot; never prints per-scan, returns neutral fallback."""
    try:
        snap = IntermarketEngine(get_mt5_bridge()).get_snapshot()
        dxy_change = safe_float((snap.get("DXY") or {}).get("chg", 0.0))
        us10y_change = safe_float((snap.get("US10Y") or {}).get("chg", 0.0))
        vix_dir = (snap.get("VIX") or {}).get("dir", "FLAT")
        risk_sentiment = "RISK-OFF" if vix_dir == "UP" else "RISK-ON" if vix_dir == "DOWN" else "NEUTRAL"
        return dxy_change, us10y_change, risk_sentiment
    except Exception as e:
        if not _im_logged["done"]:
            _im_logged["done"] = True
            print("intermarket snapshot failed:", e)
        return 0.0, 0.0, "NEUTRAL"


def _safe_rates(symbol, timeframe, count):
    """Fetch rates, returning None on any failure / empty frame (never raises)."""
    try:
        df = get_mt5_bridge().get_rates(symbol, timeframe, count)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    return df


def _no_data_scan(symbol, reason="NO_DATA"):
    """Degraded scan payload so dashboards never blank or crash on missing data."""
    return {
        "symbol": symbol,
        "price": 0,
        "score": 0,
        "score_breakdown": {"structural": 0, "volatility": 0, "levels": 0, "intermarket": 0, "news": 0},
        "action": "WAIT",
        "real_action": "WAIT",
        "reason": reason,
        "h4": {"gate": "INSIDE", "direction": "FLAT", "adx": 0, "atr_ratio": 0,
               "dist_ema21_atr": 0, "ema20": 0, "ema50": 0, "ema21": 0, "atr": 0, "close": 0},
        "h1": {"dist_ema21_atr": 0, "is_pullback": False, "rsi": 50,
               "close_vs_ema": "NEUTRAL", "bias": "Neutral", "close": 0},
        "h1_bias": "Neutral",
        "h4_bias": "Neutral",
        "m15_bias": "Neutral",
        "levels": {"daily_high": 0, "daily_low": 0, "weekly_open": 0,
                   "liq_high": 0, "liq_low": 0, "atr": 0, "close": 0},
        "weekly_open": 0,
        "fundamental": {"structural": 0, "intermarket": 0, "bias": "No data",
                        "levels_score": 0, "volatility_score": 0, "rates": {}},
        "news": {"is_clean": True, "blocking": [], "upcoming_events": [],
                 "source": WAITING_SOURCE, "status": WAITING_SOURCE, "count": 0,
                 "total_events": 0, "risk": "RISK-ON"},
        "watchout_zones": None,
        "atr": 0,
        "ema21": 0,
    }


def _scan(symbol: str, cal=None):
    if not _ensure_mt5():
        return _no_data_scan(symbol, reason="MT5_NOT_CONNECTED")

    if cal is None:
        cal = _get_ea_calendar()
    cal_source = cal.get("source", "mt5_calendar_missing")
    cal_count = cal.get("count", 0)
    cal_total = len(cal.get("calendar", []))

    frames = {}
    for key, tf, n in (("h4", TIMEFRAME_H4, 200), ("h1", TIMEFRAME_H1, 200),
                       ("m15", TIMEFRAME_M15, 100), ("d1", TIMEFRAME_D1, 50),
                       ("w1", TIMEFRAME_W1, 20)):
        frames[key] = _safe_rates(symbol, tf, n)
    if any(df is None for df in frames.values()):
        degraded = _no_data_scan(symbol, reason="NO_DATA")
        # News status still reflects the EA calendar (rates missing != calendar missing)
        degraded["news"] = get_news_engine().get_news_for_symbol(symbol)
        return degraded
    df_h4 = frames["h4"]
    df_h1 = frames["h1"]
    df_m15 = frames["m15"]
    df_d1 = frames["d1"]
    df_w1 = frames["w1"]

    regime = RegimeEngine(config).evaluate_mtf_trend(df_h4, df_h1, df_m15)
    levels = LevelsEngine().get_key_levels(df_d1, df_w1, df_h1)
    price = safe_float(df_h1["close"].iloc[-1])
    h4 = regime["H4"]
    adx = safe_float(h4.get("adx", 0))
    atr_ratio = safe_float(h4.get("atr_ratio", 0))

    # --- Intermarket snapshot (DXY / US10Y / VIX) ---
    dxy_change, us10y_change, risk_sentiment = _intermarket_snapshot()

    # --- News: EA calendar ONLY (NewsEngine = single gate, no external feeds) ---
    is_clean, blocking = get_news_engine().is_news_clean(symbol)
    upcoming = get_news_engine().get_upcoming_events(symbol, hours_ahead=168)
    news = {
        "is_clean": is_clean,
        "blocking": blocking,
        "upcoming_events": upcoming,
        "source": cal_source,
        "count": cal_count,
        "total_events": cal_total,
        "risk": "RISK-OFF" if blocking else "RISK-ON",
    }

    # --- Fundamental engine: structural / intermarket / bias (dynamic rates) ---
    # dxy_change / us10y_change wired from intermarket snapshot (spec TODO, done here)
    fund = FundamentalEngine().get_fundamental(symbol, dxy_change=dxy_change, us10y_change=us10y_change)

    # --- Watchout zones (watch areas, not signals) ---
    try:
        _ema21 = safe_float(df_h1["EMA21"].iloc[-1])
        _atr = safe_float(df_h1["ATR"].iloc[-1])
        watchout_zones = calculate_watchout_zones(
            df_h4, df_h1, price,
            levels["daily_low"], levels["daily_high"], levels["weekly_open"],
            levels["liq_high"], levels["liq_low"], _ema21, _atr,
            symbol=symbol, h4_dir=h4.get("direction", "UP"),
        )
    except Exception as e:
        print("watchout zones failed:", e)
        _atr = 0.0
        watchout_zones = None

    # --- Real brain score (no more 70,70,70,70,70 placeholders) ---
    structural = fund["structural"]
    volatility = fund["volatility_score"]
    levels_score = fund["levels_score"]
    intermarket = fund["intermarket"]
    news_score = 0 if not is_clean else 100

    score = calculate_brain_score(structural, volatility, levels_score, intermarket, news_score)

    # --- Manual action (news cleanliness gates everything) ---
    action, reason = get_manual_action(
        score,
        gate=h4,
        pullback=regime["H1"].get("is_pullback", False),
        news_clean=news["is_clean"],
        risk_sentiment=fund["bias"],  # per spec
        symbol=symbol,
        dist_atr=h4.get("dist_ema21_atr", 0),
        adx=adx,
        liq_high=levels["liq_high"],
        liq_low=levels["liq_low"],
        price=price,
        levels=levels,
    )

    # --- XAU fundamental conflict guard ---
    if "XAU" in symbol and action.startswith("LIMIT AT LIQ") and (
        "DXY up" in fund["bias"] or "short pressure" in fund["bias"]
    ):
        reason = reason + f" [Fundamental Conflict] {fund['bias']}, reduce lot or wait."

    full_data = {
        "symbol": symbol,
        "price": price,
        "score": score,
        "score_breakdown": {
            "structural": structural,
            "volatility": volatility,
            "levels": levels_score,
            "intermarket": intermarket,
            "news": news_score,
        },
        "action": action,
        "real_action": action,
        "reason": reason,
        "h4": regime["H4"],
        "h1": regime["H1"],
        "h1_bias": regime["H1_bias"],
        "h4_bias": regime["H4_bias"],
        "m15_bias": regime["M15_bias"],
        "levels": levels,  # includes weekly_open, daily_high, liq_high, liq_low
        "weekly_open": levels["weekly_open"],
        "fundamental": fund,
        "news": news,
        "watchout_zones": watchout_zones,
        "atr": _atr,
        "ema21": _ema21,
    }

    tracker.log(full_data)
    return full_data


@app.get("/api/context")
def get_context():
    try:
        ctx = get_ctx_feed().get_context()
        risk = get_ctx_feed().get_risk_sentiment(ctx)
        session, _active = get_ctx_feed().get_session()
        cal = _get_ea_calendar()
        return sanitize_for_json({
            "context": ctx, "risk": risk, "session": session,
            "calendar_source": cal.get("source", "mt5_calendar_missing"),
            "calendar_last_update": cal.get("last_update", ""),
            "calendar_events": cal.get("calendar", [])[:30],
            "count": cal.get("count", 0),
        })
    except Exception as e:
        return {"context": {}, "risk": "NEUTRAL", "session": "LONDON", "error": str(e)}


@app.get("/api/prices")
def get_prices():
    """Live prices: MT5 tick first, EA fallback."""
    if not _ensure_mt5():
        raise HTTPException(status_code=503, detail="MT5 terminal not connected")
    result = {}
    for s in config.PAIRS:
        p = get_mt5_bridge().get_price(s)
        if p:
            result[s] = p
    # EA fallback for any missing
    ea_prices = get_mt5_bridge().get_prices_from_ea()
    for s in config.PAIRS:
        if s not in result or result[s] is None:
            if s in ea_prices:
                result[s] = {"bid": ea_prices[s].get("bid"), "ask": ea_prices[s].get("ask"), "time": ea_prices[s].get("time", 0), "symbol": s, "source": "ea"}
    return {"prices": result, "ea_prices": ea_prices, "timestamp": datetime.now().isoformat()}


@app.get("/api/scan_all")
def scan_all():
    if not _ensure_mt5():
        raise HTTPException(status_code=503, detail="MT5 terminal not connected")
    cal = _get_ea_calendar()
    scans = [_scan(s, cal) for s in config.PAIRS]
    blocking_news = []
    for sc in scans:
        blocking_news.extend(sc["news"].get("blocking", []))
    scan_results = {s["symbol"]: s for s in scans}
    market_pulse = calculate_currency_strength(scan_results)
    market_pulse["timestamp"] = datetime.now().strftime("%Y.%m.%d %H:%M")
    market_pulse["method"] = "H4 trend aggregation"
    market_heatmap = get_mt5_bridge().get_market_heatmap(scan_results)
    response = {
        "scans": scans,
        "calendar_source": cal.get("source", "mt5_calendar_missing"),
        "calendar_last_update": cal.get("last_update", ""),
        "calendar_events": cal.get("calendar", [])[:30],
        "count": cal.get("count", 0),
        "news_bar_text": _build_news_bar_text(blocking_news, cal),
        "blocking_news": blocking_news,
        "scan_timestamp": datetime.now().isoformat(),
        "market_pulse": market_pulse,
        "global_calendar": get_mt5_bridge().get_global_calendar(),
        "market_heatmap": market_heatmap,
    }
    return sanitize_for_json(response)


@app.get("/api/market_heatmap")
def market_heatmap():
    if not _ensure_mt5():
        raise HTTPException(status_code=503, detail="MT5 terminal not connected")
    now = time.time()
    # Return cached result if fresh enough (avoids re-running 7x _scan = 1.8s)
    if _scan_cache["result"] is not None and (now - _scan_cache["ts"]) < _HEATMAP_CACHE_TTL:
        cached = _scan_cache["result"]
        return sanitize_for_json({
            "heatmap": cached,
            "generated": datetime.now().isoformat(),
            "top_pick": cached[0] if cached else None,
            "cached": True,
        })
    cal = _get_ea_calendar()
    scans = [_scan(s, cal) for s in config.PAIRS]
    scan_results = {s["symbol"]: s for s in scans}
    hm = get_mt5_bridge().get_market_heatmap(scan_results)
    _scan_cache["ts"] = now
    _scan_cache["result"] = hm
    return sanitize_for_json({
        "heatmap": hm,
        "generated": datetime.now().isoformat(),
        "top_pick": hm[0] if hm else None,
        "cached": False,
    })


@app.get("/api/global_calendar")
def global_calendar(hours: int = 168):
    """Global calendar straight from the EA dump. Missing EA file => empty + waiting_for_ea."""
    try:
        cal = get_news_engine().get_global_events(hours_ahead=hours)
    except Exception as exc:
        print(f"global calendar failed: {exc}")
        cal = {"events": [], "count": 0, "source": WAITING_SOURCE,
               "status": WAITING_SOURCE, "last_update": ""}
    return sanitize_for_json({
        "events": cal.get("events", []),
        "count": cal.get("count", 0),
        "source": cal.get("source", WAITING_SOURCE),
        "status": cal.get("status", WAITING_SOURCE),
        "last_update": cal.get("last_update", ""),
        "window": f"{hours}h",
        "generated": datetime.now().isoformat(),
    })


@app.get("/api/logs")
def get_logs():
    try:
        with open(tracker.file, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))[-100:]  # last 100
        return {"count": len(rows), "rows": rows}
    except Exception:
        return {"count": 0, "rows": []}


@app.get("/pair/{symbol}")
def serve_pair(symbol: str):
    # ui/ is the single source of truth for HTML - never backend/static/
    html_path = os.path.join(ROOT, "ui", "pair.html")
    return FileResponse(html_path, headers={
        "Cache-Control": "no-cache, no-store, must-revalidate",
    })


@app.get("/api/pair/{symbol}/deep_dive")
def get_pair_deep(symbol: str):
    """Per-pair deep dive: full scan for the symbol, its heatmap row, and its filtered calendar."""
    symbol = symbol.upper()
    if not _ensure_mt5():
        raise HTTPException(status_code=503, detail="MT5 terminal not connected")
    scan = _scan(symbol)  # reuses existing scan pipeline; degraded scan if no data
    heatmap = get_mt5_bridge().get_market_heatmap({symbol: scan})
    heatmap_row = heatmap[0] if heatmap else None
    upcoming = get_mt5_bridge().get_upcoming_events(symbol, hours_ahead=168)
    currencies = get_mt5_bridge().get_currencies(symbol)
    return sanitize_for_json({
        "symbol": symbol,
        "currencies": currencies,
        "scan": scan,
        "heatmap": heatmap_row,
        "calendar": upcoming,
        "calendar_count": len(upcoming),
        "global_calendar": get_mt5_bridge().get_global_calendar(),
        "calendar_source": (scan.get("news") or {}).get("source", "mt5_terminal_calendar"),
        "generated": datetime.now().isoformat(),
    })


@app.get("/", response_class=HTMLResponse)
def root():
    html_path = os.path.join(os.path.dirname(__file__), "..", "ui", "web_dashboard.html")
    html_path = os.path.abspath(html_path)
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()
    # Force version
    content = content.replace("V4.4 Block 1", "V4.4 Block 3").replace("V4.4 Block 2", "V4.4 Block 3")
    return HTMLResponse(content=content, headers={
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
        "X-Tomoko-Version": "V4.4 Block 3"
    })
