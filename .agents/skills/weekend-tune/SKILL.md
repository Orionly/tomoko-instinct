---
name: weekend-tune
description: >-
  Runs Tomoko's offline Weekend Comparison & ML Fine-Tuning suite (`scripts/weekend_comparison.py --tune --purge-archive`) after finalizing any open Friday Telegram outcomes. Audits zone markups + point-in-time executed trades, runs Optuna Bayesian optimization + Stage 1.5 Logistic Regression / Stage 2 LightGBM calibration + Entry DNA calibration, purges temporary C: weekly archives while preserving trader_dna_profile.json and D:\Tomoko_Trader_Vault\dataset\, and reports a concise weekend scorecard.
  Triggered whenever the user enters "/tune", "tune", or asks to run weekend ML calibration / weekend comparison.
---

# Weekend Comparison & Offline ML Calibration (/tune)

Use this workflow on weekends (Saturday/Sunday) after the trading week closes to calibrate Tomoko's Zone Engine (`data/tuned_zone_params.json`, `data/models/dna_zone_scorer.pkl`) and Battle-Plan Entry DNA (`data/tuned_entry_dna.json`) from the week's accumulated `/dna` markups and `/tgdna` Telegram signals.

## Procedure

1. **Finalize Open Friday Telegram Signals**:
   - First run a quick deduplicated sweep so any Friday NY-session signals that were `PENDING_RECENT` are finalized against the closed weekly candles:
     ```powershell
     python scripts/trader_dna_telegram.py --days 2
     ```

2. **Run Weekend Comparison & ML Fine-Tuning**:
   - Default (live calibration + purge temporary `trader_dna/archive/current_week/`):
     ```powershell
     python scripts/weekend_comparison.py --tune --purge-archive
     ```
   - If the user passes `dry-run` (e.g., `/tune dry-run`):
     ```powershell
     python scripts/weekend_comparison.py --tune --dry-run
     ```
   - If the user passes `reset` (e.g., `/tune reset`):
     ```powershell
     python scripts/weekend_comparison.py --reset-defaults
     ```

3. **Weekend Scorecard Output**:
   - Present a clean, concise executive summary covering:
     - **ML Stage & Progression** (`STAGE_1_OPTUNA`, `STAGE_1_5_LOGREG`, or `STAGE_2_LIGHTGBM`) and sample count
     - **Before vs. After Scorecard** (Zone Match Recall %, Mean IoU, 50% CE Offset)
     - **Point-in-Time Trade Confluence & Audited Win Rate** (`TP_HIT` vs `SL_HIT`)
     - **Parameter Calibrations Applied** (Zone Engine `tuned_zone_params.json` + Battle-Plan Entry DNA `tuned_entry_dna.json`)
     - **Archive & Vault Status** confirming `trader_dna/archive/current_week/` was cleaned while `trader_dna/data/trader_dna_profile.json` and `D:\Tomoko_Trader_Vault\dataset\` remain permanently intact.
