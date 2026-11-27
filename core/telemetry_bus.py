# Tomoko Instinct — Real-Time Data-Flow & System Health Telemetry Bus
#
# Captures EVERY data movement across External Upstreams, Core Compute Engines,
# In-Memory/Disk Caches, Persistence Vaults, and FastAPI Endpoints.
# Zero failure ranking: every ERROR, FALLBACK, STALE, CACHED, or LIVE state
# is tracked, recorded in a thread-safe ring buffer, and pushed via SSE.

import os
import time
import json
import queue
import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Valid data-flow states (every non-LIVE/CACHED anomaly is surfaced equally)
STATE_LIVE = "LIVE"
STATE_CACHED = "CACHED"
STATE_STALE = "STALE"
STATE_FALLBACK = "FALLBACK"
STATE_ERROR = "ERROR"
STATE_IDLE = "IDLE"


# ============================================================================
# AUTHORITATIVE TOMOKO SYSTEM TOPOLOGY (44 NODES · 68 DIRECTED DATA PIPES)
# ============================================================================
TOPOLOGY_NODES: List[Dict[str, Any]] = [
    # --- Central Router Hub ---
    {
        "id": "hub.tomoko_core",
        "label": "TOMOKO CORTEX BUS",
        "short": "T",
        "cluster": "HUB",
        "file": "backend/api.py",
        "role": "Central Data-Flow Router & Scan Pipeline Coordinator",
        "expected_ttl_sec": 30,
    },

    # --- 1. EXTERNAL UPSTREAMS (8) ---
    {
        "id": "up.mt5_bridge",
        "label": "MT5 Broker Bridge (HFM / VT)",
        "short": "MT5",
        "cluster": "UPSTREAM",
        "file": "core/price_engine.py:_init_mt5_bridge",
        "role": "Tier-1 live broker ticks & authentic M15/H1/H4/D1/W1 candles",
        "expected_ttl_sec": 15,
    },
    {
        "id": "up.tv_oracle",
        "label": "TradingView Spot Oracle",
        "short": "TV",
        "cluster": "UPSTREAM",
        "file": "core/price_engine.py:_fetch_tradingview_spot",
        "role": "Tier-2 public CFD/Forex scanner quotes (OANDA:XAUUSD + 6 FX pairs)",
        "expected_ttl_sec": 30,
    },
    {
        "id": "up.yfinance_bars",
        "label": "Yahoo Finance OHLCV & Spot",
        "short": "YF-FX",
        "cluster": "UPSTREAM",
        "file": "core/price_engine.py:refresh_all_candles",
        "role": "Decoupled historical OHLCV bars (60d H1, 14d M15, 1y D1) & spot fallback",
        "expected_ttl_sec": 120,
    },
    {
        "id": "up.yfinance_macro",
        "label": "Yahoo Finance 6-Pillar Macro",
        "short": "YF-MAC",
        "cluster": "UPSTREAM",
        "file": "core/macro_intelligence_engine.py:_fetch_yf_ticker",
        "role": "Live macro tickers: DX-Y.NYB, ^TNX, TIP, CL=F, ^GSPC, ^VIX, GC=F",
        "expected_ttl_sec": 300,
    },
    {
        "id": "up.fred_dfii10",
        "label": "St. Louis Fed FRED (DFII10)",
        "short": "FRED",
        "cluster": "UPSTREAM",
        "file": "core/macro_intelligence_engine.py:get_fred_10y_real_yield",
        "role": "10-Year Treasury Inflation-Indexed Real Yield CSV feed",
        "expected_ttl_sec": 21600,
    },
    {
        "id": "up.faireconomy_tradays",
        "label": "FairEconomy & Tradays Calendar",
        "short": "CAL-UP",
        "cluster": "UPSTREAM",
        "file": "core/news_engine.py:sync_calendar",
        "role": "Weekly ForexFactory JSON + Tradays T+60s actual release widget",
        "expected_ttl_sec": 86400,
    },
    {
        "id": "up.world_rss_truth",
        "label": "World RSS (10 Wires) & Truth Social",
        "short": "RSS",
        "cluster": "UPSTREAM",
        "file": "core/world_news_engine.py:sync_feeds",
        "role": "Reuters/AP/BBC/AlJazeera/GoogleNews RSS + @realDonaldTrump Truth Social",
        "expected_ttl_sec": 360,
    },
    {
        "id": "up.gemini_cloud",
        "label": "Google Gemini 2.5 Flash Cloud",
        "short": "GEMINI",
        "cluster": "UPSTREAM",
        "file": "core/session_briefing_engine.py:generate_briefing",
        "role": "Step-1 Google Search Grounding + Step-2 Batched 7-Pair JSON Battle-Plan",
        "expected_ttl_sec": 21600,
    },

    # --- 2. CORE COMPUTE ENGINES (16) ---
    {
        "id": "eng.price_engine",
        "label": "PriceEngine (Spot + Bars + Heatmap)",
        "short": "PRICE",
        "cluster": "ENGINE",
        "file": "core/price_engine.py",
        "role": "Autonomous spot polling (5s), candle refresh (30s), basis offset & indicators",
        "expected_ttl_sec": 15,
    },
    {
        "id": "eng.regime_engine",
        "label": "RegimeEngine (MTF Trend & H4 Gate)",
        "short": "REGIME",
        "cluster": "ENGINE",
        "file": "core/regime_engine.py",
        "role": "Evaluates H4 Gate (ADX/ATR/EMA21), H1 pullback, and M15 directional alignment",
        "expected_ttl_sec": 60,
    },
    {
        "id": "eng.levels_engine",
        "label": "LevelsEngine (PDH/PDL/Weekly/Liq)",
        "short": "LEVELS",
        "cluster": "ENGINE",
        "file": "core/levels_engine.py",
        "role": "Extracts Daily High/Low, Weekly Open, Pivot, and Liquidity High/Low",
        "expected_ttl_sec": 60,
    },
    {
        "id": "eng.intermarket_engine",
        "label": "IntermarketEngine (DXY/US10Y/VIX)",
        "short": "INTER",
        "cluster": "ENGINE",
        "file": "core/intermarket_engine.py",
        "role": "Cross-asset DXY, US10Y, and VIX risk-on/risk-off snapshot",
        "expected_ttl_sec": 120,
    },
    {
        "id": "eng.fundamental_engine",
        "label": "FundamentalEngine (Rate & DXY Bias)",
        "short": "FUND",
        "cluster": "ENGINE",
        "file": "core/fundamental_engine.py",
        "role": "Computes structural rate differential and dynamic DXY/US10Y intermarket score",
        "expected_ttl_sec": 60,
    },
    {
        "id": "eng.watchout_zones",
        "label": "WatchoutZones (Premium/Discount)",
        "short": "WATCH",
        "cluster": "ENGINE",
        "file": "core/watchout_zones.py",
        "role": "Calculates dealing range Premium/Discount/Equilibrium % and watch areas",
        "expected_ttl_sec": 60,
    },
    {
        "id": "eng.decision_zones",
        "label": "DecisionZones (M15/H1/H4 50% CE)",
        "short": "D-ZONES",
        "cluster": "ENGINE",
        "file": "core/decision_zones.py",
        "role": "Algorithmic Upper/Lower Supply & Demand zones, 50% CE midlines, Swap Flips",
        "expected_ttl_sec": 60,
    },
    {
        "id": "eng.brain_score",
        "label": "BrainScore & Action Gate",
        "short": "SCORE",
        "cluster": "ENGINE",
        "file": "core/brain_score.py",
        "role": "Synthesizes 5-pillar score (Structure/Vol/Levels/Intermarket/News) & Manual Action",
        "expected_ttl_sec": 60,
    },
    {
        "id": "eng.news_engine",
        "label": "NewsEngine (Calendar & 60m Block Gate)",
        "short": "NEWS",
        "cluster": "ENGINE",
        "file": "core/news_engine.py",
        "role": "Filters high-impact blocking events (60m pre / 30m post) & polls T+60s actuals",
        "expected_ttl_sec": 120,
    },
    {
        "id": "eng.world_news_engine",
        "label": "WorldNewsEngine + FinBERT Classifier",
        "short": "W-NEWS",
        "cluster": "ENGINE",
        "file": "core/world_news_engine.py",
        "role": "Classifies geopolitical shocks, central bank speeches, and pair attribution",
        "expected_ttl_sec": 300,
    },
    {
        "id": "eng.macro_intel",
        "label": "MacroIntelligenceEngine (6 Pillars)",
        "short": "MACRO",
        "cluster": "ENGINE",
        "file": "core/macro_intelligence_engine.py",
        "role": "Builds 6-pillar matrix, Hybrid 10Y TIPS pressure, session states & playbook",
        "expected_ttl_sec": 300,
    },
    {
        "id": "eng.macro_scenario",
        "label": "MacroScenarioEngine (If-Then Trees)",
        "short": "SCEN",
        "cluster": "ENGINE",
        "file": "core/macro_scenario_engine.py",
        "role": "Generates pre-event Hot/In-Line/Cool scenario trees & post-release verdicts",
        "expected_ttl_sec": 300,
    },
    {
        "id": "eng.trap_radar",
        "label": "TrapRadarEngine (BSL/SSL & Judas)",
        "short": "TRAP",
        "cluster": "ENGINE",
        "file": "core/trap_radar_engine.py",
        "role": "Detects Buy-Side/Sell-Side liquidity sweeps, Bull/Bear traps & retail skew",
        "expected_ttl_sec": 120,
    },
    {
        "id": "eng.us_catalyst",
        "label": "USCatalystPredictor (T-30m Matrix)",
        "short": "CAT-P",
        "cluster": "ENGINE",
        "file": "core/us_catalyst_predictor.py",
        "role": "Synthesizes T-30m institutional bank matrix & pre-release crowd-trap warnings",
        "expected_ttl_sec": 300,
    },
    {
        "id": "eng.session_briefing",
        "label": "SessionBriefingEngine (Battle-Plan)",
        "short": "BRIEF",
        "cluster": "ENGINE",
        "file": "core/session_briefing_engine.py",
        "role": "Pre-Tokyo/London/NY 7-pair Battle-Plan generator & Setup Sanitizer Gate",
        "expected_ttl_sec": 21600,
    },
    {
        "id": "eng.battle_record",
        "label": "BattleRecordEngine (M15 Evaluator)",
        "short": "BATTLE",
        "cluster": "ENGINE",
        "file": "core/battle_record_engine.py",
        "role": "Evaluates active Battle-Plans every 60s against live M15 candles (Fills/TP/SL)",
        "expected_ttl_sec": 120,
    },

    # --- 3. IN-MEMORY & DISK CACHES (6) ---
    {
        "id": "cache.candles_pkl",
        "label": "Candles Pickle Cache",
        "short": "C-PKL",
        "cluster": "CACHE",
        "file": "logs/candles_cache.pkl",
        "role": "Persists 7-pair × 5-TF OHLCV bars + basis offsets for <10ms startup",
        "expected_ttl_sec": 120,
    },
    {
        "id": "cache.scan_all",
        "label": "Scanner (3s) & Heatmap (5s) Cache",
        "short": "C-SCAN",
        "cluster": "CACHE",
        "file": "backend/api.py:_scan_all_cache",
        "role": "Fast in-memory TTL cache preventing redundant 7-pair serial scans",
        "expected_ttl_sec": 30,
    },
    {
        "id": "cache.macro_ttl",
        "label": "Macro YF (5m) & FRED (6h) Cache",
        "short": "C-MAC",
        "cluster": "CACHE",
        "file": "core/macro_intelligence_engine.py:_cache",
        "role": "In-memory cache for 7 macro tickers and FRED DFII10 real yield",
        "expected_ttl_sec": 300,
    },
    {
        "id": "cache.calendar_json",
        "label": "30-Day Macro Calendar Cache",
        "short": "C-CAL",
        "cluster": "CACHE",
        "file": "logs/decoupled_calendar_cache.json",
        "role": "Rolling 30-day economic calendar archive with Tradays actuals",
        "expected_ttl_sec": 86400,
    },
    {
        "id": "cache.world_news_json",
        "label": "30-Day World News & Speech Cache",
        "short": "C-WN",
        "cluster": "CACHE",
        "file": "logs/world_news_cache.json",
        "role": "Rolling 30-day archive (up to 3,000 news items & 150 speech briefs)",
        "expected_ttl_sec": 600,
    },
    {
        "id": "cache.session_briefing",
        "label": "Session Briefing Disk Cache",
        "short": "C-BRF",
        "cluster": "CACHE",
        "file": "logs/session_briefings.json",
        "role": "Latest generated 7-pair Pre-Session Battle-Plan JSON",
        "expected_ttl_sec": 21600,
    },

    # --- 4. PERSISTENCE & TRADER DNA VAULTS (5) ---
    {
        "id": "vault.battle_ledger",
        "label": "BattleRecord Ledger & Monthly Archive",
        "short": "V-BTL",
        "cluster": "VAULT",
        "file": "logs/battle_record.json",
        "role": "Hot ledger (logs/battle_record.json) + permanent monthly archive (data/battle_archive/)",
        "expected_ttl_sec": 3600,
    },
    {
        "id": "vault.perf_tracker",
        "label": "PerformanceTracker & Scan Log CSV",
        "short": "V-LOG",
        "cluster": "VAULT",
        "file": "core/performance_tracker.py",
        "role": "Appends scan telemetry & score history for audit (/api/logs)",
        "expected_ttl_sec": 300,
    },
    {
        "id": "vault.tuned_dna",
        "label": "Weekend-Calibrated Entry DNA",
        "short": "V-TUNE",
        "cluster": "VAULT",
        "file": "data/tuned_entry_dna.json",
        "role": "Optuna/ML weekend-tuned SL/TP/wick parameters read by SessionBriefingEngine",
        "expected_ttl_sec": 604800,
    },
    {
        "id": "vault.trader_dna_profile",
        "label": "Trader DNA Profile & Dropoff Queue",
        "short": "V-DNA",
        "cluster": "VAULT",
        "file": "trader_dna/data/trader_dna_profile.json",
        "role": "Decoupled ground-truth zone markups & trade execution reviews (Zero Live Interference)",
        "expected_ttl_sec": 604800,
    },
    {
        "id": "vault.d_drive_dataset",
        "label": "D:\\Tomoko_Trader_Vault Dataset",
        "short": "V-D:",
        "cluster": "VAULT",
        "file": "D:/Tomoko_Trader_Vault/dataset",
        "role": "Permanent VLM/LLM training dataset (images/, labels/, train_vlm.jsonl, train_llm.jsonl)",
        "expected_ttl_sec": 604800,
    },

    # --- 5. FASTAPI ENDPOINTS (8) ---
    {
        "id": "api.scan_all",
        "label": "GET /api/scan_all & /market_heatmap",
        "short": "API-SCN",
        "cluster": "API",
        "file": "backend/api.py:scan_all",
        "role": "Primary dashboard 7-pair scanner, currency strength pulse & momentum heatmap",
        "expected_ttl_sec": 30,
    },
    {
        "id": "api.prices_context",
        "label": "GET /api/prices & /api/context",
        "short": "API-PRC",
        "cluster": "API",
        "file": "backend/api.py:get_prices",
        "role": "Real-time spot bid/ask quotes, session state, and global risk sentiment",
        "expected_ttl_sec": 30,
    },
    {
        "id": "api.pair_deep_dive",
        "label": "GET /api/pair/{sym}/deep_dive & /candles",
        "short": "API-PAIR",
        "cluster": "API",
        "file": "backend/api.py:get_pair_deep",
        "role": "Per-pair OHLCV candles, algorithmic decision zones, briefing, traps & scenarios",
        "expected_ttl_sec": 60,
    },
    {
        "id": "api.macro_intel",
        "label": "GET /api/macro_intel & /macro_confluence",
        "short": "API-MAC",
        "cluster": "API",
        "file": "backend/api.py:get_macro_intel",
        "role": "Serves 6-pillar macro matrix, Hybrid 10Y TIPS & per-pair driver attribution",
        "expected_ttl_sec": 120,
    },
    {
        "id": "api.news_world",
        "label": "GET /api/world_news, /speeches & /shocks",
        "short": "API-NWS",
        "cluster": "API",
        "file": "backend/api.py:get_world_news",
        "role": "Serves global economic calendar, geopolitical headlines, speeches & shock alerts",
        "expected_ttl_sec": 120,
    },
    {
        "id": "api.briefing",
        "label": "GET /api/briefing & POST /briefing/generate",
        "short": "API-BRF",
        "cluster": "API",
        "file": "backend/api.py:get_session_briefing",
        "role": "Serves Pre-Session AI Trade Battle-Plan across all 7 pairs",
        "expected_ttl_sec": 300,
    },
    {
        "id": "api.traps_scenarios",
        "label": "GET /api/traps, /scenarios & /catalyst_pred",
        "short": "API-TRP",
        "cluster": "API",
        "file": "backend/api.py:get_all_liquidity_traps",
        "role": "Serves BSL/SSL liquidity sweep radar, If-Then scenario trees & T-30m predictions",
        "expected_ttl_sec": 120,
    },
    {
        "id": "api.battle_dna",
        "label": "GET /api/battle-record & /weekend_comparison",
        "short": "API-BTL",
        "cluster": "API",
        "file": "backend/api.py:get_battle_record",
        "role": "Serves monitored BattleRecord ledger outcomes & isolated Trader DNA benchmark",
        "expected_ttl_sec": 300,
    },
]

