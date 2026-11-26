# Tomoko Instinct — Workspace Customizations & Execution Guidelines

This document defines automated workflows, custom slash commands, and pair-programming rules for the **Tomoko Instinct** private institutional trading platform.

---

## Custom Slash Commands

### `/dna` (or `dna`) — Unified Trader DNA Ingestion & Permanent LLM/VLM Vault Pipeline

Whenever the user enters `/dna` or `dna`:
1. **Locate Dropoff**:
   - Inspect `trader_dna/dropoff/` directory (`c:\Users\USER\Documents\Tomoko\trader_dna\dropoff\`).
   - Snapshots can arrive either via Tomoko's in-page **TradingView Terminal Snapshot Dock** (`📸 Snap → DNA` or `Ctrl+V` paste, which also writes a paired `.context.json` live market telemetry sidecar) or manually saved PNGs.
   - If no new chart images exist, notify the user with a quick reminder:
     *"No new chart images found in `trader_dna/dropoff/`. Click `📸 Snap → DNA` in Tomoko's TradingView Terminal (or paste with `Ctrl+V`) and run `/dna` again."*
   - If images exist, sort by Windows creation/modification time descending (newest first) and process all pending snapshots.

2. **Native Vision Inspection (Zero API Quota)**:
   - Open each snapshot directly using Antigravity's built-in `read_file` / native vision tool.
   - **Do NOT call external Google AI Studio APIs** (preserve user's Gemini key strictly for news, macro telemetry, and Battle-Plan briefings).
   - Determine **Symbol & Timeframe** with 100% certainty:
     - Read the on-chart TradingView header/legend (e.g., `Gold Spot / U.S. Dollar · 15 · OANDA`) and top toolbar (`XAUUSD | 15m`), cross-checked with the filename (`{SYMBOL}_{TF}_{MODE}_{TIMESTAMP}.png`) or countdown timer below the price pill if present (`> 60:00` → H4, `15:01–60:00` → H1, `≤ 15:00` → M15/M5).
     - Note: Filename tags (`_ZONES_` vs `_TRADE_`) serve as a helpful hint, while visual inspection confirms whether the chart contains horizontal zone boxes, a Long/Short Position tool, or both.

3. **Dual-Mode Extraction (Zone Markups & Trade Execution Reviews)**:
   - **Mode A: TRADE EXECUTION REVIEW** (If TradingView Long/Short Position tool or Entry/SL/TP levels are present):
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
     - Record via `core.trader_dna_engine.record_trade_execution(...)` (which updates `trader_dna/data/trader_dna_profile.json` AND permanently exports to `D:\Tomoko_Trader_Vault\dataset\`).
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
     - Record via `core.trader_dna_engine.record_session_zones(...)` (which updates `trader_dna/data/trader_dna_profile.json` AND permanently exports to `D:\Tomoko_Trader_Vault\dataset\`).
   - **Hybrid Chart (Zones + Trade Position Tool on the same image)**: Extract and log both (pass `archive_files=False` on the first call and `archive_files=True` on the second so both records receive the image & sidecar context cleanly before archiving).

4. **Permanent LLM/VLM Vault Archival & Weekly Lifecycle**:
   - Calling `record_session_zones()` / `record_trade_execution()` automatically:
     1. Updates `trader_dna/data/trader_dna_profile.json` (used by weekend Optuna / LogReg / LightGBM tuning).
     2. Permanently saves to `D:\Tomoko_Trader_Vault\dataset\` (**NEVER purged**):
        - `images/{sample_id}.png`
        - `labels/{sample_id}.json` (including the live MT5/macro `.context.json` sidecar when captured from Tomoko)
        - `train_vlm.jsonl` (multimodal Vision-Language LoRA format)
        - `train_llm.jsonl` (text LLM ChatML LoRA format)
     3. Moves the processed dropoff image into `trader_dna/archive/current_week/` and cleans up the dropoff sidecar so `trader_dna/dropoff/` is left 100% empty.
   - **Weekend Post-Tuning Purge**: Running `python scripts/weekend_comparison.py --tune --purge-archive` only clears temporary C: workspace copies in `trader_dna/archive/current_week/` while `trader_dna/data/trader_dna_profile.json` and `D:\Tomoko_Trader_Vault\dataset\` remain permanently intact.

5. **Session Output**:
   - Present a clear, formatted summary table showing:
     - Timeframe, Live price, Mode (Zone Markup vs Trade Execution)
     - Zones (price ranges, 50% CE midlines, anchor types, structural roles, "WHY") and/or Trade Execution (Entry, SL, TP, R:R, trigger candle, rationale)
     - Confirmation that permanent LLM/VLM training samples were archived to `D:\Tomoko_Trader_Vault\dataset\`.
