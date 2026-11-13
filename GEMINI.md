# Tomoko Instinct — Workspace Customizations & Execution Guidelines

This document defines automated workflows, custom slash commands, and pair-programming rules for the **Tomoko Instinct** institutional trading platform.

---

## Custom Slash Commands

### `/ingest` (or `ingest`) — Proprietary Chart Markup Ingestion Engine

Whenever the user enters `/ingest` or `ingest`:
1. **Locate Dropzone**:
   - Inspect `markups/` directory (`c:\Users\USER\Documents\Tomoko\markups\`).
   - If no new chart images exist, notify the user with a quick reminder:
     *"No new chart images found in `markups/`. Save your TradingView snapshots there (Camera icon -> Download image) and run `/ingest` again."*
   - If images exist, sort by Windows creation/modification time descending (newest first) and process pending snapshots.

2. **Native Vision Inspection (Zero API Quota)**:
   - Open each snapshot directly using Antigravity's built-in `view_file` tool.
   - **Do NOT call external Google AI Studio APIs** (preserve user's Gemini key for news and live macro telemetry).
   - Determine timeframe with 100% mathematical certainty using the countdown timer displayed immediately below the blue market price pill on the right price axis:
     - Timer shows hours (e.g., `01:38:43` or `> 60:00`) $\rightarrow$ **H4**
     - Timer is between `15:01` and `60:00` (e.g., `33:24`) $\rightarrow$ **H1**
     - Timer is `≤ 15:00` (e.g., `02:06` or `01:09`) $\rightarrow$ **M15** (or **M5** if top bar displays `5m`)
     - If no timer is visible, infer timeframe from candle density and axis markings.

3. **Zone, Midline & DNA Characteristics Extraction**:
   - Extract:
     - Symbol (e.g., `XAUUSD`) & Live Market Price from the blue pill badge
     - All horizontal drawn rectangular boxes (from highest to lowest price):
       - Top boundary (`high`), Bottom boundary (`low`)
       - Dashed purple centerline (`50% Consequent Encroachment midline = (high + low) / 2`)
       - Box height in points (`high - low`)
       - Type: `RESISTANCE` (above current price) or `SUPPORT` (below current price)
       - Role: `Upper Zone 2`, `Upper Zone 1`, `Lower Zone 1`, `Lower Zone 2` (or intermediate pullback shelf)
       - **Zone Marking Characteristics ("WHY Marked")**:
         - `anchor_type`: `WICK_CLUSTER`, `BODY_EXTREME`, `ORDER_BLOCK`, `IMBALANCE_FVG`, or `FRACTAL_SWING_TIP`
         - `structural_role`: `LIQUIDITY_SWEEP_ORIGIN`, `BREAK_OF_STRUCTURE_ORIGIN`, `SWAP_FLIP`, `SESSION_HIGH_LOW`, or `RANGE_BOUNDARY`
         - `prior_retest_count`: Estimated touches before snapshot (0 = fresh, 1, 2, 3+)
         - `why_marked`: Clear, precise institutional rationale explaining why the trader marked this zone

4. **Benchmark Archival & Weekly Lifecycle (Decoupled from Live Engine)**:
   - Update `data/trader_profile.json`:
     - Update `active_session` dictionary with the latest zones by timeframe.
     - Append the snapshot entry with its extracted characteristics to cumulative `history`.
   - **ZERO LIVE INTERFERENCE RULE**: Ingested data is strictly kept as historical ground-truth benchmark data for **weekend comparison and fine-tuning**. Ingested markups are **NEVER** injected into Tomoko's live trading execution, live order flow radar, or live chart visualization. Tomoko operates 100% autonomously on its own institutional algorithms.
   - **Weekly Screenshot Retention**: Move processed image files into `markups/archive/current_week/`. Raw images remain preserved throughout the week across all 3 daily sessions (pre-Tokyo, pre-London, pre-US).
   - **Weekend Post-Tuning Purge**: Raw screenshots are automatically purged on the weekend after running the fine-tuning suite (`python scripts/weekend_comparison.py --tune --purge-archive`), leaving disk space clean while `data/trader_profile.json` permanently retains the numerical and characteristic learning data.
   - Ensure `markups/` dropzone is left 100% clean and empty.

5. **Session Output**:
   - Present a clear, formatted summary table showing:
     - Timeframe & Live price
     - Zones with price ranges, 50% CE midlines, anchor types, structural roles, and the institutional "WHY" rationale.
   - Remind the user: *"Data logged to `data/trader_profile.json` for weekend comparison and fine-tuning. Live Tomoko engines continue running 100% autonomously."*

---

### `/dna` (or `dna`) — Trader DNA Ingestion & Closed-Loop Calibration Pipeline

Whenever the user enters `/dna` or `dna`:
1. **Locate Dropoff**:
   - Inspect `trader_dna/dropoff/` directory (`c:\Users\USER\Documents\Tomoko\trader_dna\dropoff\`).
   - If no new chart images exist, notify the user with a quick reminder:
     *"No new chart images found in `trader_dna/dropoff/`. Save your TradingView snapshots there (Camera icon -> Download image) and run `/dna` again."*
   - If images exist, sort by Windows creation/modification time descending (newest first) and process all pending snapshots.

2. **Native Vision Inspection (Zero API Quota)**:
   - Open each snapshot directly using Antigravity's built-in `view_file` tool.
   - **Do NOT call external Google AI Studio APIs** (preserve user's Gemini key for news and live macro telemetry).
   - Determine timeframe with 100% mathematical certainty using the countdown timer displayed immediately below the blue market price pill on the right price axis:
     - Timer shows hours (e.g., `01:38:43` or `> 60:00`) $\rightarrow$ **H4**
     - Timer is between `15:01` and `60:00` (e.g., `33:24`) $\rightarrow$ **H1**
     - Timer is `≤ 15:00` (e.g., `02:06` or `01:09`) $\rightarrow$ **M15** (or **M5** if top bar displays `5m`)
     - If no timer is visible, infer timeframe from candle density and axis markings.

3. **Dual-Mode Extraction (Canvas vs Trade Review)**:
   - **Mode A: TRADE EXECUTION REVIEW** (If TradingView Long/Short Position tool or trade entry box is present):
     - Extract:
       - `direction`: LONG (Buy) or SHORT (Sell)
       - `entry_price`: exact boundary where green and red meet
       - `stop_loss`: outer boundary of red box (read exact price on right axis)
       - `take_profit`: outer boundary of green box (read exact price on right axis)
       - `risk_reward`: R:R ratio
       - `pattern_type`: `UNMITIGATED_OB_50_CE`, `SWAP_FLIP_BREAKER_RETEST`, `TRENDLINE_CHOUCH_RETEST`, `LIQUIDITY_SWEEP_REVERSAL`
       - `trigger_candle`: `wick_rejection_type` (UPPER_WICK / LOWER_WICK), `wick_rejection_ratio`, `displacement_confirmed`
       - `invalidation_logic`: structural reason why SL was parked there
       - `tp_logic`: opposing shelf / target reason why TP was parked there
       - `why_entered`: full institutional entry rationale
     - Log to `trader_dna/data/trader_dna_profile.json` under `executed_trades` and `history`.
   - **Mode B: PRE-SESSION CANVAS MARKUP** (If horizontal rectangular zones are present):
     - Extract:
       - Symbol (e.g., `XAUUSD`) & Live Market Price
       - All horizontal drawn rectangular boxes (from highest to lowest price):
         - Top boundary (`high`), Bottom boundary (`low`)
         - Dashed purple centerline (`50% Consequent Encroachment midline = (high + low) / 2`)
         - Box height in points (`high - low`)
         - Type: `RESISTANCE` (above current price) or `SUPPORT` (below current price)
         - Role: `Upper Zone 2`, `Upper Zone 1`, `Lower Zone 1`, `Lower Zone 2` (or intermediate pullback shelf)
         - `anchor_type`: `WICK_CLUSTER`, `BODY_EXTREME`, `ORDER_BLOCK`, `IMBALANCE_FVG`, or `FRACTAL_SWING_TIP`
         - `structural_role`: `LIQUIDITY_SWEEP_ORIGIN`, `BREAK_OF_STRUCTURE_ORIGIN`, `SWAP_FLIP`, `SESSION_HIGH_LOW`, or `RANGE_BOUNDARY`
         - `prior_retest_count`: Estimated touches before snapshot (0 = fresh, 1, 2, 3+)
         - `why_marked`: Clear, precise institutional rationale explaining why the trader marked this zone
     - Log to `trader_dna/data/trader_dna_profile.json` under `active_session` and `history`.

4. **Benchmark Archival & Weekly Lifecycle (Decoupled from Live Engine)**:
   - Update `trader_dna/data/trader_dna_profile.json`:
     - Update `executed_trades` or `active_session` dictionary.
     - Append the snapshot entry with its extracted characteristics to cumulative `history`.
   - **ZERO LIVE INTERFERENCE RULE**: Ingested data is strictly kept as historical ground-truth benchmark data for **weekend comparison and fine-tuning**. Ingested markups are **NEVER** injected into Tomoko's live trading execution, live order flow radar, or live chart visualization. Tomoko operates 100% autonomously on its own institutional algorithms.
   - **Weekly Screenshot Retention**: Move processed image files into `trader_dna/archive/current_week/`. Raw images remain preserved throughout the week across all 3 daily sessions (pre-Tokyo, pre-London, pre-US).
   - **Weekend Post-Tuning Purge**: Raw screenshots are automatically purged on the weekend after running the fine-tuning suite (`python scripts/weekend_comparison.py --tune --purge-archive`), leaving disk space clean while `trader_dna/data/trader_dna_profile.json` permanently retains the numerical and characteristic learning data.
   - Ensure `trader_dna/dropoff/` dropzone is left 100% clean and empty.

5. **Session Output**:
   - Present a clear, formatted summary table showing:
     - Timeframe & Live price
     - Zones with price ranges, 50% CE midlines, and order flow context
     - Key institutional confluences across timeframes
   - Remind the user: *"Data logged to `trader_dna/data/trader_dna_profile.json` for weekend comparison and fine-tuning. Live Tomoko engines continue running 100% autonomously."*
