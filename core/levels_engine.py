import pandas as pd

# LevelsEngine - Key liquidity / reference level detection (PDH / PDL reference)
class LevelsEngine:
    def get_key_levels(self, df_daily, df_weekly, df_h1):
        """
        Determines PDH, PDL, Today High, Today Low, Weekly Open, and Liquidity Boundaries.
        Accurately skips weekend (Saturday/Sunday) partial bars so Monday always anchors to Friday.
        """
        daily_high, daily_low, prev_open = 0.0, 0.0, 0.0
        today_high, today_low = 0.0, 0.0

        if df_daily is not None and len(df_daily) > 0:
            today_high = float(df_daily['high'].iloc[-1])
            today_low = float(df_daily['low'].iloc[-1])

            # Find the last completed full trading day before today (skipping weekend bars)
            prev_row = None
            if len(df_daily) >= 2:
                for i in range(len(df_daily) - 2, -1, -1):
                    row = df_daily.iloc[i]
                    idx = df_daily.index[i]
                    # Check day of week (Monday=0 ... Sunday=6)
                    day_idx = getattr(idx, 'dayofweek', None)
                    if day_idx is None and 'time' in row:
                        try:
                            day_idx = pd.to_datetime(row['time']).dayofweek
                        except Exception:
                            day_idx = None

                    # If this row is a Saturday (5) or Sunday (6) sliver, skip backwards to Friday
                    if day_idx in (5, 6):
                        continue

                    prev_row = row
                    break

            if prev_row is None and len(df_daily) >= 2:
                prev_row = df_daily.iloc[-2]

            if prev_row is not None:
                daily_high = float(prev_row['high'])
                daily_low = float(prev_row['low'])
                prev_open = float(prev_row['open'])
            else:
                daily_high = today_high
                daily_low = today_low
                prev_open = float(df_daily['open'].iloc[-1])

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
            "today_high": today_high,
            "today_low": today_low,
        }
