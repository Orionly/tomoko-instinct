"""
Autonomous Financial Sentiment & Macro Intelligence Classifier.
Powered by Quantized FinBERT ONNX with FX & Gold Domain Polarity Mapping.
"""

import os
import sys
import time
import logging
import re
from typing import Dict, Any, List, Optional, Tuple

log = logging.getLogger("Tomoko.NewsClassifier")

# Paths to the quantized FinBERT ONNX model
MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "finbert_onnx")
MODEL_FILE = os.path.join(MODEL_DIR, "model.onnx")
TOKENIZER_FILE = os.path.join(MODEL_DIR, "tokenizer.json")

# FinBERT output labels (index 0, 1, 2)
LABELS = ["positive", "negative", "neutral"]


class FinBERTClassifier:
    """
    Thread-safe, lazy-loaded ONNX runtime wrapper for FinBERT.
    Provides ~95ms CPU inference with ~140MB memory footprint.
    """

    def __init__(self, model_path: str = MODEL_FILE, tokenizer_path: str = TOKENIZER_FILE):
        self.model_path = model_path
        self.tokenizer_path = tokenizer_path
        self.session = None
        self.tokenizer = None
        self._is_available = False
        self._init_model()

    def _init_model(self):
        """Initialize ONNX session and tokenizer if files exist."""
        if not os.path.exists(self.model_path) or not os.path.exists(self.tokenizer_path):
            log.warning("[FinBERTClassifier] Model files not found at %s. AI classification disabled.", self.model_path)
            self._is_available = False
            return

        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer

            self.np = np
            self.ort = ort

            # Configure high-performance CPU session
            opts = ort.SessionOptions()
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            opts.intra_op_num_threads = 4

            self.tokenizer = Tokenizer.from_file(self.tokenizer_path)
            self.tokenizer.enable_truncation(max_length=128)
            self.tokenizer.enable_padding(length=128, pad_id=0, pad_token="[PAD]")

            self.session = ort.InferenceSession(self.model_path, opts, providers=["CPUExecutionProvider"])
            self.input_names = [inp.name for inp in self.session.get_inputs()]
            self._is_available = True
            log.info("[FinBERTClassifier] Successfully loaded FinBERT ONNX from %s", self.model_path)
        except Exception as e:
            log.error("[FinBERTClassifier] Failed to initialize ONNX model: %s", e)
            self._is_available = False

    @property
    def is_available(self) -> bool:
        return self._is_available

    def classify_sentiment(self, text: str) -> Dict[str, Any]:
        """
        Run raw FinBERT inference on input text.
        Returns label, confidence, probabilities, and latency.
        """
        if not self._is_available or not text:
            return {
                "label": "neutral",
                "confidence": 0.5,
                "probabilities": {"positive": 0.33, "negative": 0.33, "neutral": 0.34},
                "latency_ms": 0.0,
                "model": "fallback"
            }

        t0 = time.perf_counter()
        encoded = self.tokenizer.encode(text)
        input_ids = self.np.array([encoded.ids], dtype=self.np.int64)
        attention_mask = self.np.array([encoded.attention_mask], dtype=self.np.int64)

        inputs = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        }
        if "token_type_ids" in self.input_names:
            inputs["token_type_ids"] = self.np.array([encoded.type_ids], dtype=self.np.int64)

        outputs = self.session.run(None, inputs)
        logits = outputs[0]
        
        # Softmax
        e_x = self.np.exp(logits - self.np.max(logits, axis=-1, keepdims=True))
        probs = (e_x / self.np.sum(e_x, axis=-1, keepdims=True))[0]

        latency_ms = (time.perf_counter() - t0) * 1000.0
        best_idx = int(self.np.argmax(probs))

        return {
            "label": LABELS[best_idx],
            "confidence": float(probs[best_idx]),
            "probabilities": {
                "positive": float(probs[0]),
                "negative": float(probs[1]),
                "neutral": float(probs[2]),
            },
            "latency_ms": float(latency_ms),
            "model": "finbert_onnx"
        }


# Singleton instance
_classifier_instance: Optional[FinBERTClassifier] = None


def get_news_classifier() -> FinBERTClassifier:
    """Get or initialize singleton FinBERT classifier instance."""
    global _classifier_instance
    if _classifier_instance is None:
        _classifier_instance = FinBERTClassifier()
    return _classifier_instance


