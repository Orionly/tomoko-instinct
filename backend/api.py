# Tomoko Brain Scanner - Web API (FastAPI)
# Run: uvicorn backend.api:app --reload
# Then open http://localhost:8000/

import os
import sys
import csv
import time
import threading
import logging
import pandas as pd

log = logging.getLogger(__name__)

# Ensure project root (parent of backend/) is importable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from typing import Optional
from datetime import datetime, timezone

from core.price_engine import PriceEngine, calculate_currency_strength, sanitize_for_json, safe_float
from core.regime_engine import RegimeEngine
from core.levels_engine import LevelsEngine
from core.brain_score import (calculate_brain_score, get_manual_action,
                             score_volatility, score_levels)
from core.fundamental_engine import FundamentalEngine
from core.watchout_zones import calculate_watchout_zones
from core.decision_zones import calculate_all_decision_zones
from core.intermarket_engine import IntermarketEngine
from core.performance_tracker import PerformanceTracker
from core.context_feed import ContextFeed
from core.macro_intelligence_engine import MacroIntelligenceEngine
from core.news_engine import NewsEngine, WAITING_SOURCE, _parse_event_time
from core.world_news_engine import get_world_news_engine
from core.macro_scenario_engine import MacroScenarioEngine
from core.trap_radar_engine import TrapRadarEngine
from core.session_briefing_engine import SessionBriefingEngine
import config

tracker = PerformanceTracker()

# --- Decoupled PriceEngine singleton --------------------------------------- #
_price_engine = None


def get_price_engine():
    global _price_engine
    if _price_engine is None:
        _price_engine = PriceEngine()
    return _price_engine


def _ensure_data_feed():
    """Return True if decoupled PriceEngine data feed is available."""
    pe = get_price_engine()
    return pe.connected or len(pe.latest_prices) > 0


_ctx_feed = None


def get_ctx_feed():
    global _ctx_feed
    if _ctx_feed is None:
        _ctx_feed = ContextFeed(get_price_engine())
    return _ctx_feed



_news_engine = None


def get_news_engine():
    """Single news gate for web + desktop: decoupled calendar only, no external feeds."""
    global _news_engine
    if _news_engine is None:
        _news_engine = NewsEngine(get_price_engine())
    return _news_engine


_macro_engine = None


def get_macro_engine():
    global _macro_engine
    if _macro_engine is None:
        _macro_engine = MacroIntelligenceEngine()
    return _macro_engine


_levels_engine = None


def get_levels_engine():
    global _levels_engine
    if _levels_engine is None:
        _levels_engine = LevelsEngine()
    return _levels_engine


_macro_scenario_engine = None


def get_macro_scenario_engine():
    global _macro_scenario_engine
    if _macro_scenario_engine is None:
        _macro_scenario_engine = MacroScenarioEngine(
            news_engine=get_news_engine(),
            macro_engine=get_macro_engine(),
            price_engine=get_price_engine(),
        )
    return _macro_scenario_engine


_trap_radar_engine = None


def get_trap_radar_engine():
    global _trap_radar_engine
    if _trap_radar_engine is None:
        _trap_radar_engine = TrapRadarEngine(
            price_engine=get_price_engine(),
            levels_engine=get_levels_engine(),
        )
    return _trap_radar_engine


_session_briefing_engine = None


def get_session_briefing_engine():
    global _session_briefing_engine
    if _session_briefing_engine is None:
        _session_briefing_engine = SessionBriefingEngine(
            price_engine=get_price_engine(),
            levels_engine=get_levels_engine(),
            news_engine=get_news_engine(),
            macro_engine=get_macro_engine(),
            macro_scenario_engine=get_macro_scenario_engine(),
            trap_radar_engine=get_trap_radar_engine(),
        )
    return _session_briefing_engine

# Calendar module globals - ensure same module instances, not new instances


# --- Calendar source of truth: THE EA DUMP via MT5Bridge only (no mt5.calendar_events) ---
_cal_log_state = {"source": None}


def _report_calendar_once(source, count):
    """Log calendar source once at startup + on state change only. No per-scan spam."""
    if _cal_log_state["source"] == source:
        return
    _cal_log_state["source"] = source
    if source == "calendar_unavailable":
        print(f"Calendar unavailable ({source}) - news will not block.")
    else:
        print(f"Macro Calendar: {source} {count} events")


