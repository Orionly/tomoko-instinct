# Tomoko Instinct — Pre-Session AI Trade Battle-Plan Specification

> **Document Status:** Architectural Blueprint & Implementation Specification  
> **Target Version:** Tomoko Instinct v5.1+  
> **Scope:** Automated Institutional Pre-Session Analysis across 7 FX & Commodity Pairs  

---

## 1. Executive Summary & Vision

Tomoko Instinct is designed as an autonomous, institutional-grade terminal for professional currency and commodities traders. 

While conventional trading platforms provide generic, "textbook" news summaries (e.g., *"Yields rose slightly, safe-haven demand elevated"*), professional traders require **tactical execution gameplans (Battle-Plans)**. 

The **Pre-Session AI Analysis Engine** transforms raw quantitative telemetry (live price action, real yields, calendar events, order flow levels) into an actionable, session-specific trading playbook delivered directly into each pair's **Deep Dive Card** and the **Macro Cockpit**.

---

## 2. Core Architecture: Single-Call Batched Cloud API

Rather than running a resource-heavy 1.5B+ parameter Small Language Model (SLM) locally on the trader's machine (which incurs RAM/CPU overhead and package installation complexities on Windows), Tomoko utilizes a **scheduled, single batched API call (e.g., Gemini 2.0 / 1.5 Flash, Claude 3.5 Sonnet, or GPT-4o-mini)**.

```
┌────────────────────────────────────────────────────────┐
│  Scheduled Trigger (30 min prior to major session)     │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│  Tomoko Aggregation Layer                              │
│  - LevelsEngine (D1/W1 High/Low, Pivot boundaries)     │
│  - PriceEngine (Live, H4/H1/M15 Closes, ATR, RSI)      │
│  - MacroIntelligenceEngine (10Y Real Yield, DXY, VIX)  │
│  - NewsEngine (Upcoming tier-1 events, consensus)      │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼  (1 Single Batched Request for all 7 pairs)
┌────────────────────────────────────────────────────────┐
│  High-Reasoning Cloud LLM API                          │
│  - Evaluates cross-pair macro consistency              │
│  - Produces structured 5-block battle plans            │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│  Local Cache: logs/session_briefings.json              │
│  - Instant UI delivery (0ms latency for trader)        │
│  - Offline fallback resilience                         │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│  Pair Deep Dive Card (pair.html) & Web Dashboard       │
│  - Per-pair 5-block battle-plan                        │
│  - Pre-session executive macro thesis                  │
└────────────────────────────────────────────────────────┘
```

### Key Advantages of this Architecture:
1. **Cross-Pair Macro Coherence:** By analyzing all 7 pairs in one context window, the AI ensures non-contradictory logic (e.g., if DXY is bid on real yields, EURUSD, GBPUSD, and USDJPY align with identical macro transmission reasoning).
2. **Ultra-Low Cost & Zero Quota Pressure:** 
   - 3 sessions per day $\times$ 1 call = **3 API calls per day** (~90 calls per month).
   - Monthly API cost is negligible (< $0.05 / month) or entirely within free tiers.
3. **Zero Local Hardware Burden:** 0 MB model download, zero CPU spikes, and works reliably on low-spec laptops and VPS instances.
4. **Zero-Latency UI:** The user interface never awaits an API call; cards read directly from the local JSON cache.

---

## 3. Session Schedule & Automated Trigger Windows

The background scheduler triggers analysis **30 minutes prior to institutional liquidity handoffs**:

| Trading Session | Institutional Open | Trigger Time (UTC) | Local Time (MYT / GMT+8) | Session Focus |
| :--- | :--- | :--- | :--- | :--- |
| **Tokyo (Asia)** | 00:00 UTC | **23:30 UTC** | 07:30 MYT | Overnight US close recap, Asian liquidity boundaries, JPY/AUD sentiment |
| **London (Europe)** | 08:00 UTC | **07:30 UTC** | 15:30 MYT | European macro open, EUR/GBP drivers, UK/EU calendar releases |
| **New York (US)** | 13:00 UTC | **12:30 UTC** | 20:30 MYT | US morning data releases, Wall Street risk sentiment, DXY & Gold catalysts |

---

## 4. The 5-Block Institutional Battle-Plan Template

A complete trade briefing requires quantitative boundaries, execution triggers, game-theory probability branching, and de-risking rules. 

Every pair analysis follows this standardized 5-block structure:

### Reference Gold (XAUUSD) Example