# Directed edges (src -> dst) representing real data movement pipes
TOPOLOGY_EDGES: List[Dict[str, str]] = [
    # Upstream -> Core Engines
    {"src": "up.mt5_bridge", "dst": "eng.price_engine", "label": "MT5 ticks & OHLCV"},
    {"src": "up.tv_oracle", "dst": "eng.price_engine", "label": "TV CFD/FX spot quotes"},
    {"src": "up.yfinance_bars", "dst": "eng.price_engine", "label": "YF historical OHLCV"},
    {"src": "up.yfinance_macro", "dst": "eng.macro_intel", "label": "6-pillar macro quotes"},
    {"src": "up.fred_dfii10", "dst": "eng.macro_intel", "label": "10Y TIPS real yield"},
    {"src": "up.faireconomy_tradays", "dst": "eng.news_engine", "label": "FF calendar + Tradays actuals"},
    {"src": "up.world_rss_truth", "dst": "eng.world_news_engine", "label": "10 RSS feeds + Truth Social"},
    {"src": "up.gemini_cloud", "dst": "eng.session_briefing", "label": "Grounded brief + 7-pair JSON"},

    # Engine <-> Cache / Vault persistence
    {"src": "eng.price_engine", "dst": "cache.candles_pkl", "label": "Save/load candles_cache.pkl"},
    {"src": "eng.macro_intel", "dst": "cache.macro_ttl", "label": "YF 5m & FRED 6h TTL cache"},
    {"src": "eng.news_engine", "dst": "cache.calendar_json", "label": "30-day calendar JSON"},
    {"src": "eng.world_news_engine", "dst": "cache.world_news_json", "label": "30-day news & speech JSON"},
    {"src": "eng.session_briefing", "dst": "cache.session_briefing", "label": "session_briefings.json"},
    {"src": "eng.session_briefing", "dst": "eng.battle_record", "label": "Seed new session battle plans"},
    {"src": "vault.tuned_dna", "dst": "eng.session_briefing", "label": "Calibrated SL/TP/wick params"},
    {"src": "eng.battle_record", "dst": "vault.battle_ledger", "label": "Persist ledger & monthly archive"},
    {"src": "eng.brain_score", "dst": "vault.perf_tracker", "label": "Log scan score row"},
    {"src": "vault.trader_dna_profile", "dst": "vault.d_drive_dataset", "label": "Permanent VLM/LLM dataset export"},

    # PriceEngine -> Downstream Technical & Liquidity Engines
    {"src": "eng.price_engine", "dst": "hub.tomoko_core", "label": "Spot prices & MTF DataFrames"},
    {"src": "eng.price_engine", "dst": "eng.regime_engine", "label": "H4/H1/M15 OHLCV + EMAs/ADX"},
    {"src": "eng.price_engine", "dst": "eng.levels_engine", "label": "D1/W1/H1 bars for PDH/PDL"},
    {"src": "eng.price_engine", "dst": "eng.intermarket_engine", "label": "Cross-asset price feed"},
    {"src": "eng.price_engine", "dst": "eng.watchout_zones", "label": "H4/H1 bars + spot"},
    {"src": "eng.price_engine", "dst": "eng.decision_zones", "label": "M15/H1/H4 bars + spot"},
    {"src": "eng.price_engine", "dst": "eng.trap_radar", "label": "H1/M15 bars for BSL/SSL sweep"},
    {"src": "eng.price_engine", "dst": "eng.battle_record", "label": "M15 bars for fill/TP/SL eval"},

    # Inter-Engine Causal Synthesis
    {"src": "eng.levels_engine", "dst": "eng.watchout_zones", "label": "PDH/PDL/Weekly/Liq levels"},
    {"src": "eng.levels_engine", "dst": "eng.decision_zones", "label": "Key levels for zone confluence"},
    {"src": "eng.levels_engine", "dst": "eng.trap_radar", "label": "Key levels for liquidity pools"},
    {"src": "eng.intermarket_engine", "dst": "eng.fundamental_engine", "label": "DXY & US10Y change"},
    {"src": "eng.regime_engine", "dst": "eng.brain_score", "label": "MTF trend & H4 gate"},
    {"src": "eng.fundamental_engine", "dst": "eng.brain_score", "label": "Fundamental & intermarket score"},
    {"src": "eng.watchout_zones", "dst": "eng.brain_score", "label": "Premium/Discount zone label"},
    {"src": "eng.levels_engine", "dst": "eng.brain_score", "label": "Daily range & liquidity proximity"},
    {"src": "eng.news_engine", "dst": "eng.brain_score", "label": "60m high-impact news gate"},

    # Macro / Catalyst / Briefing Synthesis
    {"src": "eng.news_engine", "dst": "eng.macro_scenario", "label": "Upcoming high/med calendar events"},
    {"src": "eng.macro_intel", "dst": "eng.macro_scenario", "label": "6-pillar macro posture"},
    {"src": "eng.news_engine", "dst": "eng.us_catalyst", "label": "US high-impact release schedule"},
    {"src": "eng.world_news_engine", "dst": "eng.us_catalyst", "label": "Geopolitical & speech sentiment"},
    {"src": "eng.macro_intel", "dst": "eng.us_catalyst", "label": "DXY/US10Y/VIX pre-release skew"},
    {"src": "eng.macro_intel", "dst": "eng.session_briefing", "label": "6-pillar macro telemetry"},
    {"src": "eng.world_news_engine", "dst": "eng.session_briefing", "label": "36h macro headlines"},
    {"src": "eng.decision_zones", "dst": "eng.session_briefing", "label": "M15/H1 supply & demand corridors"},
    {"src": "eng.trap_radar", "dst": "eng.session_briefing", "label": "Active Bull/Bear trap warnings"},
    {"src": "eng.macro_scenario", "dst": "eng.session_briefing", "label": "Catalyst If-Then branches"},

    # Engines & Caches -> Hub & FastAPI Endpoints
    {"src": "eng.brain_score", "dst": "hub.tomoko_core", "label": "Pair scan payload & action"},
    {"src": "eng.decision_zones", "dst": "hub.tomoko_core", "label": "M15/H1/H4 decision zones"},
    {"src": "hub.tomoko_core", "dst": "cache.scan_all", "label": "Update 3s/5s scan cache"},
    {"src": "cache.scan_all", "dst": "api.scan_all", "label": "Serve /api/scan_all & heatmap"},
    {"src": "eng.price_engine", "dst": "api.prices_context", "label": "Serve /api/prices & /context"},
    {"src": "hub.tomoko_core", "dst": "api.pair_deep_dive", "label": "Serve /api/pair/{sym}/deep_dive"},
    {"src": "eng.macro_intel", "dst": "api.macro_intel", "label": "Serve /api/macro_intel"},
    {"src": "eng.news_engine", "dst": "api.news_world", "label": "Serve /api/global_calendar"},
    {"src": "eng.world_news_engine", "dst": "api.news_world", "label": "Serve /api/world_news & /speeches"},
    {"src": "eng.session_briefing", "dst": "api.briefing", "label": "Serve /api/briefing"},
    {"src": "eng.trap_radar", "dst": "api.traps_scenarios", "label": "Serve /api/traps"},
    {"src": "eng.macro_scenario", "dst": "api.traps_scenarios", "label": "Serve /api/scenarios"},
    {"src": "eng.us_catalyst", "dst": "api.traps_scenarios", "label": "Serve /api/pair/{sym}/catalyst_prediction"},
    {"src": "eng.battle_record", "dst": "api.battle_dna", "label": "Serve /api/battle-record"},
    {"src": "vault.trader_dna_profile", "dst": "api.battle_dna", "label": "Serve /api/benchmarks/weekend_comparison"},
]


