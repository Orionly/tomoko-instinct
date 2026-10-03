# PROJECT_BRAIN.md — Tomoko Instinct V4.4 Block 3

*Generated: 2026-10-03 | Analysis of `C:\Users\USER\Documents\Tomoko`*

---

## 1. Project Overview

**Tomoko** is a **manual-first trading decision support system** ("Brain = Information, Bot = Hands & Feet") for a micro-account (~$5.67 / 567 USC) on VT Markets MT5. It does **not auto-trade** — it scans 7 pairs (EURUSD, GBPUSD, USDJPY, GBPJPY, EURJPY, EURGBP, XAUUSD) and outputs a **Brain Score (0–100)** + **actionable guidance** (WAIT, LOOK, LIMIT AT LIQ) via:

- **Web Dashboard** (FastAPI + vanilla JS, port 8000) — primary UI
- **Desktop Dashboard** (CustomTkinter) — legacy Tkinter app
- **EA Bridge** (raw TCP socket, port 18001) — receives live ticks/calendar from a custom MT5 EA ("TomokoDataPump")

**Core philosophy**: "No brain = blowout." Every signal is **decision support only**; the human confirms/logging via Journal (3 trades/day max, 1 open position).

---

## 2. Tech Stack + Versions

| Layer | Technology | Version |
|-------|------------|---------|
| Language | Python | 3.13.6 (system, no venv) |
| MT5 Bridge | MetaTrader5 | 5.0.45 |
| Web API | FastAPI + Uvicorn | (latest via pip) |
| Data | pandas, numpy | 2.2.2 / 1.26.4 |
| Macro Proxies | yfinance | 0.2.40 |
| Desktop UI | customtkinter, CTkTable | 5.2.2 / 0.1.0 |
| HTTP | requests | 2.32.3 |
| Env | python-dotenv | 1.0.1 |
| OS/Terminal | Windows 10/11, MT5 terminal (VT Markets) | `C:\Program Files\VT Markets (Pty) MT5 Terminal\terminal64.exe` |

**No database** — CSV/JSON logs only (`logs/brain_signals.csv`, `journal/trades.json|csv`).

---

## 3. Folder Structure Map