```markdown
⚡ XAUUSD PRE-SESSION BATTLE-PLAN (NEW YORK OPEN // 12:30 UTC / 20:30 MYT)
Status: Live $4,185.10 (-0.29% early) | Reclaimed breakdown low $4,088 | Back inside Value

[BLOCK 1: MACRO CONTEXT & PRIMARY DRIVER]
• Active Regime: RISK-OFF / Real Yield Easing (10Y down 10bp to 5.09%; DXY faded 90pts to 102.00).
• Primary Driver Attribution: Real Yield Compression (70% weight), DXY softening (20%), Haven unwinding (10%).
• Tier-1 Catalyst Today: 22:00 MYT (14:00 UTC) UoM Consumer Sentiment Prelim (Consensus: 46.3 vs 54.2 prev).
  - IF Actual < 48.0 → Dovish Fed reinforcement, DXY breaks <101.80 → Gold tailwind targeting $4,200+.
  - IF Actual > 54.0 or 1Y Inflation > 3.6% → USD bid caps upside near $4,194 resistance.

[BLOCK 2: MTF MARKET STRUCTURE & KEY LEVELS]
• H4 Structure: Inside Decision Zone [$4,074 - $4,201]; RSI 52 (Neutral). Ascending triangle false breakdown reclaimed (Bear Trap).
• H1 Structure: Bullish recovery ($4,073 low → $4,194 high = 120pt expansion).
• Resistance Ceilings: $4,194 (Day High) | $4,201 (Decision Zone High) | $4,230 (Structural Macro Ceiling).
• Demand Floors:       $4,154 (Value Low) | $4,149 (Pivot Point) | $4,103 (Weekly Low) | $4,088 (Major Low).

[BLOCK 3: PRIMARY EXECUTION SETUP (Base Case: Long on Pullback — 65% Probability)]
• Entry Zone: $4,150 - $4,170 (Confluence: Value Low $4,154 + Pivot $4,149 + DXY fade).
• Trigger Confirmation: M5 lower rejection wick (≥ 2x candle body) at $4,155 + Bullish Engulfing close > $4,175.
• Stop Loss: $4,135 (Strict: 15pts below value low and beneath $4,149 pivot).
• Targets: TP1 $4,185 | TP2 $4,194 | TP3 $4,201 | TP4 $4,217 | TP5 $4,230.
• Trade Management: Move SL to Breakeven at $4,194. Take 50% profit off table 15 min BEFORE 22:00 MYT UoM news.

[BLOCK 4: CONTINGENCY / INVALIDATION PLAN (Catalyst Shock — 35% Probability)]
• Trigger Condition: UoM prints > 54.0 + DXY reclaims > 102.50 + 10Y yields rebound > 5.15%.
• Counter-Trend Short Zone: $4,210 - $4,230 (Exhaustion wick sweep into Macro Ceiling).
• Short Trigger: M5 upper wick rejection at $4,217 + Bearish Engulfing close < $4,200.
• Stop Loss: $4,245 (Above broken structure).
• Downside Targets: $4,194 → $4,185 → $4,154 → $4,103.
• Hard Structural Invalidation: H1 close below $4,103 invalidates bull thesis; H1 close above $4,230 invalidates bear thesis.

[BLOCK 5: SESSION TRAP WARNING & TRADER ACTION CHECKLIST]
• Session Trap Warning: Watch for London/NY overlap "Judas Swing" — a quick liquidity dip below $4,149 pivot to flush early retail longs before institutional reversal upward.
• Risk / ATR Sizing: H1 ATR is $17.60. Maintain minimum stop distance of $31.60 (1.8x ATR). Reduce lot size by 30% into Friday close.
• Execution Checklist:
  [ ] Alert set at $4,150 (Buy Zone Entry)
  [ ] Alert set at $4,194 (Take Profit 2 / Breakeven trigger)
  [ ] Warning: High spread volatility expected between 21:58 - 22:05 MYT
```

---

## 5. Technical Implementation Details

### A. Background Scheduler (`core/session_briefing_engine.py`)
- Continuously tracks the current UTC time.
- Triggers at `23:30`, `07:30`, and `12:30` UTC on institutional trading days (Monday morning Asia through Friday NY).
- Assembles a structured payload from local engines:
  ```json
  {
    "timestamp": "2026-10-17T12:30:00Z",
    "session": "NEW_YORK",
    "macro_telemetry": {
      "dxy": {"price": 102.00, "change_pct": -0.53},
      "us10y": {"yield": 5.09, "change_bp": -10.1},
      "vix": {"price": 20.0, "regime": "ELEVATED"},
      "real_yield_10y": 2.78
    },
    "pairs_data": {
      "XAUUSD": {"price": 4185.10, "h1_atr": 17.60, "pivots": [4149, 4194, 4201]},
      "EURUSD": {"price": 1.1243, "h1_atr": 0.0035, "pivots": [1.1210, 1.1280]}
      // ... GBPUSD, USDJPY, GBPJPY, EURJPY, EURGBP
    }
  }
  ```

### B. Storage & Retrieval Cache
- Saved to: [`logs/session_briefings.json`](file:///c:/Users/USER/Documents/Tomoko/logs/session_briefings.json).
- Format:
  ```json
  {
    "session": "NEW_YORK",
    "generated_at": "2026-10-17T12:30:00Z",
    "executive_macro_thesis": "...",
    "pairs": {
      "XAUUSD": { ... 5-block payload ... },
      "EURUSD": { ... 5-block payload ... }
    }
  }
  ```

### C. Web API Endpoints (`backend/api.py`)
- `GET /api/session_briefing?session=latest`: Returns the global executive thesis and all pair plans.
- `GET /api/pair/{symbol}/deep_dive`: Enhanced to inject `data["ai_battle_plan"]` for the specific requested currency.

### D. UI Component Rendering (`ui/pair.html`)
- Dedicated **"⚡ Pre-Session AI Battle-Plan"** glassmorphic card on the Pair Deep Dive view.
- Expandable / collapsible blocks with visual pills for Entry Zones, Invalidation, and Checklist items.
- Visual timestamp indicator displaying recency (e.g., *"Generated 42m ago ahead of New York Open"*).

---

## 6. Implementation Roadmap

1. **Phase 1: Engine & API Client Hook**
   - Create `core/session_briefing_engine.py` with multi-provider cloud support (Gemini, Claude, OpenAI).
   - Add `AI_API_KEY` and `AI_MODEL_NAME` to `config.py`.
2. **Phase 2: Local Context Bundler**
   - Wire `PriceEngine`, `LevelsEngine`, and `MacroIntelligenceEngine` to format the pre-flight prompt.
3. **Phase 3: Deep Dive UI Integration**
   - Add the Battle-Plan card to [`ui/pair.html`](file:///c:/Users/USER/Documents/Tomoko/ui/pair.html) and [`ui/web_dashboard.html`](file:///c:/Users/USER/Documents/Tomoko/ui/web_dashboard.html).
4. **Phase 4: Session Notification System**
   - Optional desktop / browser audio alert when a fresh pre-session battle-plan is generated.