def _get_ea_calendar():
    """Read decoupled calendar (nfs.faireconomy.media + Tradays actuals). Single source of truth."""
    cal = get_news_engine().get_global_events()
    source = cal.get('source', 'decoupled_faireconomy_tradays')
    events = cal.get('events', [])
    _report_calendar_once(source, len(events))
    return {
        "source": source,
        "last_update": cal.get('last_update', ''),
        "calendar": events,
        "count": len(events),
    }


def _build_news_bar_text(blocking_events, cal):
    """Global news radar message from blocking HIGH events (60min before / 30min after)."""
    parts, seen = [], set()
    for ev in blocking_events:
        key = (ev.get("time"), ev.get("event"), ev.get("currency"))
        if key in seen:
            continue
        seen.add(key)
        try:
            ev_dt = _parse_event_time(ev.get("time", ""))
            t = ev_dt.strftime("%H:%M") if ev_dt else str(ev.get("time", ""))[-5:]
        except Exception:
            t = ev.get("time", "")
        parts.append(f"{t} {ev.get('event', '')} [HIGH] AVOID {ev.get('currency', '')}")
    if parts:
        return "🔴 HIGH NEWS IMPACT: " + " • ".join(parts)
    return "🟢 NEWS RADAR: Clear • No high-impact releases scheduled in next 60m"


def _clamp(v, lo=0, hi=100):
    return int(max(lo, min(hi, v)))

