# Tomoko Brain Scanner - Desktop Dashboard V1
# Dark terminal, manual-first
# Run: python ui/dashboard.py

import customtkinter as ctk
import sys, os
import json
from pathlib import Path
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
import config
import MetaTrader5 as mt5
TIMEFRAME_H4 = mt5.TIMEFRAME_H4
TIMEFRAME_H1 = mt5.TIMEFRAME_H1
TIMEFRAME_M15 = mt5.TIMEFRAME_M15
TIMEFRAME_D1 = mt5.TIMEFRAME_D1
from core.mt5_bridge import MT5Bridge
from core.brain_score import calculate_brain_score, get_manual_action
from core.regime_engine import RegimeEngine
from core.context_feed import ContextFeed
from core.levels_engine import LevelsEngine
from core.risk_engine import RiskEngine
from core.news_feed import NewsFeed
from core.journal import Journal
from strategies.trend_following_v2 import TrendFollowingV2
from strategies.liquidity_sweep import LiquiditySweep
from datetime import datetime

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

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

        self.grid_columnconfigure(0, weight=1)

        # Header (fixed width so price/score text changes never reflow the card)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.configure(width=340)
        header.pack_propagate(False)
        header.pack(fill="x", padx=12, pady=(10,4))
        ctk.CTkLabel(header, text=symbol, font=("JetBrains Mono", 14, "bold"), text_color="#00e5ff", width=110, anchor="w").pack(side="left")
        self.price_label = ctk.CTkLabel(header, text="0.00000", font=("JetBrains Mono", 15, "bold"), width=110, anchor="w")
        self.price_label.pack(side="left", padx=10)
        self.score_circle = ctk.CTkLabel(header, text=" 72 ", font=("JetBrains Mono", 14, "bold"), fg_color="#00e5ff", corner_radius=12, width=48, anchor="center")
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
        self.info_text = ctk.CTkLabel(self, text="", font=("JetBrains Mono", 13), justify="left", anchor="w", text_color="#a1a1aa", wraplength=340, height=80)
        self.info_text.pack(fill="x", padx=12, pady=4)

        # Action
        self.action_label = ctk.CTkLabel(self, text="WAIT", font=("Inter", 13, "bold"), fg_color="#1f2937", corner_radius=6, height=32)
        self.action_label.pack(fill="x", padx=12, pady=(4,8))

        self.reason_label = ctk.CTkLabel(self, text="Reason...", font=("Inter", 11), text_color="#71717a", wraplength=320, justify="left", anchor="w")
        self.reason_label.pack(fill="x", padx=12, pady=(0,10))

        # Buttons
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=(0,10))
        ctk.CTkButton(btn_row, text="Log Trade", width=80, height=24, font=("Inter", 10), fg_color="#1e293b", command=self.log_trade).pack(side="left", padx=(0,6))
        ctk.CTkButton(btn_row, text="Deep Dive", width=80, height=24, font=("Inter", 10), fg_color="#00e5ff", text_color="black", command=lambda: on_select(symbol)).pack(side="left")

        # Real data loop
        self.update_real()

    def _bias_color(self, bias):
        return "#22c55e" if bias == "Bullish" else "#ef4444" if bias == "Bearish" else "#ffb800"

    def update_real(self):
        try:
            df_h4 = self.mt5.get_rates(self.symbol, mt5.TIMEFRAME_H4, 200)
            df_h1 = self.mt5.get_rates(self.symbol, mt5.TIMEFRAME_H1, 200)
            df_m15 = self.mt5.get_rates(self.symbol, mt5.TIMEFRAME_M15, 200)
            df_daily = self.mt5.get_rates(self.symbol, mt5.TIMEFRAME_D1, 50)
            df_weekly = self.mt5.get_rates(self.symbol, mt5.TIMEFRAME_W1, 20)
            price_data = self.mt5.get_price(self.symbol)
            if df_h4.empty or df_h1.empty or df_m15.empty or df_daily.empty or not price_data:
                self.price_label.configure(text="NO DATA")
                self.after(2000, self.update_real)
                return
            regime = self.regime.evaluate_mtf_trend(df_h4, df_h1, df_m15)
            h4 = regime['H4']
            h1 = regime['H1']
            m15_struct = self.regime.evaluate_m15_structure(df_m15)
            levels = self.levels_engine.get_key_levels(df_daily, df_weekly, df_h1)
            self.mtf = regime
            # Brain score
            structural = 90 if h4['gate'] == "OPEN" else 40
            volatility = min(100, int(h4['adx'] * 2.5))
            levels_score = 70
            intermarket = 65
            news = 100
            score = calculate_brain_score(structural, volatility, levels_score, intermarket, news)
            # Action via strategies: liquidity sweep first, then trend following
            liq = LiquiditySweep().check_entry(h4, h1, levels, score)
            if liq:
                action, reason = liq
            else:
                action, reason = TrendFollowingV2().check_entry(h4, h1, score)
            # Update UI
            if "JPY" in self.symbol or "XAU" in self.symbol:
                price_text = f"{price_data['bid']:.3f}"
                fmt = ".3f"
            else:
                price_text = f"{price_data['bid']:.5f}"
                fmt = ".5f"
            self.price_label.configure(text=price_text.ljust(10))
            self.score_circle.configure(text=f" {score:>3} ")
            # color logic
            if score < 40:
                self.score_circle.configure(fg_color="#ef4444", text_color="white")
            elif score < 70:
                self.score_circle.configure(fg_color="#ffb800", text_color="black")
            else:
                self.score_circle.configure(fg_color="#00e5ff", text_color="black")
            # MTF badges
            self.badge_h4.configure(text=f"H4 {regime['H4_bias']}", fg_color=self._bias_color(regime['H4_bias']), text_color="black" if regime['H4_bias'] != "Neutral" else "white")
            self.badge_h1.configure(text=f"H1 {regime['H1_bias']}", fg_color=self._bias_color(regime['H1_bias']), text_color="black" if regime['H1_bias'] != "Neutral" else "white")
            self.badge_m15.configure(text=f"M15 {regime['M15_bias']}", fg_color=self._bias_color(regime['M15_bias']), text_color="black" if regime['M15_bias'] != "Neutral" else "white")
            # Info text
            ema21_h1 = df_h1.iloc[-1]['EMA21']
            ema21_m15 = df_m15.iloc[-1]['EMA21']
            info = (
                f"H4 {h4['gate']} {h4['direction']} | ADX {h4['adx']} ATRx{h4['atr_ratio']}\n"
                f"H1 Dist {h1['dist_ema21_atr']} ATR | M15 {mtf['M15_bias']}\n"
                f"Liq: Buys above {levels['liq_high']:.2f} Sells below {levels['liq_low']:.2f}"
            )
            self.info_text.configure(text=info)
            self.action_label.configure(text=action)
            if action.startswith("LIMIT AT LIQ"):
                self.action_label.configure(fg_color="#ffb800", text_color="black")
            else:
                self.action_label.configure(fg_color="#1f2937")
            self.reason_label.configure(text=reason[:120])
            # store for journal
            self.current_score = score
            self.current_action = action
            self.app_ref.pairs_data[self.symbol] = {"action": action, "reason": reason}
        except Exception as e:
            print(f"Error updating {self.symbol}: {e}")
            self.info_text.configure(text=f"Error: {e}")
        self.after(3000, self.update_real)

    def log_trade(self):
        # Gather real current state from self.mt5, self.regime, self.context_feed
        try:
            df_h4 = self.mt5.get_rates(self.symbol, mt5.TIMEFRAME_H4, 50)
            df_h1 = self.mt5.get_rates(self.symbol, mt5.TIMEFRAME_H1, 50)
            price_data = self.mt5.get_price(self.symbol)
            if df_h4.empty or not price_data:
                print("No data to log")
                return
            h4 = self.regime.evaluate_h4_gate(df_h4)
            h1 = self.regime.evaluate_h1_pullback(df_h1)
            context = self.app_ref.context_feed.get_context()
            risk = self.app_ref.context_feed.get_risk_sentiment(context)
            session_name, _ = self.app_ref.context_feed.get_session()
            balance = self.mt5.get_balance()

            # Determine direction from h4
            direction = h4.get('direction', 'UP')
            is_gold = "XAU" in self.symbol

            data = {
                "symbol": self.symbol,
                "direction": direction,
                "price": price_data['bid'],
                "h4_gate": h4.get('gate'),
                "h4_dir": direction,
                "adx": h4.get('adx'),
                "atr_ratio": h4.get('atr_ratio'),
                "dist_ema21": h1.get('dist_ema21_atr'),
                "rsi": h1.get('rsi'),
                "brain_score": self.current_score if hasattr(self, 'current_score') else 0,
                "action": self.current_action if hasattr(self, 'current_action') else "WAIT",
                "dxy": context.get('DXY', {}).get('change_pct'),
                "us10y": context.get('US10Y', {}).get('change_pct'),
                "vix": context.get('VIX', {}).get('change_pct'),
                "spx": context.get('SPX', {}).get('change_pct'),
                "gold": context.get('GOLD', {}).get('price'),
                "risk": risk,
                "session": session_name,
                "balance": balance,
                "notes": f"Manual log from brain - {self.symbol} H4 {h4.get('gate')} ADX {h4.get('adx')}"
            }

            # Open modal for confirmation
            self.app_ref.open_journal_modal(data)
        except Exception as e:
            print(f"Journal log error {e}")

class TomokoBrainApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("TOMOKO // BRAIN v1 - INFORMATION FIRST")
        self.geometry("1920x1080")

        # Real MT5 bridge + regime engine (reuse STEP 1 foundation)
        self.mt5 = MT5Bridge()
        self.mt5.connect()
        self.regime = RegimeEngine(config)
        self.context_feed = ContextFeed(self.mt5)
        self.levels_engine = LevelsEngine()
        self.journal = Journal(config.JOURNAL_PATH, config.JOURNAL_CSV)
        self.journal_count = self.journal.get_count()
        self.pairs_data = {}
        self.selected_symbol = None
        self.news_feed = NewsFeed()

        # Top Context Strip
        top = ctk.CTkFrame(self, fg_color="#0a0e14", height=50)
        top.pack(fill="x", padx=0, pady=0)

        left_top = ctk.CTkFrame(top, fg_color="transparent")
        left_top.pack(side="left", padx=16, pady=8)
        ctk.CTkLabel(left_top, text="TOMOKO // BRAIN v1", font=("JetBrains Mono", 16, "bold"), text_color="#00e5ff").pack(side="left")
        self.balance_label = ctk.CTkLabel(left_top, text="  -- USC • MICRO • 1 MAX POS • LOCAL MT5", font=("JetBrains Mono", 11), text_color="#71717a")
        self.balance_label.pack(side="left", padx=10)
        self.update_balance()
        self.after(5000, self.update_balance)

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
        self.news_label = ctk.CTkLabel(news_bar, text="SESSION: loading...", font=("JetBrains Mono", 11), text_color="#ffb800")
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

        self.pairs = config.PAIRS  # 7 pairs now
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

        # Single real-data label (wired to new LevelsEngine + RegimeEngine V2)
        self.deep_label = ctk.CTkLabel(self.deep, text="", font=("JetBrains Mono", 12), justify="left", anchor="w", wraplength=380, text_color="#e4e4e7")
        self.deep_label.pack(fill="x", padx=16, pady=10, anchor="w")

        # Bottom
        bottom = ctk.CTkFrame(self, fg_color="#0a0e14", height=24)
        bottom.pack(fill="x", side="bottom")
        ctk.CTkLabel(bottom, text="Bot = Hands & Feet. Brain = Information. No brain = blowout.  |  Manual trades: 0/100 until auto", font=("JetBrains Mono", 10), text_color="#52525b").pack(side="left", padx=16, pady=4)

        # Kick off real context strip updates
        self.update_context_strip()
        self.update_bottom_bar()
        self.update_news()

        # Responsive layout
        self.minsize(800, 600)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.bind("<Configure>", self.rebuild_grid)
        self.after(200, self.rebuild_grid)

    def open_journal_modal(self, data):
        can, reason = self.journal.can_trade()
        modal = ctk.CTkToplevel(self)
        modal.title(f"Log Trade - {data['symbol']}")
        modal.geometry("500x600")
        modal.grab_set()

        ctk.CTkLabel(modal, text=f"Log {data['symbol']} {data['direction']} @ {data['price']}", font=("JetBrains Mono", 14, "bold"), text_color="#00e5ff").pack(pady=10)

        # Show key info
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

        # Notes entry
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
            # Show toast
            toast = ctk.CTkToplevel(self)
            toast.geometry("300x80")
            toast.title("Journal")
            ctk.CTkLabel(toast, text=msg, font=("JetBrains Mono", 11), text_color="#00e5ff" if success else "#ef4444").pack(pady=20)
            toast.after(2000, toast.destroy)

        ctk.CTkButton(modal, text="CONFIRM LOG (Manual Trade)", fg_color="#00e5ff", text_color="black", command=confirm, state="normal" if can else "disabled").pack(pady=12)
        ctk.CTkLabel(modal, text=reason, text_color="#ffb800" if not can else "#22c55e", font=("JetBrains Mono", 9)).pack()

    def update_bottom_bar(self):
        count = self.journal.get_count()
        # Bottom bar text: "Manual trades: X/100 until auto"
        for widget in self.winfo_children():
            if isinstance(widget, ctk.CTkFrame) and widget.cget("fg_color") == "#0a0e14":
                for child in widget.winfo_children():
                    if isinstance(child, ctk.CTkLabel) and "Manual trades" in child.cget("text"):
                        child.configure(text=f"Bot = Hands & Feet. Brain = Information. No brain = blowout. | Manual trades: {count}/100 until auto | Last: {datetime.now().strftime('%H:%M:%S')}")
        # Also update top balance label if needed

    def update_balance(self):
        try:
            bal = self.mt5.get_balance()
            self.balance_label.configure(text=f"  {bal:.2f} USC • MICRO • 1 MAX POS • LOCAL MT5")
        except Exception as e:
            print("balance update failed:", e)
        self.after(5000, self.update_balance)

    def update_context_strip(self):
        try:
            context = self.context_feed.get_context()
            risk = self.context_feed.get_risk_sentiment(context)
            session_name, active = self.context_feed.get_session()
            for key, data in context.items():
                price = data.get('price', 0)
                chg = data.get('change_pct', 0)
                arrow = "↑" if data.get('trend') == "UP" else "↓" if data.get('trend') == "DOWN" else "→"
                color = "#22c55e" if chg > 0 else "#ef4444" if chg < 0 else "#e4e4e7"
                if key == "DXY":
                    text = f"DXY {price:.2f} {chg:+.2f}% {arrow}"
                elif key == "GOLD":
                    text = f"GOLD {price:.2f} {chg:+.2f}% {arrow}"
                else:
                    text = f"{key} {price:.2f} {chg:+.2f}% {arrow}"
                self.context_labels[key].configure(text=text, text_color=color)
        except Exception as e:
            print("context strip update failed:", e)
        self.after(10000, self.update_context_strip)

    def update_news(self):
        try:
            events = self.news_feed.get_upcoming(hours=12)
            session_name, _ = self.context_feed.get_session()
            risk = self.context_feed.get_risk_sentiment(self.context_feed.get_context())
            parts = [
                f"{e['time']} {e['currency']} {e['event']} [{e['impact']}] F:{e['forecast']} P:{e['previous']}"
                for e in events[:3]
            ]
            text = " | ".join(parts) if parts else "No high-impact events in next 12h"
            block = self.news_feed.get_next_high_impact()
            if block:
                self.news_label.configure(text_color="#ef4444")
                text = f"AVOID {block['currency']} | SESSION {session_name} • Risk {risk} | " + text
            else:
                self.news_label.configure(text_color="#ffb800")
                text = f"SESSION {session_name} • Risk {risk} | " + text
            self.news_label.configure(text=text)
        except Exception as e:
            print("news update failed:", e)
        self.after(60000, self.update_news)

    def get_cols_for_width(self, width):
        # Portrait orientation (taller than wide) -> single vertical column list
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

        # Responsive wraplength for top bars
        bar_w = max(200, width - 40)
        try:
            self.news_label.configure(wraplength=bar_w)
            for lbl in self.context_labels.values():
                lbl.configure(wraplength=bar_w)
        except Exception:
            pass

        # Main grid columns
        for c in range(6):
            self.main.grid_columnconfigure(c, weight=0, minsize=0)
        for c in range(cols):
            self.main.grid_columnconfigure(c, weight=1, uniform="pairs")
        self.main.grid_columnconfigure(cols, weight=1)

        max_rows = (len(self.pair_cards) + cols - 1) // cols
        # Horizontal: stretch rows to fill height (no scroll needed).
        # Vertical (cols<=2): keep natural row heights so content overflows
        # and the outer CTkScrollableFrame scrolls instead of compressing cards.
        row_weight = 0 if cols <= 2 else 1
        for r in range(max(2, max_rows)):
            self.main.grid_rowconfigure(r, weight=row_weight)

        # Cards container spans the card columns
        self.cards_frame.grid(row=0, column=0, columnspan=cols, sticky="nsew", padx=(0, 10))

        # Cards container internal grid
        for c in range(4):
            self.cards_frame.grid_columnconfigure(c, weight=0, minsize=0)
        for c in range(cols):
            self.cards_frame.grid_columnconfigure(c, weight=1, uniform="pairs")
        for r in range(10):
            self.cards_frame.grid_rowconfigure(r, weight=0)
        for r in range(max_rows):
            self.cards_frame.grid_rowconfigure(r, weight=1, uniform="pairs")

        # Re-grid cards
        for i, card in enumerate(self.pair_cards):
            r, c = divmod(i, cols)
            card.grid(row=r, column=c, padx=6, pady=6, sticky="nsew")

        # Deep dive placement
        if cols <= 2:
            self.deep.grid(row=1, column=0, columnspan=cols, sticky="nsew", padx=10, pady=20)
        else:
            self.deep.grid(row=0, column=cols, rowspan=max_rows if max_rows > 0 else 1, sticky="nsew", padx=(10, 0))

    def on_select_pair(self, symbol):
        try:
            from core.regime_engine import RegimeEngine
            from core.levels_engine import LevelsEngine
            from core.mt5_bridge import MT5Bridge
            import MetaTrader5 as mt5
            mt5b = MT5Bridge()
            mt5b.connect()
            df_h4 = mt5b.get_rates(symbol, mt5.TIMEFRAME_H4, 200)
            df_h1 = mt5b.get_rates(symbol, mt5.TIMEFRAME_H1, 200)
            df_m15 = mt5b.get_rates(symbol, mt5.TIMEFRAME_M15, 100)
            df_d1 = mt5b.get_rates(symbol, mt5.TIMEFRAME_D1, 50)
            df_w1 = mt5b.get_rates(symbol, mt5.TIMEFRAME_W1, 20)
            if len(df_h4) < 20 or len(df_h1) < 20:
                self.deep_label.configure(text=f"{symbol} loading...")
                return
            regime = RegimeEngine(config)
            mtf = regime.evaluate_mtf_trend(df_h4, df_h1, df_m15)
            h4 = mtf['H4']; h1 = mtf['H1']
            levels = LevelsEngine().get_key_levels(df_d1, df_w1, df_h1)
            price = df_h1['close'].iloc[-1]
            text = f"{symbol} Deep Dive REAL\n\n[Key Levels Map - REAL]\nDaily High {levels['daily_high']:.2f}\nLiq High Buy Stops {levels['liq_high']:.2f} (Daily +0.3 ATR)\nCurrent >>> {price:.2f}\nLiq Low Sell Stops {levels['liq_low']:.2f}\nDaily Low {levels['daily_low']:.2f}\nATR H1 {levels['atr']:.2f}\n\n[MTF Trend - REAL]\nH4 {mtf['H4_bias']} Gate {h4['gate']} ADX {h4['adx']} ATRx{h4['atr_ratio']} Dist {h4.get('dist_ema21_atr',0)} ATR\nH1 {mtf['H1_bias']} Dist {h1['dist_ema21_atr']} ATR Pullback? {h1['is_pullback']} RSI {h1['rsi']}\nM15 {mtf['M15_bias']} Aligned? {mtf['aligned']}\n\n[TrendFollowing V2 Gate - FROM strategies/trend_following_v2.py]\nH4 EMA20 {h4['ema20']:.2f} EMA50 {h4['ema50']:.2f} {'bullish' if h4['direction']=='UP' else 'bearish'}\nADX>22? {h4['adx']>22} ATRx>0.8? {h4['atr_ratio']>0.8}\nH1 pullback <=0.5 ATR? {h1['is_pullback']} (actual {h1['dist_ema21_atr']})\n\n[Liquidity Sweep Rule - FROM strategies/liquidity_sweep.py]\nTrigger: H4 OPEN {h4['direction']} + ADX>40 + Dist>1.2 ATR\nCurrent ADX {h4['adx']} Dist {h1['dist_ema21_atr']}\nIf UP: LIMIT AT LIQ HIGH {levels['liq_high']:.2f}\nIf DOWN: LIMIT AT LIQ LOW {levels['liq_low']:.2f}\nYour example 4647.41 case: ADX 43.4 Dist 1.27 => LIMIT\n\n[Action]\n{self.pairs_data.get(symbol, {}).get('action','WAIT') if hasattr(self,'pairs_data') else 'Check card'}"
            self.deep_label.configure(text=text)
        except Exception as e:
            import traceback; traceback.print_exc()
            self.deep_label.configure(text=f"{symbol} Deep Dive error: {e}")

if __name__ == "__main__":
    app = TomokoBrainApp()
    app.mainloop()