```
Tomoko/
├── Main.py                      # 10 KB — ONE TRUE ORCHESTRATOR (supervisor, pre-flight, fail-fast)
├── config.py                    # 2 KB — All constants (pairs, risk, intermarket, news, journal)
├── Requirements.txt             # 8 deps
├── .gitignore                   # Ignores __pycache__, logs, csv, venv, .vscode, recovered files
├── FIX_REPORT.md                # Fix Agent report: Main.py became true orchestrator
├── RECOVERY_REPORT.md           # Forensic: Main.py was never 0 bytes; dashboard runs from Tomoko.bat
├── audit_for_meta.txt           # Full audit dump for Meta AI review
├── start_tomoko.bat             # 32 B — calls Tomoko.bat (legacy)
├── run_tomoko.bat               # 181 B — launches desktop Tkinter app via python Main.py (OLD)
├── START_HERE_OLD.bat           # 1.5 KB — renamed Tomoko.bat (old no-supervisor launcher)
├── Tomoko.sh                    # 8 lines — Linux launcher (uvicorn only)
├── create_shortcut.ps1          # Creates desktop shortcut to launch_hidden.vbs
├── launch_hidden.vbs            # Runs run_tomoko.bat hidden
├── desktop_legacy.py            # 461 B — OLD Tkinter launcher (renamed from Main.py)
├── web_legacy.py                # 834 B — OLD simple uvicorn launcher (renamed from Tomoko.py)
├── test_mt5.py                  # Vibe test: connect MT5, print rates/indicators
├── Tomoko-Audit-Generator-FIXED.py
├── journal/
│   ├── trades.json              # [] (empty array)
│   └── trades.csv               # Header only
├── logs/
│   └── brain_signals.csv        # ~10 MB, actively growing (PerformanceTracker)
├── ui/
│   ├── dashboard.py             # 26 KB — CustomTkinter desktop app (PairCard, deep dive modal, journal)
│   ├── web_dashboard.html       # 38 KB — Web UI (grid, market pulse, global calendar, heatmap)
│   ├── pair.html                # 25 KB — Per-pair deep dive page
│   └── __init__.py
├── backend/
│   ├── api.py                   # 15 KB — FastAPI app: /api/scan_all, /api/context, /api/prices, /api/market_heatmap, /api/global_calendar, /api/logs, /pair/{symbol}, /api/pair/{symbol}/deep_dive
│   └── static/
│       ├── web_dashboard.html   # Copy of ui/web_dashboard.html
│       └── pair.html            # Copy of ui/pair.html
├── core/
│   ├── __init__.py              # Empty
│   ├── mt5_bridge.py            # 32 KB — MT5Bridge + EA socket server (port 18001), calendar/price file bridge
│   ├── regime_engine.py         # 4.4 KB — RegimeEngine: H4 gate (ADX>22, ATRx>0.8), H1 pullback, M15 bias
│   ├── levels_engine.py         # 774 B — LevelsEngine: daily high/low, weekly open, liq high/low = daily ±0.3 ATR
│   ├── brain_score.py           # 2.7 KB — Weighted score: structural 30% + volatility 20% + levels 20% + intermarket 15% + news 15%
│   ├── fundamental_engine.py    # 3 KB — FundamentalEngine: rate diff (FRED live + fallback), gold bias (DXY/US10Y)
│   ├── news_engine.py           # NewsEngine: EA calendar only (single news gate, no external feeds)
│   ├── intermarket_engine.py    # 3 KB — IntermarketEngine: yfinance proxies (DXY, US10Y, VIX, SPX, OIL, GOLD) cached 300s
│   ├── context_feed.py          # 5.5 KB — ContextFeed: macro snapshot + risk sentiment (VIX/SPX/DXY) + session detection
│   ├── watchout_zones.py        # 3 KB — Watchout zones (discount/premium areas, NOT signals); XAU wider
│   ├── risk_engine.py           # 446 B — RiskEngine: SL/TP multipliers (XAU 1.8/3.6, FX 1.2/2.4), lot 0.01
│   ├── performance_tracker.py   # 1.4 KB — CSV logger for /api/scan results (QA)
│   └── journal.py               # 3 KB — Journal: JSON+CSV trade log, enforces 3/day, 1 open pos
├── strategies/
│   ├── __init__.py
│   ├── base_strategy.py         # 74 B — BaseStrategy (returns None)
│   ├── trend_following_v2.py    # 526 B — TrendFollowingV2: H4 OPEN + ADX>22 + pullback<=0.5 ATR + score>70
│   └── liquidity_sweep.py       # 799 B — LiquiditySweep: H4 OPEN + ADX>40 + dist>1.2 ATR + score>70 → LIMIT AT LIQ
└── scripts/
    └── guard.py                 # 2.5 KB — safe_write(): blocks undersized writes to critical files
```

---

## 4. Key Files & Responsibilities

| File | Role |
|------|------|
| **Main.py** | Supervisor: pre-flight size checks → launches `core/mt5_bridge.py` (port 18001) + `uvicorn backend.api:app` (port 8000) → health check (3s) → supervisor loop (3s) → fail-fast on any child death → graceful Ctrl+C → auto-opens browser |
| **backend/api.py** | FastAPI singleton: global `MT5Bridge` + `ContextFeed`; endpoints for scan, context, prices, heatmap, calendar, logs; 5s cache on heatmap |
| **core/mt5_bridge.py** | MT5Bridge (rates, indicators, symbol mapping), EA socket server (quotes + calendar), file-bridge calendar/prices (`%APPDATA%\MetaQuotes\Terminal\Common\Files\tomoko_calendar.json`), low-level news filter, market heatmap, currency strength. Logging-based (no stdout on connect). |
| **core/regime_engine.py** | Multi-timeframe regime: H4 gate (ADX>22, ATR ratio>0.8), H1 pullback (dist EMA21 ≤0.5 ATR), M15 bias, alignment flag |
| **core/brain_score.py** | `calculate_brain_score()` weighted sum; `get_manual_action()` gates by news_clean, liq rule, score thresholds |
| **core/fundamental_engine.py** | Rate differential (FRED live if `FRED_API_KEY` set, else fallback); Gold bias from DXY/US10Y |
| **core/watchout_zones.py** | Discount/premium watch areas from daily range, EMA21, liquidity levels — labeled "NOT A SIGNAL" |
| **core/context_feed.py** | Macro context strip (DXY, US10Y, VIX, SPX, OIL, GOLD) via yfinance/MT5; risk sentiment (RISK-ON/OFF/NEUTRAL); session (Tokyo/London/NY) |
| **core/news_engine.py** | Single news gate for web + desktop: reads EA calendar via `MT5Bridge.get_calendar_from_ea()`, `is_news_clean()` (60min before / 30min after HIGH), logs `EA calendar not available - waiting for EA` when the EA file is missing. No external feeds. |
| **core/journal.py** | Manual trade logger with micro-hardened limits (3/day, 1 open); JSON + CSV |
| **core/performance_tracker.py** | Logs every `/api/scan` result to `logs/brain_signals.csv` |
| **strategies/trend_following_v2.py** | Classic pullback entry logic |
| **strategies/liquidity_sweep.py** | "Liq rule": strong trend (ADX>40) extended (>1.2 ATR) → limit at liquidity high/low instead of pullback |
| **ui/dashboard.py** | Desktop Tkinter: 7 PairCards (live 3s updates), context strip, news bar, responsive grid, deep-dive panel, journal modal |
| **ui/web_dashboard.html** | Single-page web app: pair grid, market pulse, global calendar, market heatmap, logs modal, 5s polling |
| **ui/pair.html** | Deep-dive per symbol: overview, score breakdown, key levels visual, watchout zones, fundamental/news, info feed |
| **scripts/guard.py** | `safe_write(path, content)` — blocks writes < min size (Main.py 500, api.py 1000, mt5_bridge.py 500, web_dashboard.html 1000, config.py 100) |

