# Tomoko Brain Scanner - Desktop Dashboard V1 (Option A: Synchronized Client)
# Dark terminal, manual-first
# Run: python ui/dashboard.py

import os
import sys
import json
import time
import threading
from pathlib import Path
from datetime import datetime
import requests
import customtkinter as ctk

# Ensure project root is importable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config
from core.mt5_bridge import MT5Bridge
from core.journal import Journal

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


def _ev_time(s):
    """EA calendar time 'YYYY.MM.DD HH:MM' -> 'HH:MM'."""
    parts = str(s or "").split()
    return parts[-1][:5] if parts else "--:--"


def _ev_imp(v):
    """EA importance (HIGH/MEDIUM/LOW or numeric) -> display label."""
    if v is None:
        return "LOW"
    s = str(v).upper()
    if "HIGH" in s or s in ("2", "3"):
        return "HIGH"
    if "MED" in s or s == "1":
        return "MED"
    return "LOW"


class PairCard(ctk.CTkFrame):
    def __init__(self, master, symbol, on_select, mt5_bridge, regime_engine, context_feed, levels_engine, journal, app_ref):
        super().__init__(master, corner_radius=8, border_width=1, border_color="#1f2937", fg_color="#151c2c")
        self.configure(width=360, height=260)
        self.grid_propagate(False)
        self.symbol = symbol
        self.on_select = on_select
        self.mt5 = mt5_bridge
        self.regime = regime_engine
        self.context_feed = context_feed
        self.levels_engine = levels_engine
        self.journal = journal
        self.app_ref = app_ref
        self.current_scan = None
        self.current_score = 0
        self.current_action = "WAIT"

        self.grid_columnconfigure(0, weight=1)

        # Header (fixed width so price/score text changes never reflow the card)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.configure(width=340)
        header.pack_propagate(False)
        header.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(header, text=symbol, font=("JetBrains Mono", 14, "bold"), text_color="#00e5ff", width=110, anchor="w").pack(side="left")
        self.price_label = ctk.CTkLabel(header, text="--.-----", font=("JetBrains Mono", 15, "bold"), width=110, anchor="w")
        self.price_label.pack(side="left", padx=10)
        self.score_circle = ctk.CTkLabel(header, text=" -- ", font=("JetBrains Mono", 14, "bold"), fg_color="#1f2937", corner_radius=12, width=48, anchor="center")
        self.score_circle.pack(side="right")

        # MTF bias strip (H4 / H1 / M15)
        self.mtf_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.mtf_frame.pack(fill="x", padx=12, pady=(4, 2))
        self.badge_h4 = ctk.CTkLabel(self.mtf_frame, text="H4 ...", font=("Inter", 11, "bold"), fg_color="#1f2937", corner_radius=6, height=22, padx=6)
        self.badge_h4.pack(side="left", padx=(0, 4))
        self.badge_h1 = ctk.CTkLabel(self.mtf_frame, text="H1 ...", font=("Inter", 11, "bold"), fg_color="#1f2937", corner_radius=6, height=22, padx=6)
        self.badge_h1.pack(side="left", padx=(0, 4))
        self.badge_m15 = ctk.CTkLabel(self.mtf_frame, text="M15 ...", font=("Inter", 11, "bold"), fg_color="#1f2937", corner_radius=6, height=22, padx=6)
        self.badge_m15.pack(side="left")

        # Structural
        self.info_text = ctk.CTkLabel(self, text="Waiting for live Brain scan...", font=("JetBrains Mono", 13), justify="left", anchor="w", text_color="#a1a1aa", wraplength=340, height=80)
        self.info_text.pack(fill="x", padx=12, pady=4)

        # Action
        self.action_label = ctk.CTkLabel(self, text="WAIT", font=("Inter", 13, "bold"), fg_color="#1f2937", corner_radius=6, height=32)
        self.action_label.pack(fill="x", padx=12, pady=(4, 8))

        self.reason_label = ctk.CTkLabel(self, text="Initializing...", font=("Inter", 11), text_color="#71717a", wraplength=320, justify="left", anchor="w")
        self.reason_label.pack(fill="x", padx=12, pady=(0, 10))

        # Buttons
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkButton(btn_row, text="Log Trade", width=80, height=24, font=("Inter", 10), fg_color="#1e293b", command=self.log_trade).pack(side="left", padx=(0, 6))
        ctk.CTkButton(btn_row, text="Deep Dive", width=80, height=24, font=("Inter", 10), fg_color="#00e5ff", text_color="black", command=lambda: on_select(symbol)).pack(side="left")

    def _bias_color(self, bias):
        b = str(bias or "").capitalize()
        return "#22c55e" if b == "Bullish" else "#ef4444" if b == "Bearish" else "#ffb800"

    def update_from_scan(self, scan_data):
        if not scan_data:
            return
        self.current_scan = scan_data
        price = scan_data.get("price")
        score = scan_data.get("score", 0)
        action = scan_data.get("action", "WAIT")
        reason = scan_data.get("action_reason", "")
        h4 = scan_data.get("h4", {})
        h1 = scan_data.get("h1", {})
        regime = scan_data.get("regime", {})
        levels = scan_data.get("levels", {})

        # 1. Price
        if price is not None:
            if "JPY" in self.symbol or "XAU" in self.symbol:
                price_text = f"{float(price):.3f}"
            else:
                price_text = f"{float(price):.5f}"
        else:
            price_text = "NO DATA"
        self.price_label.configure(text=price_text.ljust(10))

        # 2. Score
        self.score_circle.configure(text=f" {score:>3} ")
        if score < 40:
            self.score_circle.configure(fg_color="#ef4444", text_color="white")
        elif score < 70:
            self.score_circle.configure(fg_color="#ffb800", text_color="black")
        else:
            self.score_circle.configure(fg_color="#00e5ff", text_color="black")

        # 3. MTF badges
        h4_b = regime.get("H4_bias", "Neutral")
        h1_b = regime.get("H1_bias", "Neutral")
        m15_b = regime.get("M15_bias", "Neutral")
        self.badge_h4.configure(text=f"H4 {h4_b}", fg_color=self._bias_color(h4_b), text_color="black" if h4_b != "Neutral" else "white")
        self.badge_h1.configure(text=f"H1 {h1_b}", fg_color=self._bias_color(h1_b), text_color="black" if h1_b != "Neutral" else "white")
        self.badge_m15.configure(text=f"M15 {m15_b}", fg_color=self._bias_color(m15_b), text_color="black" if m15_b != "Neutral" else "white")

        # 4. Info text
        liq_h = levels.get("liq_high", 0)
        liq_l = levels.get("liq_low", 0)
        info = (
            f"H4 {h4.get('gate', '--')} {h4.get('direction', '--')} | ADX {h4.get('adx', 0)} ATRx{h4.get('atr_ratio', 0)}\n"
            f"H1 Dist {h1.get('dist_ema21_atr', 0)} ATR | M15 {m15_b}\n"
            f"Liq: Buys above {liq_h:.2f} Sells below {liq_l:.2f}"
        )
        self.info_text.configure(text=info)

        # 5. Action label
        self.action_label.configure(text=action)
        if action.startswith("LIMIT AT LIQ"):
            self.action_label.configure(fg_color="#ffb800", text_color="black")
        elif "BUY" in action:
            self.action_label.configure(fg_color="#15803d", text_color="white")
        elif "SELL" in action:
            self.action_label.configure(fg_color="#b91c1c", text_color="white")
        else:
            self.action_label.configure(fg_color="#1f2937", text_color="white")

        self.reason_label.configure(text=reason[:120] if reason else "")
        self.current_score = score
        self.current_action = action
        self.app_ref.pairs_data[self.symbol] = {"action": action, "reason": reason}

    def log_trade(self):
        if not self.current_scan:
            print(f"No scan data for {self.symbol} yet to log")
            return
        sc = self.current_scan
        h4 = sc.get('h4', {})
        h1 = sc.get('h1', {})
        context = self.app_ref.context_cache
        ctx_items = context.get('context', {})
        risk = context.get('risk', 'NEUTRAL')
        session_name = context.get('session', 'LONDON')
        direction = h4.get('direction', 'UP')
        balance = getattr(self.app_ref, 'current_balance', 0.0)

        data = {
            "symbol": self.symbol,
            "direction": direction,
            "price": sc.get('price', 0.0),
            "h4_gate": h4.get('gate', '--'),
            "h4_dir": direction,
            "adx": h4.get('adx', 0),
            "atr_ratio": h4.get('atr_ratio', 0),
            "dist_ema21": h1.get('dist_ema21_atr', 0),
            "rsi": h1.get('rsi', 0),
            "brain_score": sc.get('score', 0),
            "action": sc.get('action', 'WAIT'),
            "dxy": ctx_items.get('DXY', {}).get('change_pct', 0),
            "us10y": ctx_items.get('US10Y', {}).get('change_pct', 0),
            "vix": ctx_items.get('VIX', {}).get('change_pct', 0),
            "spx": ctx_items.get('SPX', {}).get('change_pct', 0),
            "gold": ctx_items.get('GOLD', {}).get('price', 0),
            "risk": risk,
            "session": session_name,
            "balance": balance,
            "notes": f"Manual log from brain - {self.symbol} H4 {h4.get('gate')} ADX {h4.get('adx')}"
        }
        self.app_ref.open_journal_modal(data)


class TomokoBrainApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("TOMOKO // BRAIN v1 - INFORMATION FIRST")
        self.geometry("1920x1080")

        self.api_url = f"http://{config.API_HOST}:{config.API_PORT}"
        self.mt5 = MT5Bridge()
        try:
            self.mt5.connect()
        except Exception:
            pass
        self.regime = None
        self.context_feed = None
        self.levels_engine = None
        self.journal = Journal(config.JOURNAL_PATH, config.JOURNAL_CSV)
        self.pairs_data = {}
        self.scans_cache = {}
        self.context_cache = {}
        self.current_balance = 0.0
        self.selected_symbol = None
        self._stop_event = threading.Event()

        # Top Context Strip
        top = ctk.CTkFrame(self, fg_color="#0a0e14", height=50)
        top.pack(fill="x", padx=0, pady=0)

        left_top = ctk.CTkFrame(top, fg_color="transparent")
        left_top.pack(side="left", padx=16, pady=8)
        ctk.CTkLabel(left_top, text="TOMOKO // BRAIN v1", font=("JetBrains Mono", 16, "bold"), text_color="#00e5ff").pack(side="left")
        self.balance_label = ctk.CTkLabel(left_top, text="  -- USC • MICRO • 1 MAX POS • LOCAL MT5", font=("JetBrains Mono", 11), text_color="#71717a")
        self.balance_label.pack(side="left", padx=10)

        # Context Strip - DXY, US10Y, VIX, SPX, OIL, GOLD (real data)
        self.context_frame = ctk.CTkFrame(self, fg_color="#111827", height=36, border_width=1, border_color="#1f2937")
        self.context_frame.pack(fill="x", padx=10, pady=4)
        self.context_labels = {}
        for key in ["DXY", "US10Y", "VIX", "SPX", "OIL", "GOLD"]:
            label = ctk.CTkLabel(self.context_frame, text=f"{key} loading...", font=("JetBrains Mono", 10), text_color="#e4e4e7")
            label.pack(side="left", padx=12, pady=6)
            self.context_labels[key] = label

        # Session + News
        news_bar = ctk.CTkFrame(self, fg_color="#151c2c", height=28)
        news_bar.pack(fill="x", padx=10, pady=2)
        self.news_label = ctk.CTkLabel(news_bar, text="SESSION: connecting to Brain Engine...", font=("JetBrains Mono", 11), text_color="#ffb800")
        self.news_label.pack(side="left", padx=12)

        # Main content (scrollable + responsive)
        self.main_scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.main_scroll.pack(fill="both", expand=True, padx=10, pady=10)
        main = ctk.CTkFrame(self.main_scroll, fg_color="transparent")
        main.pack(fill="both", expand=True)
        self.main = main
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(0, weight=1)

        # Pair cards container
        self.cards_frame = ctk.CTkFrame(main, fg_color="transparent")
        self.cards_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        self.pairs = config.PAIRS  # 7 pairs
        self.pair_cards = []
        for sym in self.pairs:
            card = PairCard(self.cards_frame, sym, self.on_select_pair, self.mt5, self.regime, self.context_feed, self.levels_engine, self.journal, self)
            self.pair_cards.append(card)

        # Right Deep Dive (scrollable, wider to prevent clipping)
        self.deep = ctk.CTkScrollableFrame(main, fg_color="#111827", border_width=1, border_color="#1f2937", corner_radius=8, width=420)
        self.deep.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

        self.current_cols = 4
        self._last_width = 0
        self.deep.grid_columnconfigure(0, weight=1)

        self.deep_title = ctk.CTkLabel(self.deep, text="DEEP DIVE // SELECT PAIR", font=("JetBrains Mono", 14, "bold"), text_color="#00e5ff", wraplength=360, justify="left", anchor="w")
        self.deep_title.pack(fill="x", padx=16, pady=(16, 8), anchor="w")

        # Single real-data label (wired to synchronized engine scan)
        self.deep_label = ctk.CTkLabel(self.deep, text="Select a pair card to view full MTF analysis, strategy triggers, and key levels.", font=("JetBrains Mono", 12), justify="left", anchor="w", wraplength=380, text_color="#e4e4e7")
        self.deep_label.pack(fill="x", padx=16, pady=10, anchor="w")

        # Bottom
        bottom = ctk.CTkFrame(self, fg_color="#0a0e14", height=24)
        bottom.pack(fill="x", side="bottom")
        self.bottom_label = ctk.CTkLabel(bottom, text="Bot = Hands & Feet. Brain = Information. No brain = blowout.  |  Manual trades: 0/100 until auto", font=("JetBrains Mono", 10), text_color="#52525b")
        self.bottom_label.pack(side="left", padx=16, pady=4)

        # Responsive layout bindings
        self.minsize(800, 600)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.bind("<Configure>", self.rebuild_grid)
        self.after(200, self.rebuild_grid)

        # Protocol for clean exit
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # Start non-blocking background polling worker thread
        self._poll_thread = threading.Thread(target=self._background_poll_loop, daemon=True)
        self._poll_thread.start()

    def on_close(self):
        self._stop_event.set()
        self.destroy()

    def _background_poll_loop(self):
        while not self._stop_event.is_set():
            scan_data = None
            ctx_data = None
            bal = None

            try:
                r_scan = requests.get(f"{self.api_url}/api/scan_all", timeout=3.5)
                if r_scan.status_code == 200:
                    scan_data = r_scan.json()
            except Exception:
                scan_data = None

            try:
                r_ctx = requests.get(f"{self.api_url}/api/context", timeout=3.5)
                if r_ctx.status_code == 200:
                    ctx_data = r_ctx.json()
            except Exception:
                ctx_data = None

            try:
                if self.mt5 and self.mt5.connected:
                    bal = self.mt5.get_balance()
            except Exception:
                bal = None

            if not self._stop_event.is_set():
                try:
                    self.after(0, self._apply_snapshot, scan_data, ctx_data, bal)
                except Exception:
                    pass

            for _ in range(30):
                if self._stop_event.is_set():
                    break
                time.sleep(0.1)

    def _apply_snapshot(self, scan_data, ctx_data, bal):
        if bal is not None:
            self.current_balance = bal
            self.balance_label.configure(text=f"  {bal:.2f} USC • MICRO • 1 MAX POS • LOCAL MT5")

        if ctx_data and isinstance(ctx_data, dict):
            self.context_cache = ctx_data
            context = ctx_data.get("context", {})
            for key in ["DXY", "US10Y", "VIX", "SPX", "OIL", "GOLD"]:
                if key in self.context_labels:
                    d = context.get(key, {})
                    price = d.get("price", 0.0)
                    chg = d.get("change_pct", 0.0)
                    arrow = "↑" if d.get("trend") == "UP" else "↓" if d.get("trend") == "DOWN" else "→"
                    color = "#22c55e" if chg > 0 else "#ef4444" if chg < 0 else "#e4e4e7"
                    self.context_labels[key].configure(text=f"{key} {price:.2f} {chg:+.2f}% {arrow}", text_color=color)

        if scan_data and isinstance(scan_data, dict) and "scans" in scan_data:
            scans = scan_data.get("scans", [])
            for sc in scans:
                sym = sc.get("symbol")
                if sym:
                    self.scans_cache[sym] = sc
                    self.pairs_data[sym] = {
                        "action": sc.get("action", "WAIT"),
                        "reason": sc.get("action_reason", "")
                    }

            for card in self.pair_cards:
                if card.symbol in self.scans_cache:
                    card.update_from_scan(self.scans_cache[card.symbol])

            blocking = scan_data.get("blocking_news", [])
            news_bar_text = scan_data.get("news_bar_text")
            if news_bar_text:
                self.news_label.configure(text=news_bar_text, text_color="#ef4444" if blocking else "#ffb800")
            else:
                session = self.context_cache.get("session", "LONDON")
                risk = self.context_cache.get("risk", "NEUTRAL")
                events = scan_data.get("calendar_events", [])
                parts = [f"{_ev_time(e.get('time'))} {e.get('currency')} {e.get('event')}" for e in events[:3]]
                ev_str = " | ".join(parts) if parts else "No HIGH events upcoming"
                self.news_label.configure(text=f"SESSION {session} • Risk {risk} | {ev_str}", text_color="#ffb800")

            if self.selected_symbol and self.selected_symbol in self.scans_cache:
                self.update_deep_dive(self.selected_symbol)

        elif not self.scans_cache:
            self.news_label.configure(text=f"CONNECTING TO BRAIN ENGINE ({self.api_url})... [Launch Main.py]", text_color="#ffb800")

        self.update_bottom_bar()

    def update_bottom_bar(self):
        count = self.journal.get_count()
        if hasattr(self, 'bottom_label'):
            self.bottom_label.configure(text=f"Bot = Hands & Feet. Brain = Information. No brain = blowout. | Manual trades: {count}/100 until auto | Synced: {datetime.now().strftime('%H:%M:%S')}")

    def on_select_pair(self, symbol):
        self.selected_symbol = symbol
        self.deep_title.configure(text=f"DEEP DIVE // {symbol}")
        self.update_deep_dive(symbol)

    def update_deep_dive(self, symbol):
        sc = self.scans_cache.get(symbol)
        if not sc:
            self.deep_label.configure(text=f"{symbol} loading live snapshot from Brain Engine...")
            return
        try:
            levels = sc.get('levels', {})
            h4 = sc.get('h4', {})
            h1 = sc.get('h1', {})
            regime = sc.get('regime', {})
            news = sc.get('news', {})
            fund = sc.get('fundamental', {})
            price = sc.get('price', 0.0)
            score = sc.get('score', 0)
            action = sc.get('action', 'WAIT')
            reason = sc.get('action_reason', '')

            text = (
                f"{symbol} Deep Dive [SYNCHRONIZED LIVE]\n\n"
                f"[Key Levels Map - LIVE]\n"
                f"Daily High {levels.get('daily_high', 0):.2f}\n"
                f"Liq High Buy Stops {levels.get('liq_high', 0):.2f} (Daily +0.3 ATR)\n"
                f"Current >>> {price:.2f}\n"
                f"Liq Low Sell Stops {levels.get('liq_low', 0):.2f}\n"
                f"Daily Low {levels.get('daily_low', 0):.2f}\n"
                f"ATR H1 {levels.get('atr', 0):.2f}\n\n"
                f"[MTF Trend - LIVE]\n"
                f"H4 {regime.get('H4_bias', 'Neutral')} Gate {h4.get('gate', '--')} ADX {h4.get('adx', 0)} ATRx{h4.get('atr_ratio', 0)} Dist {h4.get('dist_ema21_atr', 0)} ATR\n"
                f"H1 {regime.get('H1_bias', 'Neutral')} Dist {h1.get('dist_ema21_atr', 0)} ATR Pullback? {h1.get('is_pullback', False)} RSI {h1.get('rsi', 0)}\n"
                f"M15 {regime.get('M15_bias', 'Neutral')} Aligned? {regime.get('aligned', False)}\n\n"
                f"[TrendFollowing V2 Gate]\n"
                f"H4 EMA20 {h4.get('ema20', 0):.2f} EMA50 {h4.get('ema50', 0):.2f} {'bullish' if h4.get('direction') == 'UP' else 'bearish'}\n"
                f"ADX>22? {h4.get('adx', 0) > 22} ATRx>0.8? {h4.get('atr_ratio', 0) > 0.8}\n"
                f"H1 pullback <=0.5 ATR? {h1.get('is_pullback', False)} (actual {h1.get('dist_ema21_atr', 0)})\n\n"
                f"[Liquidity Sweep Rule]\n"
                f"Trigger: H4 OPEN {h4.get('direction', '--')} + ADX>40 + Dist>1.2 ATR\n"
                f"Current ADX {h4.get('adx', 0)} Dist {h1.get('dist_ema21_atr', 0)}\n"
                f"If UP: LIMIT AT LIQ HIGH {levels.get('liq_high', 0):.2f}\n"
                f"If DOWN: LIMIT AT LIQ LOW {levels.get('liq_low', 0):.2f}\n\n"
                f"[Macro & News Confluence]\n"
                f"Fundamental Bias: {fund.get('bias', 'NEUTRAL')} (Score: {fund.get('score', 50)})\n"
                f"News Clean: {news.get('is_clean', True)} (Score: {news.get('score', 100)})\n\n"
                f"[Brain Score & Action]\n"
                f"Score: {score} | Action: {action}\n"
                f"Rationale: {reason}"
            )
            self.deep_label.configure(text=text)
        except Exception as e:
            self.deep_label.configure(text=f"{symbol} Deep Dive render error: {e}")

    def open_journal_modal(self, data):
        can, reason = self.journal.can_trade()
        modal = ctk.CTkToplevel(self)
        modal.title(f"Log Trade - {data['symbol']}")
        modal.geometry("500x600")
        modal.grab_set()

        ctk.CTkLabel(modal, text=f"Log {data['symbol']} {data['direction']} @ {data['price']}", font=("JetBrains Mono", 14, "bold"), text_color="#00e5ff").pack(pady=10)

        info_text = f"""
Symbol: {data['symbol']}
Direction: {data['direction']}
Price: {data['price']}
H4 Gate: {data['h4_gate']} {data['h4_dir']} ADX {data['adx']} ATRx{data['atr_ratio']}
Dist EMA21: {data['dist_ema21']} ATR | RSI {data['rsi']}
Brain Score: {data['brain_score']} | Action: {data['action']}
Context: DXY {data['dxy']}% US10Y {data['us10y']}% VIX {data['vix']}% RISK {data['risk']}
Balance: {data['balance']} USC | Session: {data['session']}
Can Trade: {reason}
"""
        ctk.CTkLabel(modal, text=info_text, font=("JetBrains Mono", 10), justify="left", anchor="nw").pack(padx=12, pady=8, fill="x")

        notes_var = ctk.StringVar(value="")
        ctk.CTkLabel(modal, text="Notes (setup, confluence):").pack(anchor="w", padx=12)
        notes_entry = ctk.CTkEntry(modal, textvariable=notes_var, width=460, height=60)
        notes_entry.pack(padx=12, pady=4)

        def confirm():
            data['notes'] = notes_var.get()
            success, msg = self.journal.log_trade(data)
            print(msg)
            self.update_bottom_bar()
            modal.destroy()
            toast = ctk.CTkToplevel(self)
            toast.geometry("300x80")
            toast.title("Journal")
            ctk.CTkLabel(toast, text=msg, font=("JetBrains Mono", 11), text_color="#00e5ff" if success else "#ef4444").pack(pady=20)
            toast.after(2000, toast.destroy)

        ctk.CTkButton(modal, text="CONFIRM LOG (Manual Trade)", fg_color="#00e5ff", text_color="black", command=confirm, state="normal" if can else "disabled").pack(pady=12)
        ctk.CTkLabel(modal, text=reason, text_color="#ffb800" if not can else "#22c55e", font=("JetBrains Mono", 9)).pack()

    def get_cols_for_width(self, width):
        try:
            if self.winfo_height() > width:
                return 1
        except Exception:
            pass
        if width < 900:
            return 1
        if width < 1300:
            return 2
        if width < 1800:
            return 3
        return 4

    def rebuild_grid(self, event=None):
        width = self.winfo_width()
        if width < 100:
            return
        cols = self.get_cols_for_width(width)
        if event is not None and cols == self.current_cols and width == self._last_width:
            return
        self.current_cols = cols
        self._last_width = width

        bar_w = max(200, width - 40)
        try:
            self.news_label.configure(wraplength=bar_w)
            for lbl in self.context_labels.values():
                lbl.configure(wraplength=bar_w)
        except Exception:
            pass

        for c in range(6):
            self.main.grid_columnconfigure(c, weight=0, minsize=0)
        for c in range(cols):
            self.main.grid_columnconfigure(c, weight=1, uniform="pairs")
        self.main.grid_columnconfigure(cols, weight=1)

        max_rows = (len(self.pair_cards) + cols - 1) // cols
        row_weight = 0 if cols <= 2 else 1
        for r in range(max(2, max_rows)):
            self.main.grid_rowconfigure(r, weight=row_weight)

        self.cards_frame.grid(row=0, column=0, columnspan=cols, sticky="nsew", padx=(0, 10))

        for c in range(4):
            self.cards_frame.grid_columnconfigure(c, weight=0, minsize=0)
        for c in range(cols):
            self.cards_frame.grid_columnconfigure(c, weight=1, uniform="pairs")
        for r in range(10):
            self.cards_frame.grid_rowconfigure(r, weight=0)
        for r in range(max_rows):
            self.cards_frame.grid_rowconfigure(r, weight=1, uniform="pairs")

        for i, card in enumerate(self.pair_cards):
            r, c = divmod(i, cols)
            card.grid(row=r, column=c, padx=6, pady=6, sticky="nsew")

        if cols <= 2:
            self.deep.grid(row=1, column=0, columnspan=cols, sticky="nsew", padx=10, pady=20)
        else:
            self.deep.grid(row=0, column=cols, rowspan=max_rows if max_rows > 0 else 1, sticky="nsew", padx=(10, 0))


if __name__ == "__main__":
    os.makedirs("journal", exist_ok=True)
    app = TomokoBrainApp()
    app.mainloop()