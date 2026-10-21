"""
MacroScenarioEngine - The Catalyst-to-Pair Causal Synthesis Engine for Tomoko.

Bridges the macroeconomic calendar and breaking news to specific Forex pairs and Gold (XAUUSD),
generating:
1. Pre-Event "If-Then" Scenario Trees (Hot / In-Line / Miss / Divergence).
2. Pair-specific transmission mechanics (DXY -> Yields -> Gold / Majors).
3. Post-Release Flash Verdicts (instant deviation analysis and execution guardrails).
"""

import re
import math
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# Currency to primary traded pairs
CURRENCY_PAIRS_MAP = {
    "USD": ["XAUUSD", "EURUSD", "USDJPY", "GBPUSD"],
    "EUR": ["EURUSD", "EURJPY", "EURGBP"],
    "GBP": ["GBPUSD", "GBPJPY", "EURGBP"],
    "JPY": ["USDJPY", "EURJPY", "GBPJPY"],
    "AUD": ["AUDUSD", "AUDJPY"],
    "CAD": ["USDCAD"],
    "CHF": ["USDCHF"],
    "NZD": ["NZDUSD"],
}

# Known high-impact catalyst categories
CATALYST_PATTERNS = [
    {
        "category": "INFLATION",
        "keywords": ["cpi", "pce", "ppi", "inflation", "consumer price index"],
        "importance": "HIGH",
        "transmission": {
            "USD": "Hot inflation pushes Fed rate cut expectations back -> US yields spike -> DXY rallies -> Gold (XAUUSD) & EURUSD face heavy selling pressure.",
            "EUR": "Hot inflation keeps ECB restrictive -> EUR strengthens vs USD/JPY.",
            "GBP": "Hot UK CPI increases stagflation/rate hike risk -> GBP initial pop, potential equity drag.",
        },
        "higher_is": "HAWKISH", # Higher than forecast = Hawkish for currency
    },
    {
        "category": "EMPLOYMENT",
        "keywords": ["non-farm", "nfp", "unemployment rate", "employment change", "jobless claims", "average hourly earnings", "adp"],
        "importance": "HIGH",
        "transmission": {
            "USD": "Strong jobs & wage growth support consumer demand and tighter monetary policy -> USD bids higher -> Gold sells off on rising real yields.",
            "GBP": "UK claimant count & wage data drive BOE policy expectations.",
        },
        "higher_is": "HAWKISH",
    },
    {
        "category": "CENTRAL_BANK",
        "keywords": ["interest rate", "rate decision", "fomc", "federal funds rate", "monetary policy", "ecb press conference", "boe official bank rate", "powell speaks", "lagarde speaks"],
        "importance": "CRITICAL",
        "transmission": {
            "USD": "Direct monetary policy stance. Hawkish guidance reprices treasury curve instantly; Dovish stance unleashes gold rallies and dollar liquidation.",
            "EUR": "ECB rate trajectory directly shifts EUR yield differentials.",
            "JPY": "BOJ rate shifts or yield curve control (YCC) comments produce explosive multi-hundred pip USDJPY moves.",
        },
        "higher_is": "HAWKISH",
    },
    {
        "category": "GROWTH_PMI",
        "keywords": ["gdp", "retail sales", "ism manufacturing", "ism services", "services pmi", "manufacturing pmi", "flash pmi"],
        "importance": "HIGH",
        "transmission": {
            "USD": "Resilient consumer/manufacturing data delays recession fears and rate cuts -> Pro-cyclical USD strength.",
            "EUR": "European PMI contractions heighten recession pricing -> Pressures EURUSD downwards.",
            "GBP": "UK services PMI is the primary bellwether for UK economic resilience.",
        },
        "higher_is": "HAWKISH",
    },
]


