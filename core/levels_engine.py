# LevelsEngine - Key liquidity / reference level detection (PDH / PDL reference)
class LevelsEngine:
    def get_key_levels(self, df_daily, df_weekly, df_h1):
        # Use previous completed day candle (PDH/PDL) for fixed daily liquidity sweep reference
        if df_daily is not None and len(df_daily) >= 2:
            daily_high = float(df_daily['high'].iloc[-2])
            daily_low = float(df_daily['low'].iloc[-2])
            prev_open = float(df_daily['open'].iloc[-2])
        elif df_daily is not None and len(df_daily) == 1:
            daily_high = float(df_daily['high'].iloc[-1])
            daily_low = float(df_daily['low'].iloc[-1])
            prev_open = float(df_daily['open'].iloc[-1])
        else:
            daily_high, daily_low, prev_open = 0.0, 0.0, 0.0

        if df_weekly is not None and len(df_weekly) > 0:
            weekly_open = float(df_weekly['open'].iloc[-1])
        else:
            weekly_open = prev_open

        if df_h1 is not None and len(df_h1) > 0:
            atr = float(df_h1['ATR'].iloc[-1])
            close = float(df_h1['close'].iloc[-1])
        else:
            atr = 10.0
            close = daily_high

        liq_high = daily_high + 0.3 * atr
        liq_low = daily_low - 0.3 * atr

        return {
            "daily_high": daily_high,
            "daily_low": daily_low,
            "weekly_open": weekly_open,
            "liq_high": liq_high,
            "liq_low": liq_low,
            "atr": atr,
            "close": close,
            "today_high": float(df_daily['high'].iloc[-1]) if df_daily is not None and len(df_daily) > 0 else daily_high,
            "today_low": float(df_daily['low'].iloc[-1]) if df_daily is not None and len(df_daily) > 0 else daily_low,
        }