TIMEFRAME_H4 = 16388
TIMEFRAME_H1 = 16385
TIMEFRAME_M15 = 15
TIMEFRAME_D1 = 16408
TIMEFRAME_W1 = 32769

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Log calendar source once at startup."""
    _get_ea_calendar()
    yield


app = FastAPI(title="Tomoko Brain API", version="2.0", lifespan=lifespan)

# Intermarket snapshot failure logged once, not per-scan
_im_logged = {"done": False}

# Simple time-based cache for expensive endpoints (scan_all runs 7x _scan serially = 2-5s)
_scan_cache = {"ts": 0.0, "result": None}
_HEATMAP_CACHE_TTL = 5.0  # seconds
_scan_all_cache = {"ts": 0.0, "result": None}
_SCAN_ALL_TTL = 3.0  # seconds


def _intermarket_snapshot():
    """Get DXY/US10Y/VIX snapshot; never prints per-scan, returns neutral fallback."""
    try:
        snap = IntermarketEngine(get_price_engine()).get_snapshot()
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


def _news_why(news, news_score):
    """Explain the news component for the hover tooltip."""
    if news_score >= 100:
        mins = config.NEWS_CONFIG.get("block_minutes_before", 60)
        return f"No HIGH news in {mins}min = news 100"
    blocking = news.get("blocking") or []
    if not blocking:
        return f"News gate closed = news {news_score}"
    ev = blocking[0]
    rel = ""
    try:
        ev_time = _parse_event_time(ev.get("time", ""))
        if ev_time:
            now_utc = datetime.now(timezone.utc)
            mins_diff = int((ev_time - now_utc).total_seconds() / 60)
            rel = f" in {mins_diff}min" if mins_diff >= 0 else f" {abs(mins_diff)}min ago"
    except Exception:
        rel = ""
    return f"BLOCKED: {ev.get('currency')} {ev.get('event')}{rel} = news {news_score}"


def _zone_label(watchout_zones):
    """DISCOUNT / PREMIUM / EQUILIBRIUM from the watchout zones (or '' if absent)."""
    pd = (watchout_zones or {}).get("premium_discount") or {}
    cp = pd.get("current_pct")
    if cp is None:
        return ""
    try:
        cp = float(cp)
    except (TypeError, ValueError):
        return ""
    if cp < 50:
        return "DISCOUNT"
    if cp > 50:
        return "PREMIUM"
    return "EQUILIBRIUM"


def _safe_rates(symbol, timeframe, count):
    """Fetch rates, returning None on any failure / empty frame (never raises)."""
    try:
        df = get_price_engine().get_rates(symbol, timeframe, count)
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
        "score_tooltips": {"structural": "", "volatility": "", "levels": "",
                            "intermarket": "", "news": reason},
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
                        "rates": {}},
        "news": {"is_clean": True, "blocking": [], "upcoming_events": [],
                 "source": WAITING_SOURCE, "status": WAITING_SOURCE, "count": 0,
                 "total_events": 0, "risk": "RISK-ON"},
        "watchout_zones": None,
        "decision_zones": None,
        "atr": 0,
        "ema21": 0,
    }


def _scan(symbol: str, cal=None):
    if not _ensure_data_feed():
        return _no_data_scan(symbol, reason="CONNECTING")

    if cal is None:
        cal = _get_ea_calendar()
    cal_source = cal.get("source", "calendar_unavailable")
    cal_count = cal.get("count", 0)
    cal_total = len(cal.get("calendar", []))

    frames = {}
    for key, tf, n in (("h4", TIMEFRAME_H4, 200), ("h1", TIMEFRAME_H1, 200),
                       ("m15", TIMEFRAME_M15, 100), ("d1", TIMEFRAME_D1, 50),
                       ("w1", TIMEFRAME_W1, 20)):
        frames[key] = _safe_rates(symbol, tf, n)
    if frames.get("h4") is None or frames.get("h1") is None:
        degraded = _no_data_scan(symbol, reason="CONNECTING")
        degraded["news"] = get_news_engine().get_news_for_symbol(symbol)
        return degraded
    if frames.get("m15") is None:
        frames["m15"] = frames["h1"]
    if frames.get("d1") is None:
        frames["d1"] = frames["h4"]
    if frames.get("w1") is None:
        frames["w1"] = frames["d1"]
    df_h4 = frames["h4"]
    df_h1 = frames["h1"]
    df_m15 = frames["m15"]
    df_d1 = frames["d1"]
    df_w1 = frames["w1"]

    regime = RegimeEngine(config).evaluate_mtf_trend(df_h4, df_h1, df_m15)
    levels = LevelsEngine().get_key_levels(df_d1, df_w1, df_h1)
    daily_open = safe_float(df_d1["open"].iloc[-1]) if (df_d1 is not None and len(df_d1) > 0) else 0.0
    price = safe_float(df_h1["close"].iloc[-1])
    if daily_open <= 0.0:
        daily_open = price
    levels["daily_open"] = daily_open
    h4 = regime["H4"]
    adx = safe_float(h4.get("adx", 0))
    atr_ratio = safe_float(h4.get("atr_ratio", 0))

    # --- Intermarket snapshot (DXY / US10Y / VIX) ---
    dxy_change, us10y_change, risk_sentiment = _intermarket_snapshot()

    # --- News: Autonomous Macro Calendar (NewsEngine) ---
    news = get_news_engine().get_news_for_symbol(symbol, hours_ahead=168)
    is_clean = news.get("is_clean", True)

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

    # --- Multi-timeframe Decision Zones (floating relative to current price) ---
    try:
        decision_zones = calculate_all_decision_zones(
            df_m15, df_h1, df_h4, price, levels, symbol=symbol
        )
    except Exception as e:
        print("decision zones calculation failed:", e)
        decision_zones = None

    # --- Real brain score ---
    # structural / intermarket / news come from the engines; volatility and
    # levels are computed here from live H4/price data (no magic constants).
    structural = fund["structural"]
    intermarket = fund["intermarket"]
    news_score = 0 if not is_clean else 100

    atr_ratio_h4 = safe_float(h4.get("atr_ratio", 0))
    dist_h4 = safe_float(h4.get("dist_ema21_atr", 0))
    volatility, vol_why = score_volatility(atr_ratio_h4, dist_h4)

    levels_score, levels_why = score_levels(
        price, levels["daily_low"], levels["daily_high"],
        zone_label=_zone_label(watchout_zones),
    )

    score = calculate_brain_score(structural, volatility, levels_score, intermarket, news_score)

    score_tooltips = {
        "structural": f"{fund['bias']} = structural {structural}",
        "volatility": vol_why,
        "levels": levels_why,
        "intermarket": f"{fund['bias']} = intermarket {intermarket}",
        "news": _news_why(news, news_score),
    }

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
    if "XAU" in symbol:
        if action.startswith("LIMIT AT LIQ HIGH") and (
            "DXY up" in fund["bias"] or "short pressure" in fund["bias"]
        ):
            reason = reason + f" [Fundamental Conflict] {fund['bias']}, reduce lot or wait."
        elif action.startswith("LIMIT AT LIQ LOW") and ("long favored" in fund["bias"]):
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
        "score_tooltips": score_tooltips,
        "action": action,
        "real_action": action,
        "reason": reason,
        "h4": regime["H4"],
        "h1": regime["H1"],
        "h1_bias": regime["H1_bias"],
        "h4_bias": regime["H4_bias"],
        "m15_bias": regime["M15_bias"],
        "levels": levels,  # includes weekly_open, daily_high, liq_high, liq_low
        "daily_open": daily_open,
        "weekly_open": levels["weekly_open"],
        "fundamental": fund,
        "news": news,
        "watchout_zones": watchout_zones,
        "decision_zones": decision_zones,
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
            "calendar_source": cal.get("source", "calendar_unavailable"),
            "calendar_last_update": cal.get("last_update", ""),
            "calendar_events": cal.get("calendar", [])[:30],
            "count": cal.get("count", 0),
        })
    except Exception as e:
        now_dt = datetime.now(timezone.utc)
        wday = now_dt.weekday()
        c_min = now_dt.hour * 60 + now_dt.minute
        is_wk = (wday == 5) or (wday == 4 and c_min >= 1260) or (wday == 6 and c_min < 1260)
        fallback_session = "WEEKEND CLOSED" if is_wk else "LONDON"
        return {"context": {}, "risk": "NEUTRAL", "session": fallback_session, "error": str(e)}


@app.get("/api/prices")
def get_prices():
    """Live prices from autonomous PriceEngine."""
    if not _ensure_data_feed():
        raise HTTPException(status_code=503, detail="Data feed unavailable")
    result = {}
    for s in config.PAIRS:
        p = get_price_engine().get_price(s)
        if p:
            result[s] = p
    # EA fallback for any missing
    ea_prices = get_price_engine().get_prices_from_ea()
    for s in config.PAIRS:
        if s not in result or result[s] is None:
            if s in ea_prices:
                result[s] = {"bid": ea_prices[s].get("bid"), "ask": ea_prices[s].get("ask"), "time": ea_prices[s].get("time", 0), "symbol": s, "source": "ea"}
    return {"prices": result, "ea_prices": ea_prices, "timestamp": datetime.now().isoformat()}


@app.get("/api/scan_all")
def scan_all():
    if not _ensure_data_feed():
        raise HTTPException(status_code=503, detail="Data feed unavailable")
    now = time.time()
    if _scan_all_cache["result"] is not None and (now - _scan_all_cache["ts"]) < _SCAN_ALL_TTL:
        return _scan_all_cache["result"]

    cal = _get_ea_calendar()
    scans = [_scan(s, cal) for s in config.PAIRS]
    blocking_news = []
    for sc in scans:
        blocking_news.extend(sc["news"].get("blocking", []))
    scan_results = {s["symbol"]: s for s in scans}
    market_pulse = calculate_currency_strength(scan_results)
    market_pulse["timestamp"] = datetime.now().strftime("%Y.%m.%d %H:%M")
    market_pulse["method"] = "H4 trend aggregation"
    market_heatmap = get_price_engine().get_market_heatmap(scan_results)
    cal_info = get_news_engine().get_global_events()
    response = sanitize_for_json({
        "scans": scans,
        "calendar_source": cal_info.get("source", "decoupled_faireconomy_tradays"),
        "calendar_last_update": cal_info.get("last_update", ""),
        "calendar_events": cal_info.get("events", [])[:30],
        "count": cal_info.get("count", 0),
        "news_bar_text": _build_news_bar_text(blocking_news, cal_info),
        "blocking_news": blocking_news,
        "scan_timestamp": datetime.now().isoformat(),
        "market_pulse": market_pulse,
        "global_calendar": cal_info.get("events", []),
        "market_heatmap": market_heatmap,
    })
    _scan_all_cache["ts"] = now
    _scan_all_cache["result"] = response
    return response


@app.get("/api/market_heatmap")
def market_heatmap():
    if not _ensure_data_feed():
        raise HTTPException(status_code=503, detail="Data feed unavailable")
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
    hm = get_price_engine().get_market_heatmap(scan_results)
    _scan_cache["ts"] = now
    _scan_cache["result"] = hm
    return sanitize_for_json({
        "heatmap": hm,
        "generated": datetime.now().isoformat(),
        "top_pick": hm[0] if hm else None,
        "cached": False,
    })


@app.get("/api/global_calendar")
def global_calendar(hours: int = 168, hours_back: int = 720):
    """Global calendar from decoupled news engine with 30-day archive support."""
    try:
        cal = get_news_engine().get_global_events(hours_ahead=hours, hours_back=hours_back)
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
        rows = tracker.get_recent_logs(100)
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


@app.get("/ui/lightweight-charts.standalone.production.js")
def serve_lightweight_charts_js():
    js_path = os.path.join(ROOT, "ui", "lightweight-charts.standalone.production.js")
    if os.path.exists(js_path):
        return FileResponse(js_path, media_type="application/javascript", headers={
            "Cache-Control": "public, max-age=86400",
        })
    raise HTTPException(status_code=404, detail="Library not found")


@app.get("/api/pair/{symbol}/candles")
def get_pair_candles(symbol: str, tf: str = "H1", limit: int = 500):
    """
    Returns sanitized OHLC candlestick data formatted for TradingView Lightweight Charts,
    along with marked zones from trader_profile.json and active decision zones.
    """
    symbol = symbol.upper()
    tf_upper = tf.upper()
    pe = get_price_engine()
    df = pe.get_rates(symbol, tf_upper, count=limit)
    
    candles = []
    seen_times = set()
    dec = 2 if "XAU" in symbol else (3 if "JPY" in symbol else 5)
    
    if df is not None and not df.empty:
        for idx, row in df.iterrows():
            t_val = row.get("time", idx)
            ts = None
            if hasattr(t_val, "timestamp"):
                ts = int(t_val.timestamp())
            elif isinstance(idx, (pd.Timestamp, datetime)):
                ts = int(idx.timestamp())
            else:
                try:
                    ts = int(pd.to_datetime(t_val).timestamp())
                except Exception:
                    continue
            if ts is None or ts in seen_times:
                continue
            seen_times.add(ts)
            candles.append({
                "time": ts,
                "open": round(safe_float(row["open"]), dec),
                "high": round(safe_float(row["high"]), dec),
                "low": round(safe_float(row["low"]), dec),
                "close": round(safe_float(row["close"]), dec),
                "volume": round(safe_float(row.get("volume", 0)), 1),
            })
            
    candles.sort(key=lambda x: x["time"])
    
    # Retrieve marked zones from trader_profile.json
    ingested_zones = []
    try:
        from core.markup_ingestion_engine import load_trader_profile
        prof = load_trader_profile()
        active = prof.get("active_session", {})
        tf_session = active.get(tf_upper, {})
        if tf_session and tf_session.get("symbol") == symbol:
            ingested_zones = tf_session.get("zones", [])
        elif not tf_session:
            for _tf_key, _sess in active.items():
                if _sess.get("symbol") == symbol and _sess.get("zones"):
                    ingested_zones = _sess.get("zones")
                    break
    except Exception as ex:
        log.debug("Error loading ingested zones for candles: %s", ex)
        
    cur_p = (pe.latest_prices.get(symbol) or {}).get("price", candles[-1]["close"] if candles else 0.0)
    
    return sanitize_for_json({
        "symbol": symbol,
        "timeframe": tf_upper,
        "candles": candles,
        "current_price": cur_p,
        "ingested_zones": ingested_zones,
    })


@app.get("/api/pair/{symbol}/deep_dive")
def get_pair_deep(symbol: str):
    """Per-pair deep dive: full scan for the symbol, its heatmap row, and its filtered calendar."""
    symbol = symbol.upper()
    if not _ensure_data_feed():
        raise HTTPException(status_code=503, detail="Data feed unavailable")
    scan = _scan(symbol)  # reuses existing scan pipeline; degraded scan if no data
    heatmap = get_price_engine().get_market_heatmap({symbol: scan})
    heatmap_row = heatmap[0] if heatmap else None
    upcoming = get_news_engine().get_upcoming_events(symbol, hours_ahead=168)
    currencies = get_price_engine().get_currencies(symbol)
    briefing = get_session_briefing_engine().get_briefing(symbol)
    traps = get_trap_radar_engine().scan_pair_liquidity_traps(symbol)
    scenarios = get_macro_scenario_engine().get_pair_catalyst_briefing(symbol)
    return sanitize_for_json({
        "symbol": symbol,
        "currencies": currencies,
        "scan": scan,
        "ai_battle_plan": briefing.get("plan", {}),
        "ai_session_info": {
            "session": briefing.get("session"),
            "generated_at_utc": briefing.get("generated_at_utc"),
            "global_macro_executive_thesis": briefing.get("global_macro_executive_thesis"),
        },
        "trap_radar": traps,
        "macro_scenarios": scenarios,
        "heatmap": heatmap_row,
        "calendar": upcoming,
        "calendar_count": len(upcoming),
        "global_calendar": get_news_engine().get_global_events().get("events", []),
        "calendar_source": (scan.get("news") or {}).get("source", "decoupled_faireconomy_tradays"),
        "generated": datetime.now().isoformat(),
    })


@app.get("/api/session_briefing")
def get_session_briefing_api(symbol: Optional[str] = None):
    """Returns the latest scheduled institutional Pre-Session AI Trade Battle-Plan."""
    return sanitize_for_json(get_session_briefing_engine().get_briefing(symbol))


@app.get("/api/macro_intel")
def get_macro_intel():
    """Returns the full 6-pillar macro matrix, hybrid TIPS, and synthesized insight."""
    try:
        return get_macro_engine().get_full_intelligence()
    except Exception as e:
        return {"error": str(e), "matrix": {}, "insight": {}, "sessions": {}}


@app.get("/api/pair/{symbol}/macro_confluence")
def get_pair_macro_confluence(symbol: str):
    """Per-pair macro confluence and driver attribution."""
    try:
        engine = get_macro_engine()
        matrix = engine.get_macro_matrix()
        insight = engine.synthesize_macro_insight(matrix)
        conf = engine.get_pair_macro_confluence(symbol, matrix, insight)
        return sanitize_for_json({"confluence": conf, "matrix": matrix})
    except Exception as e:
        return {"error": str(e), "confluence": {"symbol": symbol, "status": "NEUTRAL", "class": "neut", "rationale": "Offline"}}


@app.get("/api/world_news")
def get_world_news(pair: Optional[str] = None, category: Optional[str] = None, severity: Optional[str] = None, limit: int = 150):
    """Retrieve categorized world news and geopolitical headlines with pair attribution (30-day rolling archive)."""
    try:
        engine = get_world_news_engine()
        items = engine.get_news(pair=pair, category=category, severity=severity, limit=limit)
        return sanitize_for_json({
            "count": len(items),
            "items": items,
            "last_update": engine.last_fetch_time.isoformat() if engine.last_fetch_time else None,
        })
    except Exception as e:
        return {"count": 0, "items": [], "error": str(e)}


@app.get("/api/speeches")
def get_speeches(limit: int = 25):
    """Retrieve executive speech briefs (Powell, Trump, Ueda, Lagarde, Bailey, etc.)."""
    try:
        engine = get_world_news_engine()
        speeches = engine.get_speeches(limit=limit)
        return sanitize_for_json({
            "count": len(speeches),
            "speeches": speeches,
        })
    except Exception as e:
        return {"count": 0, "speeches": [], "error": str(e)}


@app.get("/api/geopolitical_shocks")
def get_geopolitical_shocks(max_age_minutes: int = 60):
    """Retrieve active critical/high severity geopolitical shocks for sticky floating alert banner."""
    try:
        engine = get_world_news_engine()
        shocks = engine.get_active_shocks(max_age_minutes=max_age_minutes)
        return sanitize_for_json({
            "active": len(shocks) > 0,
            "count": len(shocks),
            "shocks": shocks,
        })
    except Exception as e:
        return {"active": False, "count": 0, "shocks": [], "error": str(e)}


# --- Pre-Session Battle-Plan & Macro Scenario Routes ------------------------ #
@app.get("/api/briefing")
def get_session_briefing():
    """Retrieve latest institutional Pre-Session Battle-Plan across all pairs."""
    try:
        engine = get_session_briefing_engine()
        briefing = engine.get_briefing()
        if not briefing:
            briefing = engine.generate_briefing()
        return sanitize_for_json(briefing)
    except Exception as e:
        return {"error": str(e), "pairs": {}}


@app.get("/api/briefing/{symbol}")
def get_pair_briefing(symbol: str):
    """Retrieve tactical Pre-Session Battle-Plan for a specific pair."""
    try:
        engine = get_session_briefing_engine()
        briefing = engine.get_briefing(symbol=symbol)
        if not briefing or not briefing.get("plan"):
            full = engine.generate_briefing()
            briefing = engine.get_briefing(symbol=symbol)
        return sanitize_for_json(briefing)
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


@app.post("/api/briefing/generate")
def force_generate_briefing():
    """Trigger on-demand pre-session briefing generation across all pairs."""
    try:
        engine = get_session_briefing_engine()
        res = engine.generate_briefing()
        return sanitize_for_json({"success": True, "briefing": res})
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/scenarios")
def get_macro_scenarios(hours_ahead: int = 48, min_priority: int = 2):
    """Retrieve all catalyst-to-pair If-Then scenario trees for upcoming high/medium releases."""
    try:
        engine = get_macro_scenario_engine()
        trees = engine.get_upcoming_scenarios(hours_ahead=hours_ahead, min_priority=min_priority)
        return sanitize_for_json({
            "count": len(trees),
            "scenarios": trees,
        })
    except Exception as e:
        return {"count": 0, "scenarios": [], "error": str(e)}


@app.get("/api/pair/{symbol}/scenarios")
def get_pair_macro_scenarios(symbol: str):
    """Retrieve pair-centric catalyst synthesis, intermarket posture, and If-Then scenario trees."""
    try:
        engine = get_macro_scenario_engine()
        briefing = engine.get_pair_catalyst_briefing(symbol=symbol)
        return sanitize_for_json(briefing)
    except Exception as e:
        return {"symbol": symbol, "error": str(e), "catalysts": []}


# --- Crowd Trap & Liquidity Sweep Radar Routes ------------------------------ #
@app.get("/api/traps")
def get_all_liquidity_traps():
    """Scan all 7 pairs for active Buy-Side / Sell-Side liquidity purges and crowd traps."""
    try:
        radar = get_trap_radar_engine()
        results = radar.scan_all_pairs()
        return sanitize_for_json(results)
    except Exception as e:
        return {"scanned_count": 0, "active_traps_count": 0, "active_traps": [], "error": str(e)}


@app.get("/api/pair/{symbol}/traps")
def get_pair_liquidity_traps(symbol: str):
    """Retrieve deep liquidity pool diagnosis (BSL/SSL), sweep state, and crowd positioning skew."""
    try:
        radar = get_trap_radar_engine()
        res = radar.scan_pair_liquidity_traps(symbol=symbol)
        return sanitize_for_json(res)
    except Exception as e:
        return {"symbol": symbol, "error": str(e), "trap_state": "ERROR"}


# --- Local Private Extensions (Auto-loaded if present, git-ignored) --------- #
try:
    from backend.private_routes import register_private_routes
    register_private_routes(app)
except Exception:
    pass


@app.get("/", response_class=HTMLResponse)
def root():
    html_path = os.path.join(os.path.dirname(__file__), "..", "ui", "web_dashboard.html")
    html_path = os.path.abspath(html_path)
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()
    # Force version
    content = content.replace("V4.4 Block 1", "V5.0").replace("V4.4 Block 2", "V5.0").replace("V4.4 Block 3", "V5.0")
    return HTMLResponse(content=content, headers={
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
        "X-Tomoko-Version": "V5.0"
    })
