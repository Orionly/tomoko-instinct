---
name: dna-ingest
description: >-
  Ingests the trader's TradingView snapshots from trader_dna/dropoff/ folder (Dual-Mode: Pre-Session Canvas Markups & Trade Execution Reviews with Entry, SL, TP), logs them into trader_dna/data/trader_dna_profile.json, permanently archives images + live market context into D:\Tomoko_Trader_Vault\dataset\ (train_vlm.jsonl + train_llm.jsonl) for future custom LLM/VLM training, and cleans trader_dna/dropoff/.
  Triggered whenever the user enters "/dna", "dna", or asks to ingest chart markups/trade reviews.
---

# Unified Trader DNA Ingestion & Permanent LLM/VLM Vault Pipeline (/dna)

Use this workflow to ingest trader-drawn TradingView chart snapshots (captured via Tomoko's TradingView Terminal Snapshot Dock or saved manually to `trader_dna/dropoff/`) into both the weekend ML calibration profile and the permanent LLM/VLM training vault.

## Ingestion Procedure

1. **Check Dropoff Folder**:
   - Inspect `trader_dna/dropoff/` for new image snapshots (`.png`, `.jpg`, `.jpeg`, `.webp`).
   - Note that snapshots taken via Tomoko's `📸 Snap → DNA` button include a paired `.context.json` sidecar with live MT5 candles, ATR, spot price, and active HUD levels — `record_session_zones()` / `record_trade_execution()` automatically pick this up and embed it into `D:\Tomoko_Trader_Vault\dataset\`.
   - If empty, remind the user to click `📸 Snap → DNA` in Tomoko's TradingView Terminal (or paste with `Ctrl+V`).
   - If multiple files exist, sort descending by Windows creation time and process all pending snapshots.

2. **Native Vision Inspection (Zero API Quota)**:
   - Open each snapshot directly using Antigravity's built-in `read_file` tool (0 external Gemini API quota used).
   - Identify Symbol & Timeframe from the on-chart TradingView legend (e.g., `Gold Spot / U.S. Dollar · 15 · OANDA`), top toolbar (`XAUUSD | 15m`), filename (`{SYMBOL}_{TF}_{MODE}_{TIMESTAMP}.png`), or countdown timer if visible (`> 60:00` → H4, `15:01–60:00` → H1, `≤ 15:00` → M15/M5).
   - **Mode A: TRADE EXECUTION REVIEW** (TradingView Long/Short Position tool or Entry/SL/TP levels):
     - Extract `direction`, `entry_price`, `stop_loss`, `take_profit`, `risk_reward`, `pattern_type`, `trigger_candle`, `invalidation_logic`, `tp_logic`, `why_entered`.
     - Persist by calling `core.trader_dna_engine.record_trade_execution(trade_data, file_paths=[img_path])`.
   - **Mode B: PRE-SESSION CANVAS MARKUP** (Horizontal rectangular Supply/Demand boxes + 50% CE midlines):
     - Extract Upper/Lower zones, `high`, `low`, `midline`, `box_height_pts`, `anchor_type`, `structural_role`, `prior_retest_count`, `why_marked`.
     - Persist by calling `core.trader_dna_engine.record_session_zones({tf: data}, file_paths=[img_path])`.
   - **Hybrid Chart (Both boxes + Long/Short Position tool on one image)**:
     - Call `record_session_zones(..., archive_files=False)` first, then `record_trade_execution(..., archive_files=True)` so both datasets receive the image & sidecar context cleanly before archiving.

3. **Record, Export to D: Vault & Clean**:
   - `record_session_zones()` and `record_trade_execution()` automatically:
     - Update `trader_dna/data/trader_dna_profile.json`.
     - Permanently export to `D:\Tomoko_Trader_Vault\dataset\` (`images/`, `labels/`, `train_vlm.jsonl`, `train_llm.jsonl`) — **never purged**.
     - Move the processed image to `trader_dna/archive/current_week/` and clean up its dropoff `.context.json` sidecar.
   - Present a concise institutional summary table confirming extracted zones/trades and permanent D: vault export.
