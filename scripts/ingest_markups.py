#!/usr/bin/env python3
"""
scripts/ingest_markups.py
-------------------------
Backwards compatibility wrapper for Trader DNA Ingestion.
Calls main() in scripts.trader_dna_ingest.
"""
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scripts.trader_dna_ingest import main

if __name__ == "__main__":
    main()
