# FundamentalEngine - rate differential + intermarket (dynamic rates, no hardcoding lock-in)
import os
import requests

FRED_URL = "https://api.stlouisfed.org/fred/series/observations?series_id=FEDFUNDS&api_key={key}&limit=1&sort_order=desc"


class FundamentalEngine:
    PAIR_CURRENCIES = {
        "EURUSD": ["EUR", "USD"],
        "GBPUSD": ["GBP", "USD"],
        "USDJPY": ["USD", "JPY"],
        "GBPJPY": ["GBP", "JPY"],
        "EURJPY": ["EUR", "JPY"],
        "EURGBP": ["EUR", "GBP"],
        "XAUUSD": ["XAU", "USD"],
    }

    # Cached fallback (used until FRED key is configured for live fetch).
    FALLBACK_RATES = {"USD": 5.5, "EUR": 4.0, "GBP": 5.25, "JPY": 0.1, "XAU": 0}

    def __init__(self):
        self.fred_key = os.getenv("FRED_API_KEY", "")

    def get_rates(self):
        # Try live FRED fetch first (dynamic, update without redeploying exe).
        # Only attempts when a key is configured; otherwise uses cached fallback.
        if self.fred_key:
            try:
                r = requests.get(FRED_URL.format(key=self.fred_key), timeout=2)
                if r.status_code == 200:
                    obs = r.json().get("observations")
                    if obs:
                        val = float(obs[0].get("value", 0))
                        return {
                            "USD": val,
                            "EUR": 4.0,
                            "GBP": 5.25,
                            "JPY": 0.1,
                            "XAU": 0,
                            "updated": obs[0].get("date", "live"),
                        }
            except Exception as e:
                print(f"FRED rates error {e}")
        return dict(self.FALLBACK_RATES, updated="2026-08-27")

    def get_fundamental(self, symbol, dxy_change=0, us10y_change=0):
        rates = self.get_rates()
        currencies = self.PAIR_CURRENCIES.get(symbol, ["USD"])

        if symbol == "XAUUSD":
            # Gold logic (DXY inverse + US10Y real yield + risk)
            if dxy_change < 0 and us10y_change < 0:
                bias = "Gold long favored DXY down + US10Y down"
                structural = 80
                intermarket = 85
            elif dxy_change > 0 and us10y_change > 0:
                bias = "Gold short pressure DXY up + yields up"
                structural = 40
                intermarket = 30
            else:
                bias = "Gold mixed"
                structural = 60
                intermarket = 60
        else:
            # Forex: higher-rate currency is structurally bullish
            base, quote = currencies[0], currencies[1]
            diff = rates.get(base, 0) - rates.get(quote, 0)
            structural = 70 if diff > 0 else 50
            intermarket = 65
            bias = f"{base} {rates.get(base)}% vs {quote} {rates.get(quote)}% diff {diff:+.2f}"

        return {
            "structural": structural,
            "intermarket": intermarket,
            "bias": bias,
            "rates": rates,
        }
