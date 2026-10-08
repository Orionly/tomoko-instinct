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
    def __init__(self, file="logs/brain_signals.csv", min_score_delta=3, heartbeat_seconds=300):
        os.makedirs(os.path.dirname(file) or ".", exist_ok=True)
        self.file = file
        self.min_score_delta = min_score_delta
        self.heartbeat_seconds = heartbeat_seconds
        self._last_logged = {}  # {symbol: {"action": str, "score": float, "bias": str, "time": float}}
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

    def _should_log(self, symbol, action, score, bias):
        now = time.time()
        last = self._last_logged.get(symbol)
        if last is None:
            return True
        if action != last.get("action"):
            return True
        try:
            curr_s = float(score) if score is not None else 0.0
            last_s = float(last.get("score")) if last.get("score") is not None else 0.0
            if abs(curr_s - last_s) >= self.min_score_delta:
                return True
        except (ValueError, TypeError):
            pass
        if bias != last.get("bias"):
            return True
        if (now - last.get("time", 0.0)) >= self.heartbeat_seconds:
            return True
        return False

    def log(self, data):
        # data is the enriched scan dict from /api/scan/{symbol}
        if not data or not isinstance(data, dict):
            return False
        symbol = data.get("symbol")
        if not symbol:
            return False

        action = data.get("action")
        score = data.get("score")
        bias = (data.get("fundamental") or {}).get("bias")

        with _lock:
            if not self._should_log(symbol, action, score, bias):
                return False

            self._rotate_if_needed()
            with open(self.file, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow([
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    symbol,
                    data.get("price"),
                    action,
                    score,
                    (data.get("levels") or {}).get("weekly_open"),
                    (data.get("levels") or {}).get("liq_high"),
                    (data.get("levels") or {}).get("liq_low"),
                    (data.get("news") or {}).get("is_clean"),
                    bias,
                    "",  # notes you fill manually
                ])
            self._last_logged[symbol] = {
                "action": action,
                "score": score,
                "bias": bias,
                "time": time.time(),
            }
            return True

    def get_recent_logs(self, n=100):
        if not os.path.exists(self.file):
            return []
        with _lock:
            try:
                size = os.path.getsize(self.file)
                if size == 0:
                    return []
                # For small files (<= 128KB), read directly
                if size <= 128 * 1024:
                    with open(self.file, "r", encoding="utf-8", errors="replace") as f:
                        return list(csv.DictReader(f))[-n:]

                # For larger files, seek backward to only read the tail chunk
                chunk_size = min(size, max(64 * 1024, n * 512))
                with open(self.file, "rb") as f:
                    f.seek(size - chunk_size)
                    raw = f.read(chunk_size)

                text = raw.decode("utf-8", errors="replace")
                lines = text.splitlines()
                # If we didn't start at byte 0, the first line is likely partial/truncated
                if size > chunk_size and len(lines) > 1:
                    lines = lines[1:]

                rows = []
                for row in csv.reader(lines):
                    if not row or row == _HEADER:
                        continue
                    row_dict = {
                        col: (row[i] if i < len(row) else "")
                        for i, col in enumerate(_HEADER)
                    }
                    rows.append(row_dict)
                return rows[-n:]
            except Exception:
                return []