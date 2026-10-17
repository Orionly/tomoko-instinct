"""
Benchmark FinBERT ONNX for financial & geopolitical news sentiment accuracy.
Tests memory, speed, and prediction probabilities on critical headlines.
"""

import os
import sys
import time
import json
import psutil
import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer
from huggingface_hub import hf_hub_download

REPO_ID = "nicechester/finbert-sentiment-onnx-quantized"
MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "finbert_onnx")

os.makedirs(MODEL_DIR, exist_ok=True)

print("=" * 70)
print("FINBERT ONNX ACCURACY & PERFORMANCE BENCHMARK")
print("=" * 70)

# Step 1: Download required files if not present
files_to_download = [
    ("onnx/model_quantized.onnx", "model.onnx"),
    ("tokenizer.json", "tokenizer.json"),
    ("config.json", "config.json"),
]

for src_name, dst_name in files_to_download:
    dst_path = os.path.join(MODEL_DIR, dst_name)
    if not os.path.exists(dst_path):
        print(f"Downloading {src_name} -> {dst_name}...")
        downloaded = hf_hub_download(
            repo_id=REPO_ID,
            filename=src_name,
            local_dir=MODEL_DIR,
            local_dir_use_symlinks=False
        )
        if downloaded != dst_path and os.path.exists(downloaded):
            if os.path.exists(dst_path):
                os.remove(dst_path)
            os.replace(downloaded, dst_path)

model_path = os.path.join(MODEL_DIR, "model.onnx")
tokenizer_path = os.path.join(MODEL_DIR, "tokenizer.json")
config_path = os.path.join(MODEL_DIR, "config.json")

print(f"Model path: {model_path} ({os.path.getsize(model_path) / (1024*1024):.1f} MB)")
print(f"Tokenizer path: {tokenizer_path}")

# Step 2: Measure RAM before load
process = psutil.Process(os.getpid())
ram_before = process.memory_info().rss / (1024 * 1024)

# Step 3: Load Tokenizer & ONNX Runtime Session
t0 = time.perf_counter()
tokenizer = Tokenizer.from_file(tokenizer_path)
# Enable padding and truncation to max_length 128
tokenizer.enable_truncation(max_length=128)
tokenizer.enable_padding(length=128, pad_id=0, pad_token="[PAD]")

session_options = ort.SessionOptions()
session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
session_options.intra_op_num_threads = 4

session = ort.InferenceSession(model_path, session_options, providers=["CPUExecutionProvider"])
load_time = time.perf_counter() - t0

ram_after_load = process.memory_info().rss / (1024 * 1024)
ram_model_footprint = ram_after_load - ram_before

print(f"Model Load Time: {load_time:.2f} s")
print(f"RAM before load: {ram_before:.1f} MB | After load: {ram_after_load:.1f} MB (Delta: +{ram_model_footprint:.1f} MB)")
print("=" * 70)

# Step 4: Define Test Cases
test_cases = [
    {
        "category": "De-escalation / Sanctions Relief",
        "headline": "Trump signs Executive Order waiving sanctions against Russia, opening path for diplomatic talks",
        "expected": "Relief / Positive or Neutral (NOT Conflict Safe-Haven)"
    },
    {
        "category": "De-escalation / Sanctions Relief",
        "headline": "Trump pauses new Russia sanctions, citing national interest and potential peace negotiations",
        "expected": "Relief / Positive or Neutral"
    },
    {
        "category": "Diplomatic Ambiguity",
        "headline": "Russia celebrates Trump's sanctions waiver while Ukraine warns of dangerous precedent",
        "expected": "Neutral / Mixed"
    },
    {
        "category": "Active Military Escalation",
        "headline": "Israel launches heavy airstrike against drone depot in southern Lebanon, killing top commander",
        "expected": "Negative (Escalation / Risk-Off)"
    },
    {
        "category": "Ceasefire / Peace",
        "headline": "Historic ceasefire agreed in Middle East peace talks, easing regional tension and shipping risks",
        "expected": "Positive (De-escalation / Risk-On)"
    },
    {
        "category": "Central Bank Policy",
        "headline": "Fed Chair Kevin Warsh signals caution on further interest rate cuts as labor market remains resilient",
        "expected": "Neutral or Negative (Hawkish / Caution)"
    },
    {
        "category": "Historical Speech / Low Impact",
        "headline": "Jerome Powell speaks at University symposium on the post-pandemic inflation shock and lessons learned",
        "expected": "Neutral (Academic / Historical retrospection)"
    },
    {
        "category": "Macro Inflation Data",
        "headline": "US consumer price index rises 0.4% in January, exceeding expectations and boosting bond yields",
        "expected": "Negative / Neutral (Inflation pressure)"
    },
    {
        "category": "Commodity / Energy",
        "headline": "Crude oil plunges 4% as OPEC+ delegates discuss potential output increase for Q2",
        "expected": "Negative (for Oil) / Neutral"
    },
    {
        "category": "Monetary Shock",
        "headline": "Central bank unexpectedly hikes interest rates by 50 basis points to combat runaway inflation",
        "expected": "Negative (Hawkish shock / Monetary tightening)"
    }
]

