---
name: markup-ingest
description: >-
  Ingests the trader's morning/pre-session TradingView chart markups from markups/ folder,
  extracts drawn zones and 50% CE midlines, archives images, and logs them into data/trader_profile.json
  as benchmark ground-truth for weekend comparison and algorithm fine-tuning (isolated from live Tomoko engines).
  Triggered whenever the user enters "/ingest", "ingest", or asks to ingest chart markups.
---

# Chart Markup Ingestion Pipeline (/ingest)

Use this workflow to ingest trader-drawn TradingView chart markups as benchmark data for weekend comparison and fine-tuning.

## Ingestion Procedure

1. **Check Dropzone**:
   - Inspect `markups/` directory for new image snapshots (`.png`, `.jpg`, `.jpeg`, `.webp`).
   - If empty, inform the user to save TradingView chart snapshots there.
   - If multiple files exist, sort descending by Windows creation time and select up to the **3 latest snapshots**.

2. **Native Vision Inspection**:
   - Open each snapshot using `view_file` (0 external API quota used).
   - Use countdown timer under the blue price badge to classify timeframe:
     - `> 60:00` or hours format -> H4
     - `15:01 – 60:00` -> H1
     - `≤ 15:00` -> M15 (or M5 if labeled)
   - Extract Upper Zone 2, Upper Zone 1, Lower Zone 1, Lower Zone 2, price bounds, and purple 50% CE midlines.

3. **Record & Clean (Benchmark Dataset)**:
   - Update `data/trader_profile.json` (active_session and history).
   - **Zero Live Interference**: Ingested data is strictly kept for weekend comparison and fine-tuning; it is never injected into Tomoko's live trading execution or live chart.
   - Move processed files to `markups/archive/YYYY-MM-DD/`.
   - Auto-delete archives older than 3 days.
   - Present a concise institutional briefing table.