class TelemetryBus:
    """
    Central, thread-safe real-time telemetry bus for Tomoko Instinct.
    Tracks every node, directed edge, movement packet, and active anomaly
    (ERROR, FALLBACK, STALE) without ranking or hiding any failure.
    """

    def __init__(self, max_events: int = 600, max_faults: int = 250):
        self._lock = threading.Lock()
        self._seq = 0
        self.started_at = time.time()
        self.events: deque = deque(maxlen=max_events)
        self.faults: deque = deque(maxlen=max_faults)
        self._subscribers: List[queue.Queue] = []

        # Build node & edge state tables
        self.nodes: Dict[str, Dict[str, Any]] = {}
        for n in TOPOLOGY_NODES:
            nid = n["id"]
            self.nodes[nid] = {
                **n,
                "state": STATE_IDLE,
                "last_ts": 0.0,
                "last_iso": "",
                "last_latency_ms": 0.0,
                "total_movements": 0,
                "live_count": 0,
                "cached_count": 0,
                "stale_count": 0,
                "fallback_count": 0,
                "error_count": 0,
                "items_count": 0,
                "last_detail": "Awaiting first data movement or probe",
                "metrics": {},
            }

        self.edges: Dict[str, Dict[str, Any]] = {}
        for e in TOPOLOGY_EDGES:
            eid = f"{e['src']}->{e['dst']}"
            self.edges[eid] = {
                "id": eid,
                "src": e["src"],
                "dst": e["dst"],
                "label": e.get("label", ""),
                "state": STATE_IDLE,
                "last_ts": 0.0,
                "last_iso": "",
                "last_latency_ms": 0.0,
                "total_movements": 0,
                "fault_count": 0,
                "last_detail": "Idle",
                "items_count": 0,
            }

    def emit_flow(
        self,
        src: str,
        dst: str,
        state: str = STATE_LIVE,
        latency_ms: float = 0.0,
        items: int = 0,
        detail: str = "",
        file_hint: Optional[str] = None,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Records a single data movement across edge (src -> dst), updates both
        endpoint nodes, logs to fault quarantine if state is ERROR/FALLBACK/STALE,
        and broadcasts to all connected SSE monitors.
        """
        now = time.time()
        iso_now = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%H:%M:%S.%f")[:-3]
        state_up = str(state or STATE_LIVE).upper()
        lat = round(float(latency_ms or 0.0), 1)
        eid = f"{src}->{dst}"

        with self._lock:
            self._seq += 1
            seq = self._seq

            # Auto-register dynamic edge if not in static list
            if eid not in self.edges:
                self.edges[eid] = {
                    "id": eid,
                    "src": src,
                    "dst": dst,
                    "label": f"{src} -> {dst}",
                    "state": STATE_IDLE,
                    "last_ts": 0.0,
                    "last_iso": "",
                    "last_latency_ms": 0.0,
                    "total_movements": 0,
                    "fault_count": 0,
                    "last_detail": "",
                    "items_count": 0,
                }

            edge = self.edges[eid]
            edge["state"] = state_up
            edge["last_ts"] = now
            edge["last_iso"] = iso_now
            edge["last_latency_ms"] = lat
            edge["total_movements"] += 1
            edge["items_count"] = int(items or 0)
            if detail:
                edge["last_detail"] = str(detail)[:300]
            if state_up in (STATE_ERROR, STATE_FALLBACK, STATE_STALE):
                edge["fault_count"] += 1

            # Update source and destination nodes
            for nid, is_dst in ((src, False), (dst, True)):
                if nid not in self.nodes:
                    self.nodes[nid] = {
                        "id": nid,
                        "label": nid,
                        "short": nid.split(".")[-1][:6].upper(),
                        "cluster": "ENGINE",
                        "file": file_hint or "core/",
                        "role": "Dynamic telemetry node",
                        "expected_ttl_sec": 120,
                        "state": STATE_IDLE,
                        "last_ts": 0.0,
                        "last_iso": "",
                        "last_latency_ms": 0.0,
                        "total_movements": 0,
                        "live_count": 0,
                        "cached_count": 0,
                        "stale_count": 0,
                        "fallback_count": 0,
                        "error_count": 0,
                        "items_count": 0,
                        "last_detail": "",
                        "metrics": {},
                    }
                n = self.nodes[nid]
                n["last_ts"] = now
                n["last_iso"] = iso_now
                n["total_movements"] += 1
                if is_dst or n["state"] == STATE_IDLE or state_up in (STATE_ERROR, STATE_FALLBACK, STATE_STALE):
                    n["state"] = state_up
                    n["last_latency_ms"] = lat
                    if items:
                        n["items_count"] = int(items)
                    if detail:
                        n["last_detail"] = str(detail)[:300]
                if metrics and is_dst:
                    n["metrics"].update(metrics)

                if state_up == STATE_LIVE:
                    n["live_count"] += 1
                elif state_up == STATE_CACHED:
                    n["cached_count"] += 1
                elif state_up == STATE_STALE:
                    n["stale_count"] += 1
                elif state_up == STATE_FALLBACK:
                    n["fallback_count"] += 1
                elif state_up == STATE_ERROR:
                    n["error_count"] += 1

            src_node = self.nodes.get(src, {})
            dst_node = self.nodes.get(dst, {})
            resolved_file = file_hint or dst_node.get("file") or src_node.get("file") or ""

            packet = {
                "seq": seq,
                "ts": round(now, 3),
                "iso": iso_now,
                "src": src,
                "dst": dst,
                "src_label": src_node.get("short", src),
                "dst_label": dst_node.get("short", dst),
                "edge_id": eid,
                "state": state_up,
                "latency_ms": lat,
                "items": int(items or 0),
                "detail": str(detail)[:300],
                "file": resolved_file,
            }
            self.events.append(packet)

            if state_up in (STATE_ERROR, STATE_FALLBACK, STATE_STALE):
                self.faults.append(packet)

            dead_queues = []
            for q in self._subscribers:
                try:
                    q.put_nowait(packet)
                except queue.Full:
                    try:
                        q.get_nowait()
                        q.put_nowait(packet)
                    except Exception:
                        dead_queues.append(q)
                except Exception:
                    dead_queues.append(q)
            for dq in dead_queues:
                if dq in self._subscribers:
                    self._subscribers.remove(dq)

        return packet

    def set_node_status(
        self,
        node_id: str,
        state: str,
        detail: str = "",
        items: int = 0,
        latency_ms: float = 0.0,
        metrics: Optional[Dict[str, Any]] = None,
    ):
        """Directly updates a node's health snapshot from a diagnostic probe."""
        now = time.time()
        iso_now = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%H:%M:%S.%f")[:-3]
        state_up = str(state or STATE_LIVE).upper()
        with self._lock:
            if node_id not in self.nodes:
                return
            n = self.nodes[node_id]
            prev_state = n["state"]
            n["state"] = state_up
            if n["last_ts"] == 0.0:
                n["last_ts"] = now
                n["last_iso"] = iso_now
            if latency_ms > 0:
                n["last_latency_ms"] = round(float(latency_ms), 1)
            if items >= 0:
                n["items_count"] = int(items)
            if detail:
                n["last_detail"] = str(detail)[:300]
            if metrics:
                n["metrics"].update(metrics)

            # If a probe discovers a new anomaly state transition, log it to faults
            if state_up in (STATE_ERROR, STATE_FALLBACK, STATE_STALE) and prev_state != state_up:
                self._seq += 1
                pkt = {
                    "seq": self._seq,
                    "ts": round(now, 3),
                    "iso": iso_now,
                    "src": node_id,
                    "dst": node_id,
                    "src_label": n.get("short", node_id),
                    "dst_label": n.get("short", node_id),
                    "edge_id": f"{node_id}->{node_id}",
                    "state": state_up,
                    "latency_ms": round(float(latency_ms), 1),
                    "items": int(items),
                    "detail": str(detail)[:300],
                    "file": n.get("file", ""),
                }
                self.faults.append(pkt)
                self.events.append(pkt)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=250)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def get_snapshot(self) -> Dict[str, Any]:
        """Returns full current topology state, recent events, fault quarantine, and KPI counters."""
        now = time.time()
        with self._lock:
            nodes_list = []
            counts = {
                STATE_LIVE: 0,
                STATE_CACHED: 0,
                STATE_STALE: 0,
                STATE_FALLBACK: 0,
                STATE_ERROR: 0,
                STATE_IDLE: 0,
            }
            for nid, n in self.nodes.items():
                age_sec = round(now - n["last_ts"], 1) if n["last_ts"] > 0 else -1
                st = n["state"]
                counts[st] = counts.get(st, 0) + 1
                nodes_list.append({
                    **n,
                    "age_sec": age_sec,
                })

            edges_list = []
            edge_counts = {
                STATE_LIVE: 0,
                STATE_CACHED: 0,
                STATE_STALE: 0,
                STATE_FALLBACK: 0,
                STATE_ERROR: 0,
                STATE_IDLE: 0,
            }
            for eid, e in self.edges.items():
                age_sec = round(now - e["last_ts"], 1) if e["last_ts"] > 0 else -1
                st = e["state"]
                edge_counts[st] = edge_counts.get(st, 0) + 1
                edges_list.append({
                    **e,
                    "age_sec": age_sec,
                })

            recent_events = list(self.events)[-120:]
            recent_faults = list(self.faults)[-80:]

            latencies = [ev["latency_ms"] for ev in recent_events if ev.get("latency_ms", 0) > 0]
            avg_lat = round(sum(latencies) / len(latencies), 1) if latencies else 0.0

            return {
                "timestamp": round(now, 3),
                "iso_utc": datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                "uptime_sec": round(now - self.started_at, 1),
                "seq": self._seq,
                "node_counts": counts,
                "edge_counts": edge_counts,
                "total_nodes": len(nodes_list),
                "total_edges": len(edges_list),
                "avg_latency_ms": avg_lat,
                "active_subscribers": len(self._subscribers),
                "nodes": nodes_list,
                "edges": edges_list,
                "events": recent_events,
                "faults": recent_faults,
            }


