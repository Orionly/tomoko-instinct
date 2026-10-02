# Tomoko Brain - Watchout Zones
# Watch areas derived from daily range / equilibrium / EMA21 + liquidity, NOT entry signals.
# XAU uses wider zones (sl_atr_mult 1.8 style) since gold moves in multi-USD ranges.
import math


def _safe_num(v, default=0.0):
    """Convert to float, replacing NaN/Inf with default."""
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def calculate_watchout_zones(df_h4, df_h1, current_price, daily_low, daily_high, weekly_open,
                             liq_high, liq_low, ema21, atr, symbol="", h4_dir="UP"):
    # guard against degenerate ranges so pct math never divides by zero
    daily_low = _safe_num(daily_low)
    daily_high = _safe_num(daily_high)
    current_price = _safe_num(current_price)
    ema21 = _safe_num(ema21)
    liq_high = _safe_num(liq_high) if liq_high else 0.0
    liq_low = _safe_num(liq_low) if liq_low else 0.0
    daily_range = daily_high - daily_low
    if daily_range <= 0 or math.isnan(daily_range):
        daily_range = 1.0
    safe_atr = atr if atr and not (math.isnan(atr) or math.isinf(atr)) and atr > 0 else 1e-9

    eq = (daily_high + daily_low) / 2
    premium_pct = (current_price - daily_low) / daily_range * 100

    # Option A confluence
    buy_low = daily_low
    buy_high = min(eq, ema21 + 0.2 * safe_atr)
    sell_low = daily_high
    sell_high = liq_high if liq_high else daily_high + 0.5 * safe_atr

    # For XAU: use XAU_CONFIG sl_atr_mult 1.8 -> wider zones 3-5 USD
    is_xau = "XAU" in symbol or "GOLD" in symbol
    if is_xau:
        buy_low = ema21 - 1.0 * safe_atr
        buy_high = ema21 + 0.3 * safe_atr
        sell_low = daily_high
        sell_high = daily_high + 1.0 * safe_atr

    return {
        "buy_zone": {
            "low": buy_low,
            "high": buy_high,
            "type": "DISCOUNT" if buy_high < eq else "EQUILIBRIUM",
            "discount_pct": (buy_high - daily_low) / daily_range * 100,
            "distance_atr": (current_price - buy_high) / safe_atr,
            "confluence": [
                "H4 Bullish" if h4_dir == "UP" else "H4 Bearish",
                "Discount" if buy_high < eq else "Premium",
                "Daily Low + EMA21",
                f"Liq Low {liq_low}",
            ],
        },
        "sell_zone": {
            "low": sell_low,
            "high": sell_high,
            "type": "PREMIUM" if sell_low > eq else "EQUILIBRIUM",
            "premium_pct": (sell_high - daily_low) / daily_range * 100,
            "distance_atr": (sell_low - current_price) / safe_atr,
            "confluence": [
                "Premium" if sell_low > eq else "Discount",
                "Daily High + Liq High",
                "H4 extended",
            ],
        },
        "premium_discount": {
            "daily_low": daily_low,
            "daily_high": daily_high,
            "eq": eq,
            "premium_zone": [eq, daily_high],
            "discount_zone": [daily_low, eq],
            "current_pct": premium_pct,
        },
    }
