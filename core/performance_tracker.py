# PerformanceTracker - simple private CSV logger for live signal QA (no auth, no scraping)
# Auto-rotates: when the file exceeds MAX_FILE_SIZE it is renamed to
# brain_signals_YYYYMMDD.csv (with an index suffix on collision) and a fresh file
# starts. Rotated files older than KEEP_DAYS are pruned.
import csv
import glob
import os
import threading
import time
from datetime import datetime

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
KEEP_DAYS = 30
_HEADER = [
    "timestamp", "symbol", "price", "action", "score",
    "weekly_open", "liq_high", "liq_low",
    "news_is_clean", "fund_bias", "notes",
]
_lock = threading.Lock()


class PerformanceTracker:
    def __init__(self, file="logs/brain_signals.csv"):
        os.makedirs(os.path.dirname(file) or ".", exist_ok=True)
        self.file = file
        if not os.path.exists(file):
            self._write_header()

    def _write_header(self):
        with open(self.file, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(_HEADER)

    def _rotate_if_needed(self):
        try:
            size = os.path.getsize(self.file)
        except OSError:
            size = 0
        if size < MAX_FILE_SIZE:
            return
        base, ext = os.path.splitext(self.file)
        stamp = datetime.now().strftime("%Y%m%d")
        rotated = f"{base}_{stamp}{ext}"
        n = 1
        while os.path.exists(rotated):
            rotated = f"{base}_{stamp}_{n}{ext}"
            n += 1
        try:
            os.replace(self.file, rotated)
        except OSError:
            return
        self._write_header()
        self._prune_old()

    def _prune_old(self):
        base, _ = os.path.splitext(self.file)
        cutoff = time.time() - KEEP_DAYS * 86400
        for path in glob.glob(base + "_*.csv"):
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except OSError:
                pass

    def log(self, data):
        # data is the enriched scan dict from /api/scan/{symbol}
        with _lock:
            self._rotate_if_needed()
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