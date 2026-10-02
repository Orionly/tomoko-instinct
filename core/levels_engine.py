# LevelsEngine - Key liquidity / reference level detection
class LevelsEngine:
    def get_key_levels(self, df_daily, df_weekly, df_h1):
        daily_high = df_daily['high'].iloc[-1]
        daily_low = df_daily['low'].iloc[-1]
        weekly_open = df_weekly['open'].iloc[-1] if len(df_weekly) > 0 else df_daily['open'].iloc[-1]
        atr = df_h1['ATR'].iloc[-1] if len(df_h1) > 0 else 10
        liq_high = daily_high + 0.3 * atr
        liq_low = daily_low - 0.3 * atr
        close = df_h1['close'].iloc[-1]
        return {
            "daily_high": daily_high,
            "daily_low": daily_low,
            "weekly_open": weekly_open,
            "liq_high": liq_high,
            "liq_low": liq_low,
            "atr": atr,
            "close": close,
        }
