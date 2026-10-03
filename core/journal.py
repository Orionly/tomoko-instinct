import json, csv, os, uuid
from datetime import datetime, timedelta
from pathlib import Path

import config


class Journal:
    def __init__(self, json_path="journal/trades.json", csv_path="journal/trades.csv"):
        self.json_path = Path(json_path)
        self.csv_path = Path(csv_path)
        self.json_path.parent.mkdir(exist_ok=True)
        if not self.json_path.exists():
            self.json_path.write_text("[]")
        # CSV header
        if not self.csv_path.exists():
            with open(self.csv_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp", "symbol", "direction", "price", "h4_gate", "h4_dir", "adx", "atr_ratio", "dist_ema21", "rsi", "brain_score", "action", "dxy", "us10y", "vix", "spx", "gold", "risk", "session", "balance", "notes"])

    # ------------------------------------------------------------------ #
    # Persistence helpers
    # ------------------------------------------------------------------ #

    def _save(self, trades):
        self.json_path.write_text(json.dumps(trades, indent=2), encoding='utf-8')

    def _mark_stale_opens(self, trades):
        """Flag OPEN trades older than JOURNAL_STALE_HOURS as STALE.

        A stale OPEN no longer counts toward the max-open-position guard, so a
        forgotten trade can't permanently block new entries.
        """
        now = datetime.now()
        changed = False
        for t in trades:
            if t.get("status") != "OPEN":
                continue
            try:
                opened = datetime.strptime(t.get("timestamp", ""), "%Y-%m-%d %H:%M:%S")
            except (TypeError, ValueError):
                continue
            if (now - opened) > timedelta(hours=config.JOURNAL_STALE_HOURS):
                t["status"] = "STALE"
                changed = True
        if changed:
            self._save(trades)
        return trades

    # ------------------------------------------------------------------ #
    # Guards
    # ------------------------------------------------------------------ #

    def can_trade(self):
        # Enforce micro-hardened: max N per day, 1 open pos (stale excluded)
        trades = self._mark_stale_opens(self.get_trades())
        today = datetime.now().strftime("%Y-%m-%d")
        today_trades = [t for t in trades if str(t.get('timestamp', '')).startswith(today)]
        open_positions = [t for t in trades if t.get('status') == 'OPEN']
        if len(today_trades) >= config.MAX_DAILY_TRADES:
            return False, f"MAX DAILY TRADES {config.MAX_DAILY_TRADES} reached - {len(today_trades)}/{config.MAX_DAILY_TRADES} today"
        if len(open_positions) >= config.MAX_OPEN_POSITIONS:
            return False, f"MAX OPEN POS {config.MAX_OPEN_POSITIONS} reached - close position first"
        return True, "OK"

    # ------------------------------------------------------------------ #
    # Trade lifecycle
    # ------------------------------------------------------------------ #

    def log_trade(self, data):
        # data dict from dashboard: symbol, price, h4_gate, brain_score, etc.
        can, reason = self.can_trade()
        if not can:
            return False, reason
        entry = {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "OPEN",
            **data
        }
        trades = self.get_trades()
        trades.append(entry)
        self._save(trades)
        # Append CSV (append-only log; exit data lives in JSON)
        with open(self.csv_path, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                entry['timestamp'], entry.get('symbol'), entry.get('direction'), entry.get('price'),
                entry.get('h4_gate'), entry.get('h4_dir'), entry.get('adx'), entry.get('atr_ratio'),
                entry.get('dist_ema21'), entry.get('rsi'), entry.get('brain_score'), entry.get('action'),
                entry.get('dxy'), entry.get('us10y'), entry.get('vix'), entry.get('spx'), entry.get('gold'),
                entry.get('risk'), entry.get('session'), entry.get('balance'), entry.get('notes', '')
            ])
        return True, f"Logged #{len(trades)} {data['symbol']} {data['direction']} @ {data['price']}"

    def close_trade(self, trade_id=None, symbol=None, direction=None, exit_price=None,
                    exit_time=None, pnl=None):
        """Close an OPEN trade (by id, or most recent OPEN matching symbol/direction)."""
        trades = self.get_trades()
        target = None
        if trade_id is not None:
            target = next((t for t in trades if t.get("id") == trade_id), None)
        if target is None:
            candidates = [t for t in trades if t.get("status") == "OPEN"]
            if symbol is not None:
                candidates = [t for t in candidates if t.get("symbol") == symbol]
            if direction is not None:
                candidates = [t for t in candidates if t.get("direction") == direction]
            if not candidates:
                return False, "No open trade to close"
            target = candidates[-1]  # most recent
        target["status"] = "CLOSED"
        target["exit_time"] = exit_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if exit_price is not None:
            target["exit_price"] = exit_price
        if pnl is not None:
            target["pnl"] = pnl
        else:
            entry = self._num(target.get("price"))
            exit_p = self._num(exit_price)
            if entry and exit_p:
                target["pnl"] = round(exit_p - entry, 5) if target.get("direction") == "BUY" else round(entry - exit_p, 5)
            else:
                target["pnl"] = None
        self._save(trades)
        return True, f"Closed {target.get('symbol')} at {target.get('exit_price')}"

    def get_trades(self):
        try:
            return json.loads(self.json_path.read_text(encoding='utf-8'))
        except Exception:
            return []

    def get_count(self):
        return len(self.get_trades())

    @staticmethod
    def _num(v):
        try:
            f = float(v)
            if f != f:  # NaN
                return 0.0
            return f
        except (TypeError, ValueError):
            return 0.0