---

## 5. Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         MAIN.PY (ORCHESTRATOR)                              │
│  Pre-flight → spawn children → health-check → supervisor (3s) → Ctrl+C     │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │
        ┌─────────────────────────┴─────────────────────────┐
        ▼                                                   ▼
┌───────────────┐                                   ┌───────────────┐
│ MT5 BRIDGE    │                                   │ BRAIN API     │
│ (port 18001)  │                                   │ (port 8000)   │
│               │                                   │ (FastAPI)     │
│ • MT5Bridge   │                                   │               │
│   - rates()   │                                   │ • /api/scan_all        │
│   - indicators│                                   │ • /api/context         │
│   - calendar  │                                   │ • /api/prices          │
│ • EA Socket   │◄─── JSON lines ──────────────────►│ • /api/market_heatmap  │
│   (quotes,    │        EA (TomokoDataPump)        │ • /api/global_calendar │
│    calendar)  │                                   │ • /api/logs            │
│ • File Bridge │                                   │ • /pair/{sym}          │
│   (calendar   │                                   │ • /api/pair/{sym}/deep │
│    prices)    │                                   └───────┬───────┘
└───────┬───────┘                                           │
        │                                                   │
        │ MT5 Terminal (VT Markets)                         │
        │ • tick data                                       │
        │ • rates (H4/H1/M15/D1/W1)                         │
        │ • account info                                    │
        └───────────────────────────────────────────────────┘
                                  │
                    ┌─────────────┴─────────────┐
                    ▼                           ▼
            ┌───────────────┐           ┌───────────────┐
            │ DESKTOP UI    │           │ WEB UI        │
            │ (Tkinter)     │           │ (FastAPI + JS)│
            │ ui/dashboard.py│          │ web_dashboard │
            │ • PairCard ×7 │           │ • pair grid   │
            │ • Deep dive   │           │ • market pulse│
            │ • Journal     │           │ • global cal  │
            └───────────────┘           │ • heatmap     │
                                        │ • logs modal  │
                                        └───────────────┘

EXTERNAL DATA SOURCES:
• yfinance (DXY, US10Y, VIX, SPX, OIL, GOLD) — cached 300s
• FRED API (FEDFUNDS rate) — optional, fallback rates
• MT5 Terminal Calendar (via EA dump) — ONLY news source
```

---

## 6. Architecture & Key Flows

### 6.1 Startup (Main.py)
1. **Pre-flight**: Checks `core/mt5_bridge.py>500B`, `backend/api.py>1000B`, `ui/web_dashboard.html>1000B` — aborts if undersized
2. **Launch children**: `MT5Bridge` (socket server on 18001) + `uvicorn backend.api:app` (8000, no `--reload` unless `--dev`)
3. **Health check**: 3s grace — any child dead → FATAL, stop both, exit 1
4. **Supervisor**: Poll every 3s — any child death → FATAL, stop both, exit 1 (never serves stale data)
5. **Browser auto-open**: `http://localhost:8000/`
6. **Ctrl+C**: Graceful terminate both → exit 0

