---
name: telegram-dna
description: >-
  Runs Tomoko's daily End-of-Day Telegram Trader DNA ingestion pipeline via Telethon (`scripts/trader_dna_telegram.py --days 2 --download-charts`), discarding duplicate signals/chats/PnL flex screenshots, auto-finalizing pending late-NY outcomes, tracing back M15/H1 candles at exact signal timestamps, archiving into trader_dna/data/trader_dna_profile.json + D:\Tomoko_Trader_Vault\dataset\, and optionally inspecting downloaded chart-mapping images with Native Vision.
  Triggered whenever the user enters "/tgdna", "tgdna", or asks to pull today's Telegram signals into Trader DNA.
---

# Daily End-of-Day Telegram Trader DNA Ingestion (/tgdna)

Use this workflow at the end of each trading day before stopping to pull the target Telegram channel's signals into Tomoko's Trader DNA and permanent LLM/VLM vault.

## Procedure

1. **Determine Pull Window**:
   - Default to `--days 2` (48-hour rolling sweep) so late-night NY signals posted after yesterday's EOD run are never missed.
   - Strict deduplication is built into `scripts/trader_dna_telegram.py` by BOTH `telegram_msg_id` AND content signature `(direction, entry_zone_low, entry_zone_high, stop_loss, chart_date)`, plus persistent `telegram_seen_chart_ids`, so replicates are always discarded.
   - If the user passes a number (e.g., `/tgdna 7`), use `--days 7`. If the user passes `dry-run` (e.g., `/tgdna dry-run`), add `--dry-run`.

2. **Execute Telethon EOD Pull**:
   - Run:
     ```powershell
     python scripts/trader_dna_telegram.py --days 2 --download-charts
     ```
   - This automatically:
     1. Re-audits any previously saved `PENDING_RECENT` / `OPEN_OR_EXPIRED` Telegram signals against the latest `logs/candles_cache.pkl`.
     2. Pulls the last 48h of messages from `TELEGRAM_TARGET_GROUP` (configured in `config_local.py`) using the saved session `trader_dna/data/tomoko_telegram.session`.
     3. Discards duplicate signals, admin fat-finger typos, unrelated chats, and PnL/feedback screenshots.
     4. Traces each new signal back on M15/H1 candles at exact `msg.date` (zero lookahead), audits forward `TP_HIT` / `SL_HIT` / `NO_FILL`, and saves via `record_trade_execution()` to `trader_dna/data/trader_dna_profile.json` and `D:\Tomoko_Trader_Vault\dataset\`.
     5. Downloads any new genuine chart-mapping images into `trader_dna/dropoff/` stamped with the original Telegram timestamp.

3. **Optional Native Vision Inspection for Downloaded Chart Mappings**:
   - If new `TG_*.jpg` chart-mapping images were queued in `trader_dna/dropoff/`, inspect them directly with Antigravity's native `read_file` vision tool (zero external API quota):
     - If an image is a real TradingView/MT5 chart markup with clear zones or setups, ingest it using the `/dna` procedure (`record_session_zones` / `record_trade_execution`).
     - If an image turns out to be a promotional banner or non-chart graphic, delete it from `trader_dna/dropoff/` so it never pollutes Trader DNA.

4. **EOD Summary Output**:
   - Present a concise markdown summary table showing:
     - New signals ingested (Msg ID, MYT Timestamp, Direction, Entry Zone, SL, TP, R:R, Live Price at Call, Tomoko Confluence, EOD Outcome)
     - Duplicates / Chats / PnL screenshots discarded
     - Pending signals refreshed
     - Total Trader DNA progress toward Weekend ML stages (Stage 1.5 LogReg at 30+, Stage 2 LightGBM at 80+).
