import json, csv, os
from datetime import datetime
from pathlib import Path


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

    def can_trade(self):
        # Enforce micro-hardened: max 3 per day, 1 open pos
        today = datetime.now().strftime("%Y-%m-%d")
        trades = self.get_trades()
        today_trades = [t for t in trades if t['timestamp'].startswith(today)]
        # Count open (no close yet) - simple: if last 3 have no exit, count
        open_positions = len([t for t in today_trades if t.get('status') == 'OPEN'])
        if len(today_trades) >= 3:
            return False, f"MAX DAILY TRADES 3 reached - {len(today_trades)}/3 today"
        if open_positions >= 1:
            return False, f"MAX OPEN POS 1 reached - close position first"
        return True, "OK"

    def log_trade(self, data):
        # data dict from dashboard: symbol, price, h4_gate, brain_score, etc.
        can, reason = self.can_trade()
        if not can:
            return False, reason
        entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "OPEN",
            **data
        }
        trades = self.get_trades()
        trades.append(entry)
        self.json_path.write_text(json.dumps(trades, indent=2), encoding='utf-8')
        # Append CSV
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

    def get_trades(self):
        try:
            return json.loads(self.json_path.read_text(encoding='utf-8'))
        except Exception:
            return []

    def get_count(self):
        return len(self.get_trades())