### 6.2 Scan Pipeline (`/api/scan_all` → 7× `_scan()`)
```
for each symbol in PAIRS:
  1. MT5Bridge.get_rates(symbol, H4/H1/M15/D1/W1) → DataFrames with indicators
  2. RegimeEngine.evaluate_mtf_trend() → H4 gate/direction/ADX/ATR, H1 pullback, M15 bias
  3. LevelsEngine.get_key_levels() → daily H/L, weekly open, liq H/L
  4. IntermarketEngine.get_snapshot() → DXY/US10Y/VIX changes + risk sentiment
  5. NewsEngine.is_news_clean() + get_upcoming_events() → news gate (blocks 60min before/30min after HIGH)
  6. FundamentalEngine.get_fundamental() → structural/intermarket/bias (rate diff or gold logic)
  7. calculate_watchout_zones() → discount/premium watch areas
  8. calculate_brain_score(structural, volatility, levels, intermarket, news)
  9. get_manual_action(score, gate, pullback, news_clean, risk_sentiment, liq_rule)
  10. PerformanceTracker.log() → CSV
```

### 6.3 News/Calendar — Single Source of Truth
- **EA (TomokoDataPump)** dumps `tomoko_calendar_raw.json` → `Common/Files/`
- **NewsEngine** reads it via `MT5Bridge.get_calendar_from_ea()` — the single news gate for web + desktop
- **No external news feeds.** Missing/empty EA file → empty calendar, status `waiting_for_ea`, warning logged
- **No `mt5.calendar_events`** (not available in pip package)

### 6.4 Frontend ↔ Backend
- **Web**: Polls `/api/scan_all` (5s), `/api/context` (on load), `/api/global_calendar`, `/api/market_heatmap` (cached 5s)
- **Desktop**: Direct MT5Bridge calls in `PairCard.update_real()` (3s loop), own `ContextFeed` + `NewsEngine`
- **Pair Deep Dive**: `/api/pair/{symbol}/deep_dive` → full scan + heatmap row + filtered calendar

---

## 7. Current Bugs / Risks / Technical Debt

| # | Issue | Severity | Location |
|---|-------|----------|----------|
| 1 | **No automated tests** — zero unit/integration tests | High | Entire codebase |
| 2 | **Singleton MT5Bridge in api.py** — global `mt5_bridge.connect()` at import time; if MT5 not running, API fails to start | High | `backend/api.py:37-38` |
| 3 | **Hardcoded MT5 terminal path** — `C:\Program Files\VT Markets...` won't work on other machines | Medium | `core/mt5_bridge.py:21-26` |
| 4 | **Duplicate HTML files** — `ui/web_dashboard.html`, `backend/static/web_dashboard.html`, root `web_dashboard.html` (3 copies) | Medium | 3 locations |
| 5 | **Duplicate pair.html** — `ui/pair.html` + `backend/static/pair.html` | Low | 2 locations |
| 6 | **Legacy news_feed.py removed** — news is EA-only via NewsEngine (web + desktop share one gate) | Done | `core/news_engine.py` |
| 7 | **No error handling in `_scan()` for missing DataFrames** — raises HTTPException but some callers don't catch | Medium | `backend/api.py:143-160` |
| 8 | **`safe_write` guard not enforced at runtime** — only used if explicitly called; no pre-commit hook | Low | `scripts/guard.py` |
| 9 | **`logs/brain_signals.csv` grows unbounded** — no rotation (currently ~10 MB) | Medium | `core/performance_tracker.py` |
| 10 | **FRED_API_KEY not configured** — fundamental engine uses hardcoded fallback rates | Low | `core/fundamental_engine.py:23` |
| 11 | **`desktop_legacy.py` / `web_legacy.py` / `START_HERE_OLD.bat`** — dead code kept for history | Low | Root |
| 12 | **`core/__init__.py` empty** — no package exports, implicit imports | Trivial | `core/__init__.py` |
| 13 | **No type hints / mypy** — dynamic typing throughout | Low | All `.py` |
| 14 | **Web dashboard has embedded demo data** (`demoObj`, `DEMO_PULSE`, `DEMO_HEATMAP`) — used when API fails | Medium | `ui/web_dashboard.html:569-610` |
| 15 | **MT5Bridge symbol discovery prints to stdout** — noisy logs on every connect | Low | `core/mt5_bridge.py:260-275` |
| 16 | **Socket server binds 18001 hardcoded in EA** — if port conflicts, EA must be recompiled | Medium | `core/mt5_bridge.py:35, 55-56` |
| 17 | **Journal `can_trade()` logic fragile** — counts "OPEN" status but no auto-close tracking | Medium | `core/journal.py:24-25` |
| 18 | **No requirements lockfile** — `Requirements.txt` has no hashes, no `pip-tools` | Low | `Requirements.txt` |

