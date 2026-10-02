# PerformanceTracker - simple private CSV logger for live signal QA (no auth, no scraping)
import csv
import os
from datetime import datetime


class PerformanceTracker:
    def __init__(self, file="logs/brain_signals.csv"):
        os.makedirs(os.path.dirname(file) or ".", exist_ok=True)
        self.file = file
        if not os.path.exists(file):
            with open(file, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow([
                    "timestamp", "symbol", "price", "action", "score",
                    "weekly_open", "liq_high", "liq_low",
                    "news_is_clean", "fund_bias", "notes",
                ])

    def log(self, data):
        # data is the enriched scan dict from /api/scan/{symbol}
        with open(self.file, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                data.get("symbol"),
                data.get("price"),
                data.get("action"),
                data.get("score"),
                (data.get("levels") or {}).get("weekly_open"),
                (data.get("levels") or {}).get("liq_high"),
                (data.get("levels") or {}).get("liq_low"),
                (data.get("news") or {}).get("is_clean"),
                (data.get("fundamental") or {}).get("bias"),
                "",  # notes you fill manually
            ])