# Labels according to FinBERT
labels = ["positive", "negative", "neutral"]

def softmax(x):
    e_x = np.exp(x - np.max(x, axis=-1, keepdims=True))
    return e_x / np.sum(e_x, axis=-1, keepdims=True)

# Step 5: Run Inference & Benchmark
results = []
inference_times = []

for idx, item in enumerate(test_cases, 1):
    headline = item["headline"]
    
    # Measure inference latency
    t_start = time.perf_counter()
    
    encoded = tokenizer.encode(headline)
    input_ids = np.array([encoded.ids], dtype=np.int64)
    attention_mask = np.array([encoded.attention_mask], dtype=np.int64)
    token_type_ids = np.array([encoded.type_ids], dtype=np.int64)
    
    # Check model inputs
    inputs = {
        "input_ids": input_ids,
        "attention_mask": attention_mask,
    }
    # Some ONNX exports also require token_type_ids
    input_names = [inp.name for inp in session.get_inputs()]
    if "token_type_ids" in input_names:
        inputs["token_type_ids"] = token_type_ids
        
    outputs = session.run(None, inputs)
    logits = outputs[0]
    probs = softmax(logits)[0]
    
    t_end = time.perf_counter()
    latency_ms = (t_end - t_start) * 1000.0
    inference_times.append(latency_ms)
    
    pred_idx = int(np.argmax(probs))
    pred_label = labels[pred_idx]
    confidence = float(probs[pred_idx] * 100.0)
    
    results.append({
        "index": idx,
        "category": item["category"],
        "headline": headline,
        "expected": item["expected"],
        "pred_label": pred_label,
        "confidence": confidence,
        "pos_prob": float(probs[0] * 100.0),
        "neg_prob": float(probs[1] * 100.0),
        "neu_prob": float(probs[2] * 100.0),
        "latency_ms": float(latency_ms)
    })

# Step 6: Print Detailed Results
print("\n" + "=" * 100)
print(f"{'#':<3} | {'Category':<28} | {'Prediction':<10} | {'Conf':<6} | {'Pos%':<5} | {'Neg%':<5} | {'Neu%':<5} | {'ms':<5}")
print("-" * 100)

for r in results:
    print(f"{r['index']:<3} | {r['category'][:28]:<28} | {r['pred_label'].upper():<10} | {r['confidence']:>5.1f}% | {r['pos_prob']:>4.1f}% | {r['neg_prob']:>4.1f}% | {r['neu_prob']:>4.1f}% | {r['latency_ms']:>5.1f}")
    print(f"    Headline: \"{r['headline']}\"")
    print(f"    Expected: {r['expected']}")
    print("-" * 100)

avg_latency = float(np.mean(inference_times))
median_latency = float(np.median(inference_times))
ram_final = float(process.memory_info().rss / (1024 * 1024))

print("\n" + "=" * 70)
print("PERFORMANCE SUMMARY:")
print(f"  Model Size on Disk : {os.path.getsize(model_path) / (1024*1024):.1f} MB")
print(f"  RAM Allocation     : +{ram_final - ram_before:.1f} MB")
print(f"  Avg Latency / item : {avg_latency:.2f} ms")
print(f"  Median Latency     : {median_latency:.2f} ms")
print(f"  Throughput (CPU)   : ~{1000.0 / avg_latency:.1f} headlines/sec")
print("=" * 70)

# Save JSON results for analysis
output_json = os.path.join(MODEL_DIR, "benchmark_results.json")
with open(output_json, "w") as f:
    json.dump({
        "model": REPO_ID,
        "model_size_mb": os.path.getsize(model_path) / (1024*1024),
        "ram_delta_mb": ram_final - ram_before,
        "avg_latency_ms": avg_latency,
        "median_latency_ms": median_latency,
        "results": results
    }, f, indent=2)
print(f"Results written to: {output_json}")
