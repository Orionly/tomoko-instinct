"""
core/markup_ingestion_engine.py
---------------------------------
Backwards compatibility wrapper for Trader DNA Engine.
Directs all dropoff and profile operations to `core.trader_dna_engine`.
"""

from core.trader_dna_engine import (
    TRADER_DNA_DIR,
    DROPOFF_DIR,
    DNA_ARCHIVE_DIR,
    CURRENT_WEEK_ARCHIVE,
    DNA_DATA_DIR,
    DNA_PROFILE_FILE,
    load_trader_dna_profile,
    save_trader_dna_profile,
    load_trader_profile,
    save_trader_profile,
    parse_chart_image,
    purge_weekly_archive,
    get_pending_markups,
    record_session_zones,
    ingest_pending_markups,
)

# Legacy alias mappings
MARKUPS_DIR = DROPOFF_DIR
ARCHIVE_DIR = DNA_ARCHIVE_DIR
DATA_DIR = DNA_DATA_DIR
PROFILE_FILE = DNA_PROFILE_FILE

if __name__ == "__main__":
    res = ingest_pending_markups()
    if not res:
        print("No pending chart markups found in trader_dna/dropoff/ folder.")
    else:
        print(f"Successfully ingested {len(res)} chart markup(s) into Trader DNA profile!")
        for r in res:
            print(f"  {r.get('symbol')} {r.get('timeframe')}: {len(r.get('zones', []))} zones mapped.")
