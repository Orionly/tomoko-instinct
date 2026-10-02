# Tomoko Brain - Original Brain - Config
PROJECT_NAME = "Tomoko"
VERSION = "V1-Brain-Scanner-Desktop"

# Account
ACCOUNT_MICRO = True
BALANCE_USC = 567.11  # 567.11 USC = $5.67
BALANCE_THRESHOLD = 1000

# Trading Universe - VT Markets Standard (no c suffix)
PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "EURJPY", "EURGBP", "XAUUSD"]  # 7 pairs

# RiskManager micro-hardened
RISK_CONFIG = {
    "XAU": {"sl_atr_mult": 1.8, "tp_atr_mult": 3.6, "sl_min_usd": 3.0, "lot": 0.01},
    "FX": {"sl_atr_mult": 1.2, "tp_atr_mult": 2.4, "lot": 0.01},
}

# TrendFollowing V2 rules (for info display, not auto)
TREND_CONFIG = {
    "adx_min": 22,
    "atr_ratio_min": 0.8,
    "pullback_max_atr": 0.5,
}

# Context Strip symbols for yfinance fallback
CONTEXT_SYMBOLS = {
    "DXY": "DX-Y.NYB",
    "US10Y": "^TNX",
    "VIX": "^VIX",
    "SPX": "^GSPC",
    "OIL": "CL=F",
    "GOLD": "GC=F",
    "NIKKEI": "^N225"
}

# Intermarket Drivers
INTERMARKET_MAP = {
    "EURUSD": ["DXY", "US10Y", "EURUSD"],
    "GBPUSD": ["DXY", "US10Y", "GBPUSD"],
    "USDJPY": ["DXY", "US10Y", "NIKKEI", "US10Y-JP10Y"],
    "GBPJPY": ["GBPUSD", "USDJPY", "NIKKEI", "DXY"],
    "EURJPY": ["EURUSD", "USDJPY", "NIKKEI"],
    "EURGBP": ["EURUSD", "GBPUSD", "UK10Y-EU10Y"],
    "XAUUSD": ["DXY", "US10Y", "VIX", "GOLD", "SPX"]  # Gold drivers: DXY inverse, real yields, risk-off, VIX
}

# Gold (XAUUSD) specific ATR/risk handling
XAU_CONFIG = {"sl_atr_mult": 1.8, "tp_atr_mult": 3.6, "sl_min_usd": 3.0, "lot": 0.01}

# Journal - Manual Trade Logger (Brain is information only, no auto-trade)
JOURNAL_PATH = "journal/trades.json"
JOURNAL_CSV = "journal/trades.csv"
MAX_DAILY_TRADES = 3
MAX_OPEN_POSITIONS = 1  # balance < 1000

# News Feed config
NEWS_CONFIG = {"show_actual": True, "block_minutes_before": 60, "high_impact_only": False}

# Liquidity Limit Rule (H4 OPEN + ADX>40 + Dist>1.2 ATR)
LIQ_RULE_CONFIG = {"adx_threshold": 40, "dist_threshold_atr": 1.2, "liq_offset_atr": 0.3, "enabled": True}