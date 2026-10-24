# Tomoko Instinct — Workspace Customizations & Execution Guidelines

This document defines automated workflows, custom slash commands, and pair-programming rules for the **Tomoko Instinct** institutional trading platform.

---

## Custom Slash Commands

### `/ingest` (or `ingest`) — Proprietary Chart Markup Ingestion Pipeline

Whenever the user enters `/ingest` or `ingest`:
1. **Locate Dropzone**:
   - Inspect `markups/` directory (`c:\Users\USER\Documents\Tomoko\markups\`).
   - If no new chart images exist, notify the user with a quick reminder:
     *"No new chart images found in `markups/`. Save your TradingView snapshots there (Camera icon -> Download image) and run `/ingest` again."*
   - If images exist, sort by Windows creation/modification time descending (newest first) and strictly process up to the **3 latest snapshots** (`[:3]`).

2. **Native Vision Inspection (Zero API Quota)**:
   - Open each snapshot directly using Antigravity's built-in `view_file` tool.
   - **Do NOT call external Google AI Studio APIs** (preserve user's Gemini key for news and live macro telemetry).
   - Determine timeframe with 100% mathematical certainty using the countdown timer displayed immediately below the blue market price pill on the right price axis:
     - Timer shows hours (e.g., `01:38:43` or `> 60:00`) $\rightarrow$ **H4**
     - Timer is between `15:01` and `60:00` (e.g., `33:24`) $\rightarrow$ **H1**
     - Timer is `≤ 15:00` (e.g., `02:06` or `01:09`) $\rightarrow$ **M15** (or **M5** if top bar displays `5m`)
     - If no timer is visible, infer timeframe from candle density and axis markings.

3. **Zone & Midline Extraction**:
   - Extract:
     - Symbol (e.g., `XAUUSD`)
     - Live Market Price from the blue pill badge
     - All horizontal drawn rectangular boxes (from highest to lowest price):
       - Top boundary (`high`)
       - Bottom boundary (`low`)
       - Dashed purple centerline (`50% Consequent Encroachment midline = (high + low) / 2`)
       - Type: `RESISTANCE` (above current price) or `SUPPORT` (below current price)
       - Role: `Upper Zone 2`, `Upper Zone 1`, `Lower Zone 1`, `Lower Zone 2` (or intermediate pullback shelf)

4. **Benchmark Archival & Historical Dataset (Decoupled from Live Engine)**:
   - Update `data/trader_profile.json`:
     - Update `active_session` dictionary with the latest zones by timeframe.
     - Append the snapshot entry to `history`.
   - **ZERO LIVE INTERFERENCE RULE**: All ingested data is strictly kept as historical ground-truth benchmark data for **weekend comparison and fine-tuning**. Ingested markups are **NEVER** injected into Tomoko's live trading execution, live order flow radar, or live chart visualization. Tomoko operates 100% autonomously on its own institutional algorithms.
   - Move the processed image files from `markups/` into `markups/archive/YYYY-MM-DD/`.
   - Run the 3-day rolling purge: automatically delete any archive date folder older than 3 days (72 hours).
   - Ensure `markups/` is left 100% clean and empty.

5. **Session Output**:
   - Present a clear, formatted summary table showing:
     - Timeframe & Live price
     - Zones with price ranges, 50% CE midlines, and order flow context
     - Key institutional confluences across timeframes
   - Remind the user: *"Data logged to `data/trader_profile.json` for weekend comparison and fine-tuning. Live Tomoko engines continue running 100% autonomously."*
