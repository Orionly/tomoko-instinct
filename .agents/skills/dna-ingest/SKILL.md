---
name: dna-ingest
description: >-
  Ingests the trader's TradingView snapshots from trader_dna/dropoff/ folder (Dual-Mode: Pre-Session Canvas Markups & Trade Execution Reviews with Entry, SL, TP), archives images, and logs them into trader_dna/data/trader_dna_profile.json
  as benchmark ground-truth for weekend comparison and algorithm fine-tuning (isolated from live Tomoko engines).
  Triggered whenever the user enters "/dna", "dna", or asks to ingest chart markups/trade reviews.
---

# Trader DNA Closed-Loop Ingestion Pipeline (/dna)

Use this workflow to ingest trader-drawn TradingView chart snapshots into the isolated Trader DNA calibration sandbox for weekend entry-style learning and fine-tuning.

## Ingestion Procedure

1. **Check Dropoff Folder**:
   - Inspect `trader_dna/dropoff/` directory for new image snapshots (`.png`, `.jpg`, `.jpeg`, `.webp`).
   - If empty, inform the user to save TradingView chart snapshots there (Camera icon -> Download image).
   - If multiple files exist, sort descending by Windows creation time and process all pending snapshots.

2. **Native Vision Inspection (Zero API Quota)**:
   - Open each snapshot directly using Antigravity's built-in native vision (0 external API quota used).
   - Classify Timeframe using the candle countdown timer under the blue price badge:
     - `> 60:00` or hours format -> H4
     - `15:01 – 60:00` -> H1
     - `≤ 15:00` -> M15 (or M5 if labeled)
   - **Mode A: TRADE EXECUTION REVIEW** (If TradingView Long/Short Position tool or entry box is present):
     - Extract:
       - `direction`: LONG (Buy) or SHORT (Sell)
       - `entry_price`: exact boundary where green and red meet
       - `stop_loss`: outer boundary of the red box (read exact price on right axis)
       - `take_profit`: outer boundary of the green box (read exact price on right axis)
       - `risk_reward`: R:R ratio
       - `pattern_type`: `UNMITIGATED_OB_50_CE`, `SWAP_FLIP_BREAKER_RETEST`, `TRENDLINE_CHOUCH_RETEST`, `LIQUIDITY_SWEEP_REVERSAL`
       - `trigger_candle`: `wick_rejection_type` (UPPER_WICK / LOWER_WICK), `wick_rejection_ratio`, `displacement_confirmed`
       - `invalidation_logic`: structural reason why SL was parked there
       - `tp_logic`: opposing shelf / target reason why TP was parked there
       - `why_entered`: full institutional entry rationale
     - Log to `trader_dna_profile.json["executed_trades"]` and `history`.
   - **Mode B: PRE-SESSION CANVAS MARKUP** (If horizontal rectangular zones are present):
     - Extract Upper/Lower zones, price bounds, purple 50% CE midlines, and **Zone DNA Characteristics**:
       - `anchor_type`: `WICK_CLUSTER`, `BODY_EXTREME`, `ORDER_BLOCK`, `IMBALANCE_FVG`, `FRACTAL_SWING_TIP`
       - `structural_role`: `LIQUIDITY_SWEEP_ORIGIN`, `BREAK_OF_STRUCTURE_ORIGIN`, `SWAP_FLIP`, `SESSION_HIGH_LOW`, `RANGE_BOUNDARY`
       - `why_marked`: institutional rationale
     - Log to `trader_dna_profile.json["active_session"][timeframe]` and `history`.

3. **Record & Clean (Benchmark Dataset)**:
   - Update `trader_dna/data/trader_dna_profile.json`.
   - **Zero Live Interference**: Ingested data is strictly kept for weekend comparison and fine-tuning; it is never injected into Tomoko's live trading execution or live chart.
   - Move processed files to `trader_dna/archive/current_week/`.
   - Purged on weekends post-fine-tune via `python scripts/weekend_comparison.py --tune --purge-archive`, keeping `trader_dna/data/trader_dna_profile.json` permanent.
   - Present a concise institutional briefing card with Entry / SL / TP and alignment diagnostics.