---

## 8. What Is Ready to Build Next

### Immediate (High Value, Low Effort)
1. **Add log rotation** for `brain_signals.csv` (daily/weekly, keep 30 days)
2. **Consolidate duplicate HTML** — single source in `ui/`, symlink or copy in `backend/static/` at build
3. **Move MT5 path to config/env** — `MT5_TERMINAL_PATH` from `os.getenv()` with default
4. **Add FRED_API_KEY to `.env.example`** — enable live rate differential
5. **Pre-commit hook** running `scripts/guard.py` on protected files
6. **Type hints + mypy** — start with `core/` engines

### Short-Term (Core Features)
7. **Auto-close tracking in Journal** — add `exit_price`, `exit_time`, `pnl`, `status: CLOSED` to enable real P&L
8. **WebSocket for live prices** — replace 5s polling with push from EA socket → `/ws/prices`
9. **Strategy backtester** — replay `brain_signals.csv` against historical data
10. **Alert/notification system** — Discord/Telegram webhook on "LIMIT AT LIQ" or "LOOK FOR PULLBACK"
11. **Persist heatmap cache to disk** — survive API restarts

### Medium-Term (Architecture)
12. **Plugin architecture for strategies** — discover `strategies/*.py`, register via entry points
13. **Multi-account support** — config-driven broker/account selection (VT/HFM)
14. **Dockerfile + docker-compose** — containerize API + bridge + (optional) MT5 via Wine
15. **Web UI: pair comparison view** — side-by-side deep dive for correlated pairs
16. **Automated EA deployment script** — compile/deploy `TomokoDataPump.mq5` to terminal

### Nice-to-Have
17. **Dark/light theme toggle** in web UI
18. **Keyboard shortcuts** in desktop (e.g., `L` log trade, `D` deep dive)
19. **Export journal to Excel/JSON** for tax/accounting
20. **Mobile-responsive tweaks** for web dashboard (already mostly there)

---

## 9. Quick Reference: Entry Points

| Command | What It Runs |
|---------|--------------|
| `python Main.py` | **Production** — supervised orchestrator (recommended) |
| `python Main.py --dev` | **Dev** — uvicorn with `--reload` |
| `python ui/dashboard.py` | **Desktop Tkinter** (legacy, direct MT5Bridge) |
| `python web_legacy.py` | **Old web launcher** (no supervisor, no bridge) |
| `Tomoko.bat` / `START_HERE_OLD.bat` | **Old batch** — two `cmd /k` windows, no supervisor |
| `Tomoko.sh` | **Linux** — uvicorn only (no bridge) |
| `run_tomoko.bat` → `launch_hidden.vbs` | **Hidden desktop shortcut** |

---

## 10. Configuration Cheatsheet (`config.py`)

```python
PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "EURJPY", "EURGBP", "XAUUSD"]
RISK_CONFIG = {
    "XAU": {"sl_atr_mult": 1.8, "tp_atr_mult": 3.6, "sl_min_usd": 3.0, "lot": 0.01},
    "FX":  {"sl_atr_mult": 1.2, "tp_atr_mult": 2.4, "lot": 0.01},
}
TREND_CONFIG = {"adx_min": 22, "atr_ratio_min": 0.8, "pullback_max_atr": 0.5}
LIQ_RULE_CONFIG = {"adx_threshold": 40, "dist_threshold_atr": 1.2, "liq_offset_atr": 0.3, "enabled": True}
NEWS_CONFIG = {"show_actual": True, "block_minutes_before": 60, "high_impact_only": False}
MAX_DAILY_TRADES = 3
MAX_OPEN_POSITIONS = 1
```

---

## 11. Git State

- **Branch**: `main`
- **Recent commits**:
  - `22bf6e2` — "fix: make Main.py true orchestrator (10KB), add supervisor, guard, gitignore"
  - `702a378` — "Tomoko initial - half build"
- **Uncommitted**: `backend/static/web_dashboard.html`, `web_dashboard.html`, `FIX_REPORT.md`, `RECOVERY_REPORT.md`, `Tomoko-Audit-Generator-FIXED.py`
- **Ignored**: `__pycache__/`, `*.pyc`, `logs/`, `*.csv`, `.venv/`, `.DS_Store`, `.vscode/`, `*.recovered`, `*.backup-*`

---

*End of PROJECT_BRAIN.md*