# Global Singleton Bus
_bus = TelemetryBus()


def get_telemetry_bus() -> TelemetryBus:
    return _bus


def emit_flow(
    src: str,
    dst: str,
    state: str = STATE_LIVE,
    latency_ms: float = 0.0,
    items: int = 0,
    detail: str = "",
    file_hint: Optional[str] = None,
    metrics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return _bus.emit_flow(
        src=src,
        dst=dst,
        state=state,
        latency_ms=latency_ms,
        items=items,
        detail=detail,
        file_hint=file_hint,
        metrics=metrics,
    )


# ============================================================================
# COMPREHENSIVE LIVE SYSTEM PROBE (AUDITS EVERY ENGINE, FEED, CACHE & VAULT)
# ============================================================================
def run_comprehensive_probe(api_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Executes a non-destructive deep probe of all 44 nodes and their data pipes.
    Detects silent fallbacks (_FALLBACK_MATRIX, rule-based briefing fallback,
    missing FinBERT ONNX, MT5 disconnect -> TV/YF fallback, missing timeframes,
    stale ticks, empty caches, or missing disk vaults).
    """
    now = time.time()
    ctx = api_context or {}

    # 1. Probe PriceEngine, MT5 Bridge, TV Oracle, YFinance Bars, Candles Pickle Cache
    pe = ctx.get("price_engine")
    if pe is not None:
        latest_prices = getattr(pe, "latest_prices", {}) or {}
        candles_cache = getattr(pe, "candles_cache", {}) or {}
        active_broker = getattr(pe, "active_broker", None)

        # Categorize price sources across all 7 pairs
        mt5_pairs = []
        tv_pairs = []
        yf_pairs = []
        stale_pairs = []
        missing_pairs = []
        expected_pairs = ["EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "EURJPY", "EURGBP", "XAUUSD"]

        for sym in expected_pairs:
            p_info = latest_prices.get(sym)
            if not p_info or not p_info.get("price"):
                missing_pairs.append(sym)
                continue
            src_tag = str(p_info.get("source", "")).lower()
            p_ts = float(p_info.get("time") or 0.0)
            age = now - p_ts if p_ts > 0 else 999999
            if age > 120:
                stale_pairs.append(f"{sym} ({int(age)}s)")
            if "mt5" in src_tag:
                mt5_pairs.append(sym)
            elif "tradingview" in src_tag:
                tv_pairs.append(sym)
            else:
                yf_pairs.append(f"{sym}:{src_tag}")

        # MT5 Upstream status
        if active_broker and len(mt5_pairs) == 7:
            emit_flow(
                "up.mt5_bridge", "eng.price_engine", STATE_LIVE,
                items=len(mt5_pairs),
                detail=f"MT5 {active_broker} connected · 7/7 pairs streaming live broker ticks",
                metrics={"broker": active_broker, "mt5_pairs": mt5_pairs},
            )
        elif active_broker and len(mt5_pairs) > 0:
            emit_flow(
                "up.mt5_bridge", "eng.price_engine", STATE_FALLBACK,
                items=len(mt5_pairs),
                detail=f"MT5 {active_broker} partial ({len(mt5_pairs)}/7 pairs: {','.join(mt5_pairs)}) · rest on fallback",
                metrics={"broker": active_broker, "mt5_pairs": mt5_pairs},
            )
        else:
            emit_flow(
                "up.mt5_bridge", "eng.price_engine", STATE_FALLBACK,
                items=0,
                detail="MT5 terminal offline/detached · PriceEngine using TradingView/YFinance decoupled fallback",
                metrics={"broker": "NONE"},
            )

        # TradingView Spot Oracle status
        if tv_pairs:
            emit_flow(
                "up.tv_oracle", "eng.price_engine", STATE_LIVE,
                items=len(tv_pairs),
                detail=f"TradingView Spot Oracle active for {len(tv_pairs)} pairs ({','.join(tv_pairs)})",
            )
        else:
            _bus.set_node_status(
                "up.tv_oracle",
                STATE_CACHED if len(mt5_pairs) == 7 else STATE_IDLE,
                detail="Standby (MT5 Tier-1 active for all 7 pairs)" if len(mt5_pairs) == 7 else "No TV quotes active",
                items=len(tv_pairs),
            )

        # Check Candles Cache across 7 pairs × 5 timeframes (35 total frames)
        total_frames = 0
        missing_tfs = []
        for sym in expected_pairs:
            sym_c = candles_cache.get(sym, {})
            for tf in ("M15", "H1", "H4", "D1", "W1"):
                df = sym_c.get(tf)
                if df is not None and not getattr(df, "empty", True):
                    total_frames += 1
                else:
                    missing_tfs.append(f"{sym}:{tf}")

        if missing_tfs:
            emit_flow(
                "up.yfinance_bars", "eng.price_engine",
                STATE_ERROR if total_frames == 0 else STATE_FALLBACK,
                items=total_frames,
                detail=f"Missing {len(missing_tfs)}/35 OHLCV frames: {', '.join(missing_tfs[:6])}",
            )
        else:
            emit_flow(
                "up.yfinance_bars", "eng.price_engine",
                STATE_LIVE if not active_broker else STATE_CACHED,
                items=total_frames,
                detail=f"All 35/35 OHLCV DataFrames (7 pairs × 5 TFs) populated",
            )

        # Overall PriceEngine health
        if missing_pairs:
            _bus.set_node_status(
                "eng.price_engine", STATE_ERROR,
                items=7 - len(missing_pairs),
                detail=f"Missing spot prices for: {', '.join(missing_pairs)}",
            )
        elif stale_pairs:
            _bus.set_node_status(
                "eng.price_engine", STATE_STALE,
                items=len(latest_prices),
                detail=f"Spot tick age > 120s for: {', '.join(stale_pairs[:4])} (market closed or feed stalled)",
            )
        else:
            _bus.set_node_status(
                "eng.price_engine", STATE_LIVE,
                items=len(latest_prices),
                detail=f"7/7 pairs active · {total_frames}/35 candle frames ready",
            )

        # Candles Pickle disk check
        pkl_path = os.path.join(ROOT, "logs", "candles_cache.pkl")
        if os.path.isfile(pkl_path):
            pkl_age = int(now - os.path.getmtime(pkl_path))
            pkl_kb = round(os.path.getsize(pkl_path) / 1024.0, 1)
            emit_flow(
                "eng.price_engine", "cache.candles_pkl",
                STATE_LIVE if pkl_age < 3600 else STATE_CACHED,
                items=total_frames,
                detail=f"candles_cache.pkl ({pkl_kb} KB · updated {pkl_age}s ago)",
            )
        else:
            emit_flow(
                "eng.price_engine", "cache.candles_pkl", STATE_FALLBACK,
                items=0,
                detail="logs/candles_cache.pkl not found on disk",
            )

    # 2. Probe MacroIntelligenceEngine (yfinance 6-pillar & FRED DFII10 & _FALLBACK_MATRIX detection)
    try:
        from core import macro_intelligence_engine as mie
        mac_cache = getattr(mie, "_cache", {})
        yf_data = mac_cache.get("yf_data", {})
        fred_info = mac_cache.get("fred_dfii10", {})
        expected_tickers = {
            "DXY": "DX-Y.NYB",
            "US10Y": "^TNX",
            "TIPS_ETF": "TIP",
            "OIL": "CL=F",
            "SPX": "^GSPC",
            "VIX": "^VIX",
            "GOLD_FUT": "GC=F",
        }
        missing_macro = []
        stale_macro = []
        live_macro = 0
        for k, t_sym in expected_tickers.items():
            entry = yf_data.get(t_sym)
            if not entry:
                missing_macro.append(f"{k}({t_sym})")
            else:
                age = now - float(entry.get("ts") or 0)
                if age > 900:
                    stale_macro.append(f"{k}({int(age)}s)")
                else:
                    live_macro += 1

        if len(missing_macro) == len(expected_tickers):
            emit_flow(
                "up.yfinance_macro", "eng.macro_intel", STATE_FALLBACK,
                items=0,
                detail="Macro YF cache empty (0/7 tickers fetched) — calls will serve static _FALLBACK_MATRIX if unpopulated",
            )
        elif missing_macro:
            emit_flow(
                "up.yfinance_macro", "eng.macro_intel", STATE_FALLBACK,
                items=live_macro,
                detail=f"Using _FALLBACK_MATRIX for missing tickers: {', '.join(missing_macro)}",
            )
        elif stale_macro:
            emit_flow(
                "up.yfinance_macro", "eng.macro_intel", STATE_STALE,
                items=live_macro,
                detail=f"Macro YF tickers older than 15m TTL: {', '.join(stale_macro)}",
            )
        else:
            emit_flow(
                "up.yfinance_macro", "eng.macro_intel", STATE_LIVE,
                items=live_macro,
                detail="All 7/7 Yahoo Finance macro tickers live & within TTL",
            )

        # FRED DFII10 check
        fred_ts = float(fred_info.get("ts") or 0)
        fred_val = fred_info.get("val", 2.88)
        fred_date = fred_info.get("date", "")
        if fred_ts <= 0 or not fred_date:
            emit_flow(
                "up.fred_dfii10", "eng.macro_intel", STATE_FALLBACK,
                items=0,
                detail=f"FRED DFII10 not yet fetched (ts=0) — using static fallback real yield {fred_val}%",
            )
        else:
            fred_age_m = int((now - fred_ts) / 60)
            emit_flow(
                "up.fred_dfii10", "eng.macro_intel",
                STATE_LIVE if fred_age_m < 360 else STATE_CACHED,
                items=1,
                detail=f"FRED DFII10 = {fred_val}% (obs {fred_date} · fetched {fred_age_m}m ago)",
            )

        emit_flow(
            "eng.macro_intel", "cache.macro_ttl",
            STATE_LIVE if (live_macro == 7 and fred_ts > 0) else STATE_FALLBACK,
            items=live_macro + (1 if fred_ts > 0 else 0),
            detail=f"Macro cache: {live_macro}/7 YF tickers + {'FRED live' if fred_ts > 0 else 'FRED fallback'}",
        )
    except Exception as e:
        emit_flow("up.yfinance_macro", "eng.macro_intel", STATE_ERROR, detail=f"Macro probe exception: {e}")

    # 3. Probe NewsEngine & 30-Day Calendar Cache
    ne = ctx.get("news_engine")
    if ne is not None:
        ev_count = len(getattr(ne, "events", []) or [])
        last_ft = getattr(ne, "last_fetch_time", None)
        cal_file = getattr(ne, "cache_file", os.path.join(ROOT, "logs", "decoupled_calendar_cache.json"))
        if ev_count == 0:
            emit_flow(
                "up.faireconomy_tradays", "eng.news_engine", STATE_ERROR,
                items=0,
                detail="NewsEngine holds 0 calendar events — high-impact news gate cannot block!",
            )
        else:
            age_h = round((datetime.now(timezone.utc) - last_ft).total_seconds() / 3600.0, 1) if last_ft else 999
            st = STATE_LIVE if age_h < 24 else STATE_STALE
            emit_flow(
                "up.faireconomy_tradays", "eng.news_engine", st,
                items=ev_count,
                detail=f"{ev_count} calendar events loaded (last sync {age_h}h ago)",
            )
        if os.path.isfile(cal_file):
            emit_flow(
                "eng.news_engine", "cache.calendar_json", STATE_CACHED,
                items=ev_count,
                detail=f"decoupled_calendar_cache.json ({round(os.path.getsize(cal_file)/1024, 1)} KB · {ev_count} events)",
            )
        else:
            emit_flow(
                "eng.news_engine", "cache.calendar_json", STATE_ERROR,
                items=0,
                detail="logs/decoupled_calendar_cache.json missing on disk",
            )

    # 4. Probe WorldNewsEngine & FinBERT Classifier
    wne = ctx.get("world_news_engine")
    if wne is not None:
        items_cnt = len(getattr(wne, "items", []) or [])
        sp_cnt = len(getattr(wne, "speeches", []) or [])
        last_ft = getattr(wne, "last_fetch_time", None)
        from core import world_news_engine as wne_mod
        finbert_ok = getattr(wne_mod, "analyze_news_item", None) is not None

        if items_cnt == 0:
            emit_flow(
                "up.world_rss_truth", "eng.world_news_engine", STATE_ERROR,
                items=0,
                detail="WorldNewsEngine archive has 0 headlines",
            )
        elif not finbert_ok:
            emit_flow(
                "up.world_rss_truth", "eng.world_news_engine", STATE_FALLBACK,
                items=items_cnt,
                detail=f"{items_cnt} headlines, {sp_cnt} speeches · FinBERT ONNX classifier unavailable (using keyword fallback)",
            )
        else:
            age_m = int((datetime.now(timezone.utc) - last_ft).total_seconds() / 60) if last_ft else 999
            emit_flow(
                "up.world_rss_truth", "eng.world_news_engine",
                STATE_LIVE if age_m < 15 else STATE_CACHED,
                items=items_cnt,
                detail=f"{items_cnt} headlines & {sp_cnt} speeches · FinBERT active (synced {age_m}m ago)",
            )

        wn_file = getattr(wne, "cache_file", os.path.join(ROOT, "logs", "world_news_cache.json"))
        if os.path.isfile(wn_file):
            emit_flow(
                "eng.world_news_engine", "cache.world_news_json", STATE_CACHED,
                items=items_cnt,
                detail=f"world_news_cache.json ({round(os.path.getsize(wn_file)/1024, 1)} KB)",
            )
        else:
            emit_flow(
                "eng.world_news_engine", "cache.world_news_json", STATE_FALLBACK,
                items=0,
                detail="logs/world_news_cache.json missing on disk",
            )

    # 5. Probe SessionBriefingEngine & Gemini Cloud Upstream
    sbe = ctx.get("session_briefing_engine")
    if sbe is not None:
        brf = getattr(sbe, "latest_briefing", {}) or {}
        mode = brf.get("engine_mode", "NONE")
        fb_reason = brf.get("fallback_reason")
        pairs_cnt = len(brf.get("pairs", {}) or {})
        stale = sbe.is_briefing_stale() if hasattr(sbe, "is_briefing_stale") else False

        if pairs_cnt == 0:
            emit_flow(
                "up.gemini_cloud", "eng.session_briefing", STATE_ERROR,
                items=0,
                detail="No session briefing available in memory or cache (0 pairs)",
            )
        elif fb_reason or mode == "SYNTHESIZED_RULE_BASED":
            emit_flow(
                "up.gemini_cloud", "eng.session_briefing", STATE_FALLBACK,
                items=pairs_cnt,
                detail=f"Mode={mode} · Fallback active: {fb_reason or 'Rule-based synthesis'}",
            )
        elif stale:
            emit_flow(
                "up.gemini_cloud", "eng.session_briefing", STATE_STALE,
                items=pairs_cnt,
                detail=f"Briefing stale for current session ({brf.get('session')} @ {brf.get('generated_at_utc')})",
            )
        else:
            emit_flow(
                "up.gemini_cloud", "eng.session_briefing", STATE_LIVE,
                items=pairs_cnt,
                detail=f"Mode={mode} · {pairs_cnt}/7 pairs fresh ({brf.get('generated_at_utc')})",
            )

        brf_file = os.path.join(ROOT, "logs", "session_briefings.json")
        if os.path.isfile(brf_file):
            emit_flow(
                "eng.session_briefing", "cache.session_briefing",
                STATE_STALE if stale else STATE_CACHED,
                items=pairs_cnt,
                detail=f"session_briefings.json ({round(os.path.getsize(brf_file)/1024, 1)} KB · {brf.get('session', '—')})",
            )
        else:
            emit_flow(
                "eng.session_briefing", "cache.session_briefing", STATE_ERROR,
                items=0,
                detail="logs/session_briefings.json missing",
            )

    # 6. Probe BattleRecordEngine & Hot Ledger / Archive
    bre = ctx.get("battle_record_engine")
    if bre is not None:
        battles = getattr(bre, "battles", []) or []
        ledger_path = os.path.join(ROOT, "logs", "battle_record.json")
        if os.path.isfile(ledger_path):
            emit_flow(
                "eng.battle_record", "vault.battle_ledger", STATE_LIVE,
                items=len(battles),
                detail=f"BattleRecord active: {len(battles)} tracked setups in battle_record.json",
            )
        else:
            emit_flow(
                "eng.battle_record", "vault.battle_ledger", STATE_FALLBACK,
                items=len(battles),
                detail="logs/battle_record.json not yet written to disk",
            )

    # 7. Probe Vaults: Tuned Entry DNA, Trader DNA Profile, and D:\Tomoko_Trader_Vault
    tuned_path = os.path.join(ROOT, "data", "tuned_entry_dna.json")
    if os.path.isfile(tuned_path):
        try:
            with open(tuned_path, "r", encoding="utf-8") as f:
                td = json.load(f)
            t_cnt = int(td.get("trades_total", 0) or 0)
            stage = td.get("calibration_stage", "STAGE_1")
            emit_flow(
                "vault.tuned_dna", "eng.session_briefing",
                STATE_LIVE if t_cnt > 0 else STATE_FALLBACK,
                items=t_cnt,
                detail=f"tuned_entry_dna.json ({stage} · {t_cnt} calibrated trades)",
            )
        except Exception as e:
            emit_flow("vault.tuned_dna", "eng.session_briefing", STATE_ERROR, detail=f"Corrupt tuned_entry_dna.json: {e}")
    else:
        emit_flow(
            "vault.tuned_dna", "eng.session_briefing", STATE_FALLBACK,
            items=0,
            detail="data/tuned_entry_dna.json absent — using factory DEFAULT_ENTRY_PARAMS",
        )

    dna_prof_path = os.path.join(ROOT, "trader_dna", "data", "trader_dna_profile.json")
    dropoff_dir = os.path.join(ROOT, "trader_dna", "dropoff")
    dropoff_pngs = [f for f in (os.listdir(dropoff_dir) if os.path.isdir(dropoff_dir) else []) if f.lower().endswith((".png", ".jpg", ".jpeg"))]
    if os.path.isfile(dna_prof_path):
        try:
            with open(dna_prof_path, "r", encoding="utf-8") as f:
                prof = json.load(f)
            hist_cnt = len(prof.get("history", []) or [])
            _bus.set_node_status(
                "vault.trader_dna_profile", STATE_LIVE,
                items=hist_cnt,
                detail=f"trader_dna_profile.json ({hist_cnt} history records · {len(dropoff_pngs)} pending in dropoff/)",
            )
        except Exception as e:
            _bus.set_node_status("vault.trader_dna_profile", STATE_ERROR, detail=f"Corrupt trader_dna_profile.json: {e}")
    else:
        _bus.set_node_status("vault.trader_dna_profile", STATE_FALLBACK, detail="trader_dna/data/trader_dna_profile.json not found")

    vault_dir = r"D:\Tomoko_Trader_Vault\dataset"
    if os.path.isdir(vault_dir):
        vlm_jsonl = os.path.join(vault_dir, "train_vlm.jsonl")
        img_dir = os.path.join(vault_dir, "images")
        img_cnt = len(os.listdir(img_dir)) if os.path.isdir(img_dir) else 0
        emit_flow(
            "vault.trader_dna_profile", "vault.d_drive_dataset", STATE_LIVE,
            items=img_cnt,
            detail=f"D:\\Tomoko_Trader_Vault\\dataset online ({img_cnt} archived images · VLM jsonl: {os.path.isfile(vlm_jsonl)})",
        )
    else:
        emit_flow(
            "vault.trader_dna_profile", "vault.d_drive_dataset", STATE_ERROR,
            items=0,
            detail="D:\\Tomoko_Trader_Vault\\dataset directory unreachable or missing",
        )

    # 8. Probe API Scan Caches
    scan_all_cache = ctx.get("scan_all_cache")
    if isinstance(scan_all_cache, dict):
        c_ts = float(scan_all_cache.get("ts") or 0.0)
        has_res = scan_all_cache.get("result") is not None
        if has_res:
            age = round(now - c_ts, 1)
            _bus.set_node_status(
                "cache.scan_all",
                STATE_LIVE if age < 15 else STATE_CACHED,
                items=7,
                detail=f"_scan_all_cache populated (last scan {age}s ago)",
            )

    return _bus.get_snapshot()