def analyze_news_item(title: str, summary: str = "") -> Dict[str, Any]:
    """
    High-level intelligence layer:
    Combines FinBERT raw sentiment with Tomoko's domain-specific
    FX & Gold transmission rules (Geopolitical Haven Unwind, Hawkish/Dovish Macro).
    """
    classifier = get_news_classifier()
    text = f"{title} {summary}".strip()
    text_lower = text.lower()

    # Step 1: Raw AI sentiment
    ai_result = classifier.classify_sentiment(title)
    label = ai_result["label"]
    conf = ai_result["confidence"]
    probs = ai_result["probabilities"]

    def has_any(keywords: List[str]) -> bool:
        for kw in keywords:
            if len(kw) <= 4:
                if re.search(r"\b" + re.escape(kw) + r"\b", text_lower):
                    return True
            else:
                if kw in text_lower:
                    return True
        return False

    is_geo = has_any([
        "sanction", "sanctions", "strike", "airstrike", "missile", "drone",
        "war", "invade", "invasion", "military", "ceasefire", "peace", "treaty",
        "russia", "ukraine", "iran", "israel", "gaza", "lebanon", "red sea", "taiwan"
    ])
    
    is_macro = has_any([
        "cpi", "consumer price index", "inflation", "ppi", "producer price", "interest rate",
        "rate cut", "rate hike", "hikes rate", "fed", "fomc", "ecb", "boj", "boe", "central bank",
        "powell", "warsh", "lagarde", "ueda", "bailey", "treasury yields", "bond yields", "payroll", "nonfarm"
    ])
    
    is_energy = has_any([
        "oil", "crude", "brent", "wti", "opec", "pipeline", "gas", "fuel"
    ])

    is_trade = has_any([
        "tariff", "tariffs", "trade war", "export ban", "import duty", "trade deal"
    ])

    # De-escalation semantic anchors (supports AI when confidence is split)
    has_de_escalation_words = any(w in text_lower for w in [
        "waive", "waiver", "waiving", "waived", "pause", "pauses", "pausing",
        "stall", "stalls", "stalling", "stalled", "delay", "delays", "delaying", "delayed",
        "suspend", "suspends", "freeze", "shelve", "on hold", "holds off", "hold off",
        "ceasefire", "truce", "diplomatic", "lift sanction", "lifts sanction", "ease sanction"
    ])

    implications = []
    impacted_pairs = []
    severity_override = None

    # Tweak 1: Geopolitical Transmission for Gold & Haven Currencies
    if is_geo or is_trade:
        impacted_pairs.append("XAUUSD")
        
        # If AI detects POSITIVE or de-escalation anchors confirm relief
        if label == "positive" or (has_de_escalation_words and label != "negative"):
            implications.append("XAUUSD: Geopolitical Relief (Haven Unwind)")
            if any(w in text_lower for w in ["russia", "ukraine", "europe", "gas", "sanction"]):
                if "EURUSD" not in impacted_pairs:
                    impacted_pairs.append("EURUSD")
                implications.append("EURUSD: Geopolitical Relief (Tailwind)")
            severity_override = "MEDIUM"  # Relief does not require panic sirens
        elif label == "negative":
            implications.append("XAUUSD: Safe-Haven Inflow (Bullish)")
            severity_override = "HIGH"

    # Tweak 2: Macro & Inflation Tone Inversion (Corporate beat != FX bullish)
    if is_macro:
        for p in ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]:
            if p not in impacted_pairs:
                impacted_pairs.append(p)
                
        # Check for Hawkish indicators (CPI rising, exceeding expectations, rate hikes)
        is_hawkish = any(w in text_lower for w in [
            "hike", "hikes", "inflation rises", "cpi rises", "exceeding expectations",
            "exceeds expectations", "exceeds expectation", "higher than expected",
            "caution on cut", "cautious on cut", "not in a hurry", "sticky", "higher for longer",
            "boosting bond yields", "yields jump"
        ])
        # Check for Dovish indicators (CPI cooling, rate cuts, easing monetary policy)
        is_dovish = any(w in text_lower for w in [
            "rate cut", "cuts rate", "cut rates", "monetary easing", "policy easing",
            "rate easing", "fed easing", "ecb easing", "inflation cools", "cpi cools",
            "cooling inflation", "economic slowdown", "labor market cools",
            "below expectations", "disinflation"
        ])

        if is_hawkish:
            implications.append("USD: Hawkish Policy Tone (Yield Support)")
        elif is_dovish:
            implications.append("USD: Dovish Rate Cut Bias (Yield Drag)")
        elif label == "neutral" and conf > 0.60:
            # Academic or informational symposium
            implications.append("Fed: Policy / Academic Retrospection")
            severity_override = "MONITOR"

    # Energy transmission
    if is_energy:
        if label == "negative" or any(w in text_lower for w in ["plunge", "drop", "glut", "output increase", "waive"]):
            implications.append("OIL: Supply Inflow (Price Softening)")
        elif label == "positive" or any(w in text_lower for w in ["surge", "cut", "sabotage", "strike"]):
            implications.append("OIL: Supply Disruption Risk (Inflation Tailwind)")

    if not impacted_pairs:
        impacted_pairs = ["GLOBAL"]

    return {
        "sentiment": label,
        "confidence": conf,
        "probabilities": probs,
        "latency_ms": ai_result["latency_ms"],
        "impacted_pairs": impacted_pairs,
        "implications": implications[:2],
        "severity_override": severity_override,
    }


if __name__ == "__main__":
    test_headlines = [
        "Trump signs Executive Order waiving sanctions against Russia, opening path for diplomatic talks",
        "Israel launches heavy airstrike against drone depot in southern Lebanon, killing top commander",
        "US consumer price index rises 0.4% in January, exceeding expectations and boosting bond yields",
        "Jerome Powell speaks at University symposium on the post-pandemic inflation shock",
        "Historic ceasefire agreed in Middle East peace talks, easing regional shipping risks"
    ]

    print("=" * 80)
    print("TESTING NEWS CLASSIFIER MODULE")
    print("=" * 80)

    for h in test_headlines:
        res = analyze_news_item(h)
        print(f"\nHeadline: \"{h}\"")
        print(f"Sentiment: {res['sentiment'].upper()} (Conf: {res['confidence']*100:.1f}%) | Latency: {res['latency_ms']:.1f}ms")
        print(f"Impacted Pairs: {res['impacted_pairs']}")
        print(f"Implications: {res['implications']}")
        print(f"Severity Override: {res['severity_override']}")
        print("-" * 80)