def parse_numeric_value(val_str: Any) -> Optional[float]:
    """Parse calendar strings like '3.2%', '215K', '1.4M', '-0.2%', '5.25' into floats."""
    if val_str is None:
        return None
    s = str(val_str).strip()
    if not s or s in ("-", "--", "N/A", "TBD"):
        return None
    
    # Remove symbols like '+' or '%'
    s_clean = s.replace("%", "").replace(",", "").strip()
    multiplier = 1.0
    
    if s_clean.endswith("K") or s_clean.endswith("k"):
        multiplier = 1000.0
        s_clean = s_clean[:-1]
    elif s_clean.endswith("M") or s_clean.endswith("m"):
        multiplier = 1000000.0
        s_clean = s_clean[:-1]
    elif s_clean.endswith("B") or s_clean.endswith("b"):
        multiplier = 1000000000.0
        s_clean = s_clean[:-1]
        
    try:
        return float(s_clean) * multiplier
    except (ValueError, TypeError):
        return None


class MacroScenarioEngine:
    """
    Synthesizes macroeconomic calendar events and breaking news into:
    - Pre-event 'If-Then' conditional scenarios
    - Pair-specific transmission mapping
    - Instant flash verdicts upon release
    """

    def __init__(self, news_engine=None, macro_engine=None, price_engine=None):
        self.news_engine = news_engine
        self.macro_engine = macro_engine
        self.price_engine = price_engine

    def classify_catalyst(self, event_name: str) -> Dict[str, Any]:
        """Classify event title into category and extract transmission characteristics."""
        name_lower = event_name.lower()
        for cat in CATALYST_PATTERNS:
            for kw in cat["keywords"]:
                if kw in name_lower:
                    return {
                        "category": cat["category"],
                        "matched_keyword": kw,
                        "importance": cat["importance"],
                        "higher_is": cat["higher_is"],
                        "transmission_notes": cat["transmission"],
                    }
        return {
            "category": "GENERAL_MACRO",
            "matched_keyword": "",
            "importance": "MEDIUM",
            "higher_is": "HAWKISH",
            "transmission_notes": {},
        }

    def generate_event_scenarios(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Takes a raw economic event dictionary (title, currency, forecast, previous, etc.)
        and generates a complete If-Then tactical scenario tree for relevant pairs.
        """
        title = event.get("event") or event.get("title") or "Unknown Event"
        currency = (event.get("currency") or event.get("country") or "USD").upper()
        forecast_raw = event.get("forecast", "")
        previous_raw = event.get("previous", "")
        actual_raw = event.get("actual", "")

        forecast_num = parse_numeric_value(forecast_raw)
        previous_num = parse_numeric_value(previous_raw)
        actual_num = parse_numeric_value(actual_raw)

        cat_info = self.classify_catalyst(title)
        category = cat_info["category"]
        higher_is = cat_info["higher_is"]
        affected_pairs = CURRENCY_PAIRS_MAP.get(currency, ["EURUSD", "USDJPY", "XAUUSD"])

        # Transmission context
        transmission_note = cat_info["transmission_notes"].get(
            currency,
            f"Direct macro impact on {currency} liquidity, interest rate expectations, and cross-currency yield differentials."
        )

        # Baseline baseline comparison value
        base_val = forecast_num if forecast_num is not None else previous_num

        # Compute reasonable scenario boundaries
        beat_threshold = None
        miss_threshold = None
        unit = "%" if "%" in str(forecast_raw) or "%" in str(previous_raw) else ""

        if base_val is not None:
            # Estimate deviation margin based on magnitude
            if abs(base_val) > 10000:  # e.g., 200,000 NFP
                margin = 25000.0
            elif abs(base_val) > 100:   # e.g., 105.2 ISM
                margin = 1.0
            elif abs(base_val) > 10:    # e.g., 18.5 claims
                margin = 0.5
            else:                       # e.g., 3.2% CPI
                margin = 0.2

            beat_threshold = round(base_val + margin, 2)
            miss_threshold = round(base_val - margin, 2)

        # Format scenario descriptions
        scenarios = self._build_scenario_branches(
            currency=currency,
            category=category,
            higher_is=higher_is,
            forecast_raw=forecast_raw,
            previous_raw=previous_raw,
            base_val=base_val,
            beat_threshold=beat_threshold,
            miss_threshold=miss_threshold,
            unit=unit,
            affected_pairs=affected_pairs
        )

        # Build Flash Verdict if actual is already known
        flash_verdict = None
        if actual_num is not None and base_val is not None:
            flash_verdict = self.evaluate_actual_release(
                event=event,
                actual_num=actual_num,
                forecast_num=forecast_num,
                previous_num=previous_num,
                category=category,
                higher_is=higher_is,
                currency=currency
            )

        return {
            "event_title": title,
            "currency": currency,
            "category": category,
            "time": event.get("time"),
            "timestamp": event.get("timestamp"),
            "impact": event.get("impact", "HIGH"),
            "forecast": forecast_raw,
            "previous": previous_raw,
            "actual": actual_raw,
            "transmission_logic": transmission_note,
            "affected_pairs": affected_pairs,
            "scenarios": scenarios,
            "flash_verdict": flash_verdict,
            "status": "COMPLETED" if actual_raw else "UPCOMING"
        }

    def _build_scenario_branches(
        self,
        currency: str,
        category: str,
        higher_is: str,
        forecast_raw: str,
        previous_raw: str,
        base_val: Optional[float],
        beat_threshold: Optional[float],
        miss_threshold: Optional[float],
        unit: str,
        affected_pairs: List[str]
    ) -> Dict[str, Any]:
        """Generate Hot/Beat, In-Line, and Miss conditional scenarios."""
        
        # When currency is USD:
        is_usd = (currency == "USD")
        
        # Scenario A: Hawkish / Beat
        beat_condition = f"> {beat_threshold}{unit}" if beat_threshold is not None else "Significantly above consensus"
        miss_condition = f"< {miss_threshold}{unit}" if miss_threshold is not None else "Significantly below consensus"
        inline_condition = f"Between {miss_threshold}{unit} and {beat_threshold}{unit}" if (beat_threshold and miss_threshold) else f"Near consensus ({forecast_raw or previous_raw})"

        if is_usd:
            beat_usd_bias = "STRONG_BULLISH"
            miss_usd_bias = "STRONG_BEARISH"
            gold_beat_action = "Bearish drop. Watch for break below recent support / value area low; yields likely rise."
            gold_miss_action = "Bullish expansion. Gold catches rapid safe-haven & rate-cut bids; watch for resistance breakout."
            eurusd_beat_action = "Downward impulse. Look for support retest or liquidity run below session low."
            eurusd_miss_action = "Upward continuation. Dollar selling gives EUR clearance to test overhead supply."
            usdjpy_beat_action = "Upward surge on widening US-Japan yield spreads; watch for 150+ resistance levels."
            usdjpy_miss_action = "Sharp drop as yields fall, strengthening JPY carry unwind."
        else:
            beat_usd_bias = "BULLISH_LOCAL"
            miss_usd_bias = "BEARISH_LOCAL"
            gold_beat_action = "Neutral to indirect. Focus on relative currency strength vs USD."
            gold_miss_action = "Neutral to indirect."
            eurusd_beat_action = f"Bullish for {currency}. EUR or cross gains traction."
            eurusd_miss_action = f"Bearish for {currency}. Potential breakdown."
            usdjpy_beat_action = "Indirect."
            usdjpy_miss_action = "Indirect."

        scenario_a = {
            "name": "HAWKISH_BEAT",
            "label": f"Hot / Beat ({beat_condition})",
            "trigger_condition": beat_condition,
            "macro_thesis": f"Stronger data strengthens {currency} rate yield advantage and delays monetary easing.",
            "currency_bias": f"BULLISH {currency}",
            "pair_playbook": {
                "XAUUSD": gold_beat_action,
                "EURUSD": eurusd_beat_action,
                "USDJPY": usdjpy_beat_action,
            },
            "beginner_warning": "Beware of chasing the immediate 1-minute candle spike. Spreads widen up to 5x upon release.",
            "veteran_note": "Watch for an initial fakeout/wick into prior liquidity pool before institutional expansion takes hold."
        }

        scenario_b = {
            "name": "DOVISH_MISS",
            "label": f"Cool / Miss ({miss_condition})",
            "trigger_condition": miss_condition,
            "macro_thesis": f"Weaker data fuels rate cut expectations, compressing {currency} yields and softening dollar demand.",
            "currency_bias": f"BEARISH {currency}",
            "pair_playbook": {
                "XAUUSD": gold_miss_action,
                "EURUSD": eurusd_miss_action,
                "USDJPY": usdjpy_miss_action,
            },
            "beginner_warning": "Wait at least 3 to 5 minutes for price discovery to settle before opening new market positions.",
            "veteran_note": "Look for fast displacement through key levels; if volume is heavy, trail stops behind 5-min order blocks."
        }

        scenario_c = {
            "name": "IN_LINE_OR_CHOP",
            "label": f"As Expected ({inline_condition})",
            "trigger_condition": inline_condition,
            "macro_thesis": "Prints match consensus. Market digests previous moves; high probability of whipsaw without structural trend change.",
            "currency_bias": "NEUTRAL / CHOP",
            "pair_playbook": {
                "XAUUSD": "Chop / Range-bound. Fade extremes of the pre-release 15-minute range.",
                "EURUSD": "Mean reversion. Likely sweeps session high/low and reverts to midpoint.",
                "USDJPY": "Following broader bond market drift rather than headline print.",
            },
            "beginner_warning": "Do not overtrade. When numbers print in-line, algorithms often hunt both sides of stops.",
            "veteran_note": "Prime condition for fading false breakout wicks back toward VWAP / 50% Consequent Encroachment."
        }

        return {
            "hot_beat": scenario_a,
            "cool_miss": scenario_b,
            "in_line": scenario_c,
        }

    def evaluate_actual_release(
        self,
        event: Dict[str, Any],
        actual_num: float,
        forecast_num: Optional[float],
        previous_num: Optional[float],
        category: str,
        higher_is: str,
        currency: str
    ) -> Dict[str, Any]:
        """
        Instant Post-Release Flash Verdict.
        Calculates deviation magnitude and prints an operational execution directive.
        """
        benchmark = forecast_num if forecast_num is not None else previous_num
        if benchmark is None:
            return {
                "verdict_type": "NEUTRAL",
                "headline": f"{event.get('event')} released at {actual_num}",
                "operational_bias": "NEUTRAL",
                "details": "No benchmark forecast available for deviation calculation."
            }

        delta = actual_num - benchmark
        pct_dev = (delta / abs(benchmark) * 100.0) if benchmark != 0 else 0.0

        # Classify magnitude
        abs_pct = abs(pct_dev)
        if abs_pct > 25.0 or (category == "INFLATION" and abs(delta) >= 0.3):
            magnitude = "HISTORIC_SHOCK"
        elif abs_pct > 10.0 or (category == "INFLATION" and abs(delta) >= 0.15):
            magnitude = "NOTABLE_DEVIATION"
        else:
            magnitude = "MILD_OR_IN_LINE"

        # Determine directional impact
        is_higher = delta > 0
        if is_higher:
            bias = "HAWKISH" if higher_is == "HAWKISH" else "DOVISH"
        elif delta < 0:
            bias = "DOVISH" if higher_is == "HAWKISH" else "HAWKISH"
        else:
            bias = "NEUTRAL"

        # Format flash headline
        event_name = event.get("event") or event.get("title")
        if bias == "HAWKISH":
            headline = f"{currency} HAWKISH SURGE: {event_name} beat consensus ({actual_num} vs exp {benchmark})"
            direction_summary = f"Bullish {currency} / Bearish Gold (XAUUSD) & EURUSD"
        elif bias == "DOVISH":
            headline = f"{currency} DOVISH DROP: {event_name} missed consensus ({actual_num} vs exp {benchmark})"
            direction_summary = f"Bearish {currency} / Bullish Gold (XAUUSD) & EURUSD"
        else:
            headline = f"{currency} IN-LINE PRINT: {event_name} matched expectations ({actual_num})"
            direction_summary = "Neutral / Mean Reversion Chop"

        return {
            "verdict_type": bias,
            "magnitude": magnitude,
            "headline": headline,
            "direction_summary": direction_summary,
            "actual_value": actual_num,
            "benchmark_value": benchmark,
            "delta": round(delta, 3),
            "percentage_deviation": round(pct_dev, 2),
            "action_advice": {
                "beginners": "Avoid entering during the first 5 minutes of high-impact releases. Wait for the 5-min candle to close.",
                "veterans": "Check if price swept pre-news Asian session highs/lows before entering institutional trend continuation."
            }
        }

    def get_upcoming_scenarios(self, hours_ahead: int = 48, min_priority: int = 2) -> List[Dict[str, Any]]:
        """
        Scans calendar events from NewsEngine within the next N hours and generates
        scenario trees for all qualifying high/medium impact events.
        """
        if not self.news_engine:
            return []

        events = getattr(self.news_engine, "events", [])
        if not events:
            return []

        now_utc = datetime.now(timezone.utc)
        now_ts = int(now_utc.timestamp())
        max_ts = now_ts + (hours_ahead * 3600)

        results = []
        for ev in events:
            ev_ts = ev.get("timestamp", 0)
            priority = ev.get("priority", 1)
            # Filter by timeframe and priority (medium/high)
            if (now_ts - 3600) <= ev_ts <= max_ts and priority >= min_priority:
                scenario_tree = self.generate_event_scenarios(ev)
                results.append(scenario_tree)

        return results

    def get_pair_catalyst_briefing(self, symbol: str) -> Dict[str, Any]:
        """
        Pair-Centric Synthesis:
        Takes a specific symbol (e.g. 'XAUUSD' or 'EURUSD') and returns all immediate
        macro catalysts, intermarket transmissions, and 'If-Then' scenarios governing that pair.
        """
        symbol_upper = symbol.upper()
        # Find which currencies drive this symbol
        drivers = []
        for ccy, pairs in CURRENCY_PAIRS_MAP.items():
            if symbol_upper in pairs or ccy in symbol_upper:
                drivers.append(ccy)
        if not drivers:
            drivers = ["USD"]

        upcoming = self.get_upcoming_scenarios(hours_ahead=48, min_priority=2)
        relevant_catalysts = [
            sc for sc in upcoming if sc.get("currency") in drivers
        ]

        # Extract macro intermarket posture if MacroEngine available
        intermarket_context = {}
        if self.macro_engine and hasattr(self.macro_engine, "get_macro_matrix"):
            try:
                matrix = self.macro_engine.get_macro_matrix()
                intermarket_context = {
                    "DXY": matrix.get("DXY", {}),
                    "US10Y": matrix.get("US10Y", {}),
                    "REAL_YIELD": matrix.get("FRED_REAL_YIELD_10Y", {}),
                    "VIX": matrix.get("VIX", {}),
                }
            except Exception as e:
                log.warning("Could not fetch macro matrix: %s", e)

        return {
            "symbol": symbol_upper,
            "drivers": drivers,
            "catalysts_count": len(relevant_catalysts),
            "catalysts": relevant_catalysts,
            "intermarket_context": intermarket_context,
            "generated_at": datetime.now(timezone.utc).isoformat()
